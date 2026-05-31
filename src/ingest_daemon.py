#!/usr/bin/env python3
"""
ingest_daemon.py — 24x7 SCADA Ingest Daemon
============================================

Watches a network drive (or local mount point) for new or modified SCADA
export files and ingests them into StateBuffer (plant_state.sqlite) via
the full scada_mapper → StateBuffer pipeline.

Designed to be called by cron every N minutes. Idempotent: files already
ingested are skipped based on a lightweight ingest_log table in the DB.
If the network drive is unavailable, the script exits cleanly with a
logged warning — it does not crash or corrupt the database.

Usage
-----
  # Run once (called by cron):
  python src/ingest_daemon.py

  # Override defaults:
  python src/ingest_daemon.py --watch /Volumes/SCADA_Share/exports --pattern "*.csv"
  python src/ingest_daemon.py --watch /mnt/scada --pattern "biogas_*.xlsx" --lookback 120

Cron setup (every 5 minutes):
  */5 * * * * cd /path/to/project && /path/to/venv/bin/python src/ingest_daemon.py >> logs/ingest.log 2>&1

Environment / site_config.json
-------------------------------
All defaults can be overridden in site_config.json under "ingest_daemon":

  {
    "ingest_daemon": {
      "watch_dir":       "/Volumes/SCADA_Share/exports",
      "file_pattern":    "*.csv",
      "lookback_minutes": 15,
      "poll_subfolders": false,
      "scale_biogas_flow": true,
      "biogas_scale_factor": 1.0
    }
  }

Design notes
------------
- Uses ingest_log table in plant_state.sqlite to track processed files by
  (path, mtime, size). Re-ingests if the file is modified (mtime or size
  changes), so live-appended historian files are handled correctly.
- CUSUM filtering is applied by StateBuffer — spikes are silently discarded,
  raw payloads are preserved in raw_telemetry for audit.
- Thread-safe: SQLite WAL mode is set by StateBuffer. Multiple cron
  instances cannot corrupt the DB (SQLite serialises writes).
- Network drive offline: catches OSError/PermissionError and logs a warning.
  The MCP server continues serving the last good state from StateBuffer.
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Project root resolution (same pattern as seed_plant_state.py) ────────────
def _find_project_root() -> Path:
    anchors = ("pyproject.toml", "setup.py", "setup.cfg", ".git")
    start = Path(__file__).resolve().parent
    for directory in [start, *start.parents]:
        if any((directory / a).exists() for a in anchors):
            return directory
    return start.parent

PROJECT_ROOT = _find_project_root()

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ingest_daemon")

# ── Defaults (overridden by site_config.json or CLI args) ─────────────────────
DEFAULT_WATCH_DIR        = str(PROJECT_ROOT / "scada_exports")
DEFAULT_FILE_PATTERN     = "*.csv"
DEFAULT_LOOKBACK_MINUTES = 15   # Only scan files modified in last N minutes
DEFAULT_POLL_SUBFOLDERS  = False
# Scale factor for biogas_flow_nm3h if CSV data is not in Nm³/h units.
# Set to 1.0 if your SCADA already exports in Nm³/h.
# Set to 1/24_000_000 for lab-scale mL/day data (as in seed_plant_state.py).
DEFAULT_BIOGAS_SCALE     = 1.0


# ── Load site_config.json ─────────────────────────────────────────────────────
def load_site_config() -> dict:
    for path in [PROJECT_ROOT / "site_config.json",
                 PROJECT_ROOT / "src" / "site_config.json"]:
        if path.exists():
            return json.loads(path.read_text())
    return {}

SITE_CONFIG = load_site_config()
_DAEMON_CFG = SITE_CONFIG.get("ingest_daemon", {})


# ── Ingest log (tracks which files have been processed) ──────────────────────
import sqlite3

def _ensure_ingest_log(db_path: str):
    """Add ingest_log table to StateBuffer DB if not present."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingest_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path   TEXT NOT NULL,
            file_mtime  REAL NOT NULL,
            file_size   INTEGER NOT NULL,
            ingested_at TEXT NOT NULL,
            rows_accepted INTEGER,
            rows_rejected INTEGER,
            error_msg   TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ingest_log_path ON ingest_log(file_path)")
    conn.commit()
    conn.close()


def _already_ingested(db_path: str, file_path: str, mtime: float, size: int) -> bool:
    """Return True if this exact version of the file has been ingested."""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT id FROM ingest_log WHERE file_path=? AND file_mtime=? AND file_size=?",
        (file_path, mtime, size)
    ).fetchone()
    conn.close()
    return row is not None


def _log_ingest(db_path: str, file_path: str, mtime: float, size: int,
                rows_accepted: int, rows_rejected: int, error_msg: Optional[str] = None):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO ingest_log (file_path, file_mtime, file_size, ingested_at, "
        "rows_accepted, rows_rejected, error_msg) VALUES (?,?,?,?,?,?,?)",
        (file_path, mtime, size,
         datetime.now(timezone.utc).isoformat(),
         rows_accepted, rows_rejected, error_msg)
    )
    conn.commit()
    conn.close()


# ── File discovery ────────────────────────────────────────────────────────────
def find_new_files(
    watch_dir: Path,
    pattern: str,
    lookback_minutes: int,
    poll_subfolders: bool,
) -> List[Tuple[Path, float, int]]:
    """
    Return list of (path, mtime, size) for files matching pattern that were
    modified within the lookback window.
    """
    cutoff = time.time() - lookback_minutes * 60
    results = []

    try:
        glob_fn = watch_dir.rglob if poll_subfolders else watch_dir.glob
        for p in sorted(glob_fn(pattern)):
            try:
                stat = p.stat()
                if stat.st_mtime >= cutoff:
                    results.append((p, stat.st_mtime, stat.st_size))
            except OSError:
                continue
    except OSError as exc:
        logger.warning(f"Cannot access watch_dir {watch_dir}: {exc}")
        logger.warning("Network drive may be offline — skipping this cycle.")

    return results


# ── Core ingest function ──────────────────────────────────────────────────────
def ingest_file(
    file_path: Path,
    buf,                   # StateBuffer instance
    biogas_scale: float,
) -> Tuple[int, int]:
    """
    Run one file through scada_mapper → StateBuffer.
    Returns (rows_accepted, rows_rejected).
    """
    from scada_mapper import read_file, detect_scada_vendor, auto_map_dataframe
    import pandas as pd

    df_raw, fmt = read_file(file_path)
    logger.info(f"  Format: {fmt}, rows: {len(df_raw)}, cols: {len(df_raw.columns)}")

    vendor  = detect_scada_vendor(df_raw)
    mapping = auto_map_dataframe(df_raw, vendor)
    logger.info(f"  Vendor: {vendor}, mapped vars: {list(mapping.keys())}")

    if not mapping:
        logger.warning(f"  No columns could be mapped — skipping file.")
        return 0, len(df_raw)

    accepted = rejected = 0

    # Detect timestamp column (first column by convention, or named 'timestamp'/'date')
    ts_col = None
    for candidate in list(df_raw.columns[:3]):
        parsed = pd.to_datetime(df_raw[candidate], errors="coerce")
        if parsed.notna().sum() > len(df_raw) * 0.5:
            ts_col = candidate
            break

    for _, row in df_raw.iterrows():
        mapped_row: Dict[str, float] = {}

        for internal, actual_col in mapping.items():
            raw_val = row.get(actual_col)
            if raw_val is None or (isinstance(raw_val, float) and pd.isna(raw_val)):
                continue
            try:
                val = float(str(raw_val).replace("kg", "").strip())
                # Apply scale conversion if needed (e.g. mL/day → Nm³/h)
                if internal == "biogas_flow_nm3h" and biogas_scale != 1.0:
                    val = val * biogas_scale
                mapped_row[internal] = val
            except (ValueError, TypeError):
                continue

        if not mapped_row:
            rejected += 1
            continue

        # Use historical timestamp if available, otherwise use current time
        ts = None
        if ts_col is not None:
            try:
                ts = pd.to_datetime(row[ts_col]).timestamp()
            except Exception:
                pass

        buf.insert_live_data(mapped_row, timestamp=ts)
        accepted += 1

    return accepted, rejected


# ── Main entry point ──────────────────────────────────────────────────────────
def run(args):
    from StateBuffer import StateBuffer

    # ── Resolve paths ─────────────────────────────────────────────────────────
    watch_dir = Path(
        args.watch
        or _DAEMON_CFG.get("watch_dir", DEFAULT_WATCH_DIR)
    ).expanduser()

    pattern = (
        args.pattern
        or _DAEMON_CFG.get("file_pattern", DEFAULT_FILE_PATTERN)
    )

    lookback = int(
        args.lookback
        or _DAEMON_CFG.get("lookback_minutes", DEFAULT_LOOKBACK_MINUTES)
    )

    poll_subfolders = (
        args.subfolders
        or _DAEMON_CFG.get("poll_subfolders", DEFAULT_POLL_SUBFOLDERS)
    )

    biogas_scale = float(
        _DAEMON_CFG.get("biogas_scale_factor", DEFAULT_BIOGAS_SCALE)
    )

    buffer_db_path = str(
        Path(SITE_CONFIG.get("db_path", str(PROJECT_ROOT / "data" / "plant_state.sqlite")))
    )

    logger.info("=" * 60)
    logger.info("ingest_daemon starting")
    logger.info(f"  Watch dir   : {watch_dir}")
    logger.info(f"  Pattern     : {pattern}")
    logger.info(f"  Lookback    : {lookback} min")
    logger.info(f"  Subfolders  : {poll_subfolders}")
    logger.info(f"  Buffer DB   : {buffer_db_path}")
    logger.info(f"  Biogas scale: {biogas_scale}")

    # ── Verify watch_dir is accessible ────────────────────────────────────────
    if not watch_dir.exists():
        logger.warning(f"Watch dir not found: {watch_dir}")
        logger.warning("Network drive may be unmounted. Exiting cleanly.")
        sys.exit(0)  # Clean exit — cron will retry next interval

    # ── Initialise StateBuffer ─────────────────────────────────────────────────
    Path(buffer_db_path).parent.mkdir(parents=True, exist_ok=True)
    buf = StateBuffer(
        db_path=buffer_db_path,
        retention_hours=int(SITE_CONFIG.get("retention_hours", 48)),
        cusum_params=SITE_CONFIG.get("cusum_params"),
    )
    _ensure_ingest_log(buffer_db_path)

    # ── Discover and ingest files ─────────────────────────────────────────────
    files = find_new_files(watch_dir, pattern, lookback, poll_subfolders)

    if not files:
        logger.info(f"No files modified in last {lookback} min — nothing to do.")
        buf.close()
        return

    logger.info(f"Found {len(files)} candidate file(s).")
    total_accepted = total_rejected = 0

    for file_path, mtime, size in files:
        if _already_ingested(buffer_db_path, str(file_path), mtime, size):
            logger.info(f"  SKIP (already ingested): {file_path.name}")
            continue

        logger.info(f"  Ingesting: {file_path.name}")
        error_msg = None
        accepted = rejected = 0

        try:
            accepted, rejected = ingest_file(file_path, buf, biogas_scale)
            logger.info(f"    accepted={accepted}, rejected={rejected}")
        except Exception as exc:
            error_msg = str(exc)
            logger.error(f"    FAILED: {exc}")

        _log_ingest(buffer_db_path, str(file_path), mtime, size,
                    accepted, rejected, error_msg)
        total_accepted += accepted
        total_rejected += rejected

    # ── Post-ingest: run alert check ──────────────────────────────────────────
    latest = buf.get_latest()
    if latest:
        cusum = buf.get_cusum_status()
        near_threshold = [
            tag for tag, s in cusum.items()
            if s["H"] and (s["s_pos"] > s["H"] * 0.7 or s["s_neg"] > s["H"] * 0.7)
        ]
        if near_threshold:
            logger.warning(f"CUSUM near threshold for: {near_threshold}")

    logger.info(f"Cycle complete — accepted={total_accepted}, rejected={total_rejected}")
    buf.close()


def main():
    parser = argparse.ArgumentParser(
        description="24x7 SCADA ingest daemon (run via cron)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Cron examples (every 5 minutes):
  */5 * * * * cd /path/to/project && .venv/bin/python src/ingest_daemon.py >> logs/ingest.log 2>&1

Every 15 minutes with explicit watch dir:
  */15 * * * * cd /path/to/project && .venv/bin/python src/ingest_daemon.py --watch /Volumes/SCADA/exports --lookback 20 >> logs/ingest.log 2>&1

Check ingest log (shows what was processed):
  sqlite3 data/plant_state.sqlite "SELECT file_path, ingested_at, rows_accepted FROM ingest_log ORDER BY id DESC LIMIT 20;"
        """
    )
    parser.add_argument("--watch",     help="Network drive path to watch (dir)")
    parser.add_argument("--pattern",   help="Glob pattern, e.g. '*.csv', 'export_*.xlsx'")
    parser.add_argument("--lookback",  type=int, help="Only scan files modified in last N minutes")
    parser.add_argument("--subfolders", action="store_true", help="Recurse into subfolders")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Discover and map files but do not write to DB")
    args = parser.parse_args()

    if args.dry_run:
        logger.info("[DRY RUN] No writes will be made.")
        # Still resolve and show what would be ingested
        watch_dir = Path(args.watch or _DAEMON_CFG.get("watch_dir", DEFAULT_WATCH_DIR)).expanduser()
        pattern   = args.pattern or _DAEMON_CFG.get("file_pattern", DEFAULT_FILE_PATTERN)
        lookback  = int(args.lookback or _DAEMON_CFG.get("lookback_minutes", DEFAULT_LOOKBACK_MINUTES))
        files = find_new_files(watch_dir, pattern, lookback, args.subfolders)
        logger.info(f"Would process {len(files)} file(s):")
        for f, mtime, size in files:
            logger.info(f"  {f}  (mtime={datetime.fromtimestamp(mtime)}, size={size}b)")
        return

    run(args)


if __name__ == "__main__":
    main()
