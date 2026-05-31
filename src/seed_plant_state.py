"""
seed_plant_state.py — Seed MCP Server SQLite from Real Dataset
=============================================================
Full pipeline:
  ri_flex.csv
      ↓  scada_mapper.py     (fuzzy column mapping)
      ↓  StateBuffer.py      (CUSUM filtering → plant_state.sqlite)
      ↓  this script         (reads StateBuffer → seeds MCP server DB)

Usage
-----
  python src/seed_plant_state.py                        # uses .env defaults
  python src/seed_plant_state.py --period last30        # last 30 days (default)
  python src/seed_plant_state.py --period annual        # full-year averages
  python src/seed_plant_state.py --period peak          # Oct-Dec peak period
  python src/seed_plant_state.py --period winter        # Jan-Mar low period
  python src/seed_plant_state.py --digester-volume 1.2  # override volume (m3)
  python src/seed_plant_state.py --hrt-days 20          # override HRT
  python src/seed_plant_state.py --dry-run              # print only, no writes

Pipeline details
----------------
1. scada_mapper auto-detects column names in the CSV and maps them to
   internal variable names (digester_temp_c, biogas_flow_nm3h, etc.)
 2. Each row is pushed through StateBuffer via insert_live_data() which:
   - Stores the raw payload in raw_telemetry (audit trail)
   - Applies CUSUM anomaly detection (spikes discarded)
   - Writes clean values to plant_state table in plant_state.sqlite
3. This script reads back the clean StateBuffer data, computes period
   averages, and writes them into the MCP server SQLite DB.

Estimated digester geometry (R1-FLEX)
--------------------------------------
  Volume : ~1.0 m3  (from OLR assumption: 1.28 kg VS/day / 1.5 kg VS/m3/day)
  HRT    : ~17 days (feed vol ~50 L/day into ~850 L working volume)
  Override with --digester-volume and --hrt-days when actual values are known.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, UTC
from pathlib import Path

import pandas as pd

# ── Project root ──────────────────────────────────────────────────────────────
def _find_project_root() -> Path:
    anchors = ("pyproject.toml", "setup.py", "setup.cfg", ".git")
    start = Path(__file__).resolve().parent
    for directory in [start, *start.parents]:
        if any((directory / a).exists() for a in anchors):
            return directory
    for env_candidate in (start / ".env", start.parent / ".env"):
        if env_candidate.exists():
            for line in env_candidate.read_text().splitlines():
                line = line.strip()
                if line.startswith("PROJECT_ROOT="):
                    root = Path(line.split("=", 1)[1].strip())
                    if root.is_dir():
                        return root
    return start.parent

PROJECT_ROOT = _find_project_root()

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from scada_mapper import read_file, detect_scada_vendor, auto_map_dataframe
from StateBuffer import StateBuffer


# ── Load site_config.json ──────────────────────────────────────────────────
def load_site_config() -> dict:
    """Load site_config.json from project root or src/ directory."""
    candidates = [
        PROJECT_ROOT / "site_config.json",
        PROJECT_ROOT / "src" / "site_config.json",
    ]
    for path in candidates:
        if path.exists():
            return json.loads(path.read_text())
    return {}


SITE_CONFIG = load_site_config()
# Override digester defaults from site_config.json if present
DIGESTER_VOLUME_DEFAULT = SITE_CONFIG.get("estimated_geometry", {}).get("digester_volume_m3", 6.0)
HRT_DAYS_DEFAULT = SITE_CONFIG.get("estimated_geometry", {}).get("hrt_days", 17.0)

DEFAULT_CSV       = PROJECT_ROOT / "sample_data" / "ri_flex.csv"
DEFAULT_BUFFER_DB = PROJECT_ROOT / "data" / "plant_state.sqlite"
DEFAULT_MCP_DB    = PROJECT_ROOT / "data" / "biomethane.db"
CATTLE_SLURRY_VS_PCT = 6.4


# ── Period selection ──────────────────────────────────────────────────────────
def select_period(df: pd.DataFrame, date_col: str, period: str) -> pd.DataFrame:
    dates = pd.to_datetime(df[date_col], errors="coerce")
    if period == "last30":
        cutoff = dates.max() - pd.Timedelta(days=30)
        return df[dates >= cutoff]
    elif period == "annual":
        return df
    elif period == "peak":
        return df[dates.dt.month.isin([10, 11, 12])]
    elif period == "winter":
        return df[dates.dt.month.isin([1, 2, 3])]
    raise ValueError(f"Unknown period '{period}'. Choose: last30, annual, peak, winter")




# ── Pipeline: CSV → scada_mapper → StateBuffer ────────────────────────────────
def ingest_csv_to_buffer(csv_path: Path, buffer_db: Path,
                         period: str, dry_run: bool = False) -> StateBuffer:
    print(f"\n{'='*55}")
    print(f"  STEP 1 — SCADA column mapping")
    print(f"{'='*55}")

    df_raw, fmt = read_file(csv_path)
    print(f"  File   : {csv_path.name}  ({fmt}, {len(df_raw)} rows)")

    vendor  = detect_scada_vendor(df_raw)
    mapping = auto_map_dataframe(df_raw, vendor)
    print(f"  Vendor : {vendor}")
    print(f"  Mapped : {len(mapping)} variables")
    for internal, actual in mapping.items():
        print(f"    {internal:<32} <- '{actual}'")

    date_col   = df_raw.columns[0]
    df_period  = select_period(df_raw, date_col, period)
    dates      = pd.to_datetime(df_period[date_col], errors="coerce")
    print(f"\n  Period '{period}': {len(df_period)} rows "
          f"({dates.min().date()} -> {dates.max().date()})")

    print(f"\n{'='*55}")
    print(f"  STEP 2 — StateBuffer ingestion (CUSUM filtering)")
    print(f"{'='*55}")

    db_path = ":memory:" if dry_run else str(buffer_db)
    buf = StateBuffer(db_path=db_path, retention_hours=99999)

    accepted = rejected = 0

    for _, row in df_period.iterrows():
        mapped_row = {}
        for internal, actual_col in mapping.items():
            raw_val = row.get(actual_col)
            if raw_val is None or (isinstance(raw_val, float) and pd.isna(raw_val)):
                continue
            try:
                val = float(str(raw_val).replace("kg", "").strip())
                # SCALE CONVERSION: CSV data is lab-scale (~860 mL/day),
                # but MCP system expects industrial-scale (~142 Nm³/h).
                # Convert mL/day → Nm³/h: / 1,000,000 (mL→m³) / 24 (day→hr)
                if internal == "biogas_flow_nm3h":
                    val = val / 24_000_000
                mapped_row[internal] = val
            except (ValueError, TypeError):
                continue

        if not mapped_row:
            rejected += 1
            continue

        buf.insert_live_data(mapped_row)
        accepted += 1

    print(f"  Rows accepted   : {accepted}")
    print(f"  Rows skipped    : {rejected}  (no mappable values)")
    if dry_run:
        print("  [DRY RUN] Buffer is in-memory only.")

    return buf


# ── Compute seed values from clean StateBuffer data ───────────────────────────
def compute_seed_values(buf: StateBuffer, digester_volume_m3: float,
                        hrt_days: float) -> dict:
    df = buf.get_model_dataframe()
    if df.empty:
        raise RuntimeError("StateBuffer is empty — no valid rows after CUSUM filtering.")

    def mean_nn(col):
        if col in df.columns and df[col].notna().any():
            return float(df[col].mean())
        return None

    vs_kg_per_day = 20.0 * (CATTLE_SLURRY_VS_PCT / 100.0)  # 1.28 kg VS/day

    return {
        "digester_temp_c":    mean_nn("digester_temp_c"),
        # Already converted to Nm³/h by ingest_to_statebuffer()
        "biogas_flow_nm3h":   mean_nn("biogas_flow_nm3h"),
        "ch4_pct":            mean_nn("ch4_pct"),
        "vs_kg_per_day":      round(vs_kg_per_day, 3),
        "olr_kg_vs_m3_day":   round(vs_kg_per_day / digester_volume_m3, 3),
        "digester_volume_m3": digester_volume_m3,
        "hrt_days":           hrt_days,
        "n_rows_clean":       len(df),
    }


# ── Write to MCP server SQLite ────────────────────────────────────────────────
def seed_mcp_db(mcp_db: Path, seed: dict, dry_run: bool = False):
    print(f"\n{'='*55}")
    print(f"  STEP 3 — Seed MCP server DB")
    print(f"{'='*55}")
    print(f"  Target DB       : {mcp_db}")
    print(f"  Clean rows      : {seed['n_rows_clean']}")
    print()

    updates = {}
    for key in ("digester_temp_c", "biogas_flow_nm3h", "ch4_pct"):
        if seed.get(key) is not None:
            updates[key] = seed[key]
            print(f"  {key:<25} : {seed[key]}")
        else:
            print(f"  {key:<25} : (not in dataset — keeping MCP default)")

    print()
    print(f"  Estimated OLR   : {seed['olr_kg_vs_m3_day']} kg VS/m3/day")
    print(f"  Digester volume : {seed['digester_volume_m3']} m3"
          f"  {'(estimated)' if seed['digester_volume_m3'] == 1.0 else '(provided)'}")
    print(f"  HRT             : {seed['hrt_days']} days"
          f"  {'(estimated)' if seed['hrt_days'] == 17.0 else '(provided)'}")

    if dry_run:
        print("\n  [DRY RUN] No changes written.")
        return

    if not mcp_db.exists():
        print(f"\n  ERROR: MCP DB not found at {mcp_db}")
        print("  Start the MCP server once first to create it, then re-run.")
        sys.exit(1)

    conn = sqlite3.connect(mcp_db)
    cur  = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='plant_state'")
    if not cur.fetchone():
        print(f"  ERROR: plant_state table not found in {mcp_db}")
        conn.close()
        sys.exit(1)

    for key, value in updates.items():
        cur.execute(
            "UPDATE plant_state SET value=?, updated_at=? WHERE key=?",
            (str(value), datetime.now(UTC).isoformat(), key),
        )
        if cur.rowcount == 0:
            cur.execute(
                "INSERT INTO plant_state (key, value, updated_at) VALUES (?, ?, ?)",
                (key, str(value), datetime.now(UTC).isoformat()),
            )

    try:
        cur.execute("""CREATE TABLE IF NOT EXISTS seed_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seeded_at TEXT, reactor TEXT, period TEXT,
            n_rows INTEGER, olr REAL, notes TEXT)""")
        cur.execute(
            "INSERT INTO seed_log VALUES (NULL,?,?,?,?,?,?)",
             (datetime.now(UTC).isoformat(), "R1-FLEX", seed.get("period", "custom"),
             seed["n_rows_clean"], seed["olr_kg_vs_m3_day"],
             f"vol={seed['digester_volume_m3']}m3,hrt={seed['hrt_days']}d"),
        )
    except sqlite3.Error:
        pass

    conn.commit()
    conn.close()
    print(f"\n  Wrote {len(updates)} values to {mcp_db}")


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Seed MCP server from R1-FLEX CSV via scada_mapper + StateBuffer"
    )
    parser.add_argument("--csv",             type=Path, default=DEFAULT_CSV)
    parser.add_argument("--buffer-db",       type=Path, default=DEFAULT_BUFFER_DB)
    parser.add_argument("--mcp-db",          type=Path, default=DEFAULT_MCP_DB)
    parser.add_argument("--period",          default="last30",
                        choices=["last30", "annual", "peak", "winter"])
    parser.add_argument("--digester-volume", type=float, default=DIGESTER_VOLUME_DEFAULT,
                        help=f"Digester volume m3 (default: {DIGESTER_VOLUME_DEFAULT}, from site_config.json)")
    parser.add_argument("--hrt-days",        type=float, default=HRT_DAYS_DEFAULT,
                        help=f"HRT days (default: {HRT_DAYS_DEFAULT}, from site_config.json)")
    parser.add_argument("--dry-run",         action="store_true")
    args = parser.parse_args()

    if not args.csv.exists():
        print(f"ERROR: CSV not found at {args.csv}")
        sys.exit(1)

    args.buffer_db.parent.mkdir(parents=True, exist_ok=True)

    buf  = ingest_csv_to_buffer(args.csv, args.buffer_db, args.period, args.dry_run)
    seed = compute_seed_values(buf, args.digester_volume, args.hrt_days)
    seed["period"] = args.period
    seed_mcp_db(args.mcp_db, seed, args.dry_run)
    buf.close()

    print(f"\n{'='*55}")
    print(f"  Pipeline complete.")
    print(f"  Buffer DB : {args.buffer_db}")
    print(f"  MCP DB    : {args.mcp_db}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
