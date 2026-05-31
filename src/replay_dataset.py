"""
replay_dataset.py — Historical Data Replay Through scada_mapper + StateBuffer + MCP + EnKF
===========================================================================================
Full pipeline per day:
  ri_flex.csv row
      |
      v  scada_mapper        (column -> internal var name)
       v  StateBuffer.insert_live_data()  (CUSUM filter -> plant_state.sqlite)
      v  MCP update_plant_state  (clean state -> MCP server)
      v  MCP enkf_update         (EnKF state estimation)
      v  MCP check_alerts        (alert status)
      |
      v  replay_result.json  (per-day record + summary)

Usage
-----
  python src/replay_dataset.py                          # full year
  python src/replay_dataset.py --start 2024-06-01 --end 2024-09-30
  python src/replay_dataset.py --digester-volume 1.2 --hrt-days 20
  python src/replay_dataset.py --no-enkf               # skip EnKF (faster)
  python src/replay_dataset.py --dry-run               # no MCP/DB writes
  python src/replay_dataset.py --output reports/my_replay.json

What this validates
-------------------
- Does the EnKF track real biogas trends? (X2, S2 vs actual output)
- Does check_alerts fire on days with repair events or outliers?
- Does souring_probability rise before the Oct 15-19 sustained decline?
- Where does CUSUM correctly discard instrument errors (e.g. 0degC temp sensor)?
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
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

_venv_raw  = os.environ.get("VENV_PYTHON", ".venv/bin/python")
_venv_path = Path(_venv_raw) if Path(_venv_raw).is_absolute() else PROJECT_ROOT / _venv_raw
VENV_PYTHON = str(_venv_path) if _venv_path.exists() else sys.executable

MCP_SERVER_SCRIPT = os.environ.get(
    "MCP_SERVER_SCRIPT",
    str(PROJECT_ROOT / "src" / "bio_methane_operations_mcp_server_v5.py"),
)

sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from scada_mapper import read_file, detect_scada_vendor, auto_map_dataframe
from StateBuffer import StateBuffer

DEFAULT_CSV       = PROJECT_ROOT / "sample_data" / "ri_flex.csv"
DEFAULT_BUFFER_DB = PROJECT_ROOT / "data" / "replay_buffer.sqlite"
DEFAULT_OUTPUT    = PROJECT_ROOT / "reports" / "replay_result.json"
CATTLE_SLURRY_VS_PCT = 6.4


# ── MCP stdio client ──────────────────────────────────────────────────────────
class MCPClient:
    def __init__(self):
        self.proc    = None
        self.r_queue = queue.Queue()
        self._lock   = threading.Lock()
        self._req_id = 0

    def start(self, lab_scale: bool = False) -> bool:
        try:
            env = os.environ.copy()
            _venv_dir  = Path(VENV_PYTHON).parent.parent
            _site_pkgs = list(_venv_dir.glob("lib/python3.*/site-packages"))
            if _site_pkgs:
                env["PYTHONPATH"] = f"{_site_pkgs[0]}:{env.get('PYTHONPATH', '')}"

            cmd = [VENV_PYTHON, MCP_SERVER_SCRIPT]
            if lab_scale:
                cmd.append("--lab-scale")
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1, env=env,
            )
            # Read stderr in background to prevent blocking
            threading.Thread(target=self._stderr_reader, daemon=True).start()
            threading.Thread(target=self._read_loop, daemon=True).start()
            time.sleep(1.5)

            self._send_recv({"jsonrpc": "2.0", "method": "initialize",
                             "params": {"protocolVersion": "2024-11-05",
                                        "capabilities": {},
                                        "clientInfo": {"name": "replay", "version": "1.0"}},
                             "id": self._next_id()})
            return True
        except Exception as e:
            print(f"ERROR: MCP server failed to start: {e}")
            return False

    def stop(self):
        if self.proc:
            self.proc.terminate()

    def call(self, tool: str, args: dict, timeout: int = 30) -> dict:
        resp = self._send_recv({
            "jsonrpc": "2.0", "method": "tools/call",
            "params": {"name": tool, "arguments": args},
            "id": self._next_id(),
        }, timeout=timeout)
        if not resp:
            return {"error": "timeout"}
        if "error" in resp:
            error_obj = resp["error"]
            return {"error": f"JSON-RPC {error_obj.get('code')}: {error_obj.get('message')}"}
        content = resp.get("result", {}).get("content", [])
        # Handle both single JSON object and multiple text blocks (list of alerts)
        if len(content) == 1 and isinstance(content[0], dict):
            raw = content[0].get("text", "")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
        # Try to parse each text block as JSON (for check_alerts returning list)
        results = []
        for c in content:
            if isinstance(c, dict) and "text" in c:
                try:
                    results.append(json.loads(c["text"]))
                except json.JSONDecodeError:
                    results.append(c["text"])
        if len(results) == 1:
            return results[0]
        return results

    def _next_id(self):
        self._req_id += 1
        return self._req_id

    def _read_loop(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                try:
                    self.r_queue.put(json.loads(line))
                except json.JSONDecodeError:
                    pass

    def _stderr_reader(self):
        """Read stderr in background to prevent blocking."""
        for line in self.proc.stderr:
            # Log to file instead of stdout to avoid cluttering replay output
            pass

    def _send(self, payload):
        with self._lock:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()

    def _recv(self, timeout=30):
        try:
            return self.r_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _send_recv(self, payload, timeout=30):
        self._send(payload)
        return self._recv(timeout=timeout)


# ── Data loading ──────────────────────────────────────────────────────────────
def load_and_map(csv_path: Path):
    """Load CSV and return (df, mapping, date_col)."""
    df, fmt = read_file(csv_path)
    vendor  = detect_scada_vendor(df)
    mapping = auto_map_dataframe(df, vendor)
    date_col = df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")

    # Flag outlier rows (kept in replay, logged as anomaly)
    if "biogas_flow_nm3h" in mapping:
        biogas_col = mapping["biogas_flow_nm3h"]
        df["biogas_raw"] = pd.to_numeric(df[biogas_col], errors="coerce")
    else:
        df["biogas_raw"] = None

    if "digester_temp_c" in mapping:
        temp_col = mapping["digester_temp_c"]
        df["temp_raw"] = pd.to_numeric(df[temp_col], errors="coerce")
    else:
        df["temp_raw"] = None

    df["is_outlier"] = (
        df["biogas_raw"].lt(0).fillna(False) |
        df["biogas_raw"].gt(5000).fillna(False) |
        df["temp_raw"].lt(5).fillna(False) |
        df["temp_raw"].gt(50).fillna(False)
    )

    comment_col = "Comment" if "Comment" in df.columns else None
    df["comment"] = df[comment_col].astype(str).str.strip() if comment_col else ""

    df = df.sort_values(date_col).reset_index(drop=True)
    return df, mapping, date_col


# ── Per-day replay ────────────────────────────────────────────────────────────
def replay_day(row, mapping: dict, buf: StateBuffer,
               mcp: MCPClient, enkf_initialised: bool,
               args) -> tuple[dict, bool]:
    """
    One day of the replay pipeline:
    1. Map row -> already-mapped dict
    2. StateBuffer.insert_live_data() (CUSUM filter)
    3. Read back latest clean state from StateBuffer
    4. MCP update_plant_state with clean values
    5. MCP enkf_initialise (first day only)
    6. MCP enkf_update
    7. MCP check_alerts
    """
    t0 = time.time()
    # Handle both pandas Timestamp and string date formats
    raw_date = row.get("date", "")
    if hasattr(raw_date, 'date'):
        date_str = str(raw_date.date())
    else:
        date_str = str(raw_date).split()[0]  # Strip time portion

    result = {
        "date":              date_str,
        "actual_biogas_mL":  None if pd.isna(row.get("biogas_raw", float("nan"))) else float(row["biogas_raw"]),
        "actual_temp_c":     None if pd.isna(row.get("temp_raw", float("nan"))) else float(row["temp_raw"]),
        "is_outlier":        bool(row.get("is_outlier", False)),
        "comment":           row["comment"] if (
            row["comment"] not in ("", "nan") and 
            not (isinstance(row["comment"], float) and pd.isna(row["comment"]))
        ) else None,
        "cusum_passed":      {},
        "enkf_S2_mean":      None,
        "enkf_X2_mean":      None,
        "enkf_souring_prob": None,
        "enkf_risk_level":   None,
        "alert_status":      None,
        "errors":            [],
        "elapsed_s":         0.0,
    }

    if args.dry_run:
        result["elapsed_s"] = round(time.time() - t0, 3)
        return result, enkf_initialised

    # ── 1. Build already-mapped row dict ──────────────────────────────────────
    t1 = time.time()
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
    map_time = time.time() - t1

    # ── 2. StateBuffer.insert_live_data() (CUSUM) ────────────────────────────────
    t2 = time.time()
    if mapped_row:
        # Use historical date from CSV for proper timestamp
        if "date" in row and row["date"]:
            try:
                hist_ts = pd.Timestamp(str(row["date"])).timestamp()
                buf.insert_live_data(mapped_row, timestamp=hist_ts)
            except Exception:
                buf.insert_live_data(mapped_row)  # Fallback to current time
        else:
            buf.insert_live_data(mapped_row)
    cusum_time = time.time() - t2

    # ── 3. Read back latest clean state from StateBuffer ─────────────────────
    t3 = time.time()
    df_buf = buf.get_model_dataframe()
    read_time = time.time() - t3
    if df_buf.empty:
        result["errors"].append("StateBuffer empty after insert")
        result["elapsed_s"] = round(time.time() - t0, 3)
        return result, enkf_initialised

    latest = df_buf.iloc[-1]
    latest_dict = latest.to_dict()
    result["cusum_passed"] = {
        k: round(float(v), 4) for k, v in latest_dict.items() if pd.notna(v)
    }

    # ── 4. MCP update_plant_state ─────────────────────────────────────────────
    mcp_updates = {}
    if pd.notna(latest_dict.get("digester_temp_c")):
        mcp_updates["digester_temp_c"] = float(latest_dict["digester_temp_c"])
    if pd.notna(latest_dict.get("biogas_flow_nm3h")):
        # Already converted to Nm³/h by replay_day() before StateBuffer insert
        mcp_updates["biogas_flow_nm3h"] = float(latest_dict["biogas_flow_nm3h"])
    if pd.notna(latest_dict.get("ch4_pct")):
        mcp_updates["ch4_pct"] = float(latest_dict["ch4_pct"])

    if mcp_updates:
        upd = mcp.call("update_plant_state", {"updates": mcp_updates})
        if "error" in upd:
            result["errors"].append(f"update_plant_state: {upd['error']}")

    # ── 5. MCP check_alerts ───────────────────────────────────────────────────
    alert = mcp.call("check_alerts", {})
    if isinstance(alert, list):
        # check_alerts returns a list of alert dicts with "severity" field
        if alert:
            severities = [a.get("severity", "") for a in alert]
            if any(s in ("HIGH", "CRITICAL") for s in severities):
                result["alert_status"] = "CRITICAL"
            elif any(s == "MEDIUM" for s in severities):
                result["alert_status"] = "WARNING"
            else:
                result["alert_status"] = "INFO"
            result["alert_details"] = alert
        else:
            result["alert_status"] = "OK"
    elif isinstance(alert, dict) and "error" in alert:
        result["errors"].append(f"check_alerts: {alert['error']}")

    # ── 6. MCP EnKF ──────────────────────────────────────────────────────────
    if not args.no_enkf:
        if not enkf_initialised:
            init = mcp.call("enkf_initialise", {
                "digester_volume_m3": args.digester_volume,
                "hrt_days":           args.hrt_days,
                "s1_in_g_per_l":      15.0,
                "n_ensemble":         100,
                "t_ref_celsius":      35.0,
            })
            if "error" not in init:
                enkf_initialised = True
                print(f"\n    EnKF init OK: vol={args.digester_volume}m3 "
                      f"hrt={args.hrt_days}d", end="")
            else:
                err_msg = init.get("error", str(init))
                # If ad4_enkf.py is missing, disable EnKF for all remaining days
                # rather than retrying and logging an error every single day.
                if "not available" in err_msg.lower():
                    print(f"\n    EnKF unavailable: {err_msg} — disabling for this run",
                          end="")
                    args.no_enkf = True
                else:
                    result["errors"].append(f"enkf_initialise: {err_msg}")
                    print(f"\n    EnKF FAILED: {err_msg}", end="")

        if enkf_initialised:
            # enkf_update reads biogas_flow_nm3h, ch4_pct, digester_temp_c
            # automatically from plant_state. Only pass optional lab values.
            enkf_args = {}
            if pd.notna(latest_dict.get("fos_mg_per_l")):
                enkf_args["fos_mg_per_l"] = float(latest_dict["fos_mg_per_l"])
            enkf = mcp.call("enkf_update", enkf_args)
            if "S2_mean" in enkf:
                result["enkf_S2_mean"]      = round(enkf["S2_mean"], 3)
                result["enkf_X2_mean"]      = round(enkf.get("X2_mean", 0), 4)
                result["enkf_souring_prob"] = round(enkf.get("souring_probability", 0), 3)
                result["enkf_risk_level"]   = enkf.get("risk_level")
            elif "error" in enkf:
                result["errors"].append(f"enkf_update: {enkf.get('error')}")

    result["elapsed_s"] = round(time.time() - t0, 3)
    return result, enkf_initialised


# ── Summary ───────────────────────────────────────────────────────────────────
def compute_summary(records: list) -> dict:
    valid         = [r for r in records if not r["is_outlier"] and r["actual_biogas_mL"]]
    alert_days    = [r for r in records if r["alert_status"] in ("WARNING", "CRITICAL")]
    souring_days  = [r for r in records if (r["enkf_souring_prob"] or 0) > 0.3]
    repair_days   = [r for r in records if r["comment"] and
                     isinstance(r["comment"], str) and
                     any(w in r["comment"].lower() for w in ["repair", "repar"])]
    outlier_days  = [r for r in records if r["is_outlier"]]
    actual_vals   = [r["actual_biogas_mL"] for r in valid]

    return {
        "days_processed":       len(records),
        "days_clean":           len(valid),
        "days_outlier":         len(outlier_days),
        "days_with_alerts":     len(alert_days),
        "souring_events":       len(souring_days),
        "repair_events_in_log": len(repair_days),
        "biogas_mean_mL":       round(sum(actual_vals) / len(actual_vals), 1) if actual_vals else None,
        "biogas_min_mL":        min(actual_vals) if actual_vals else None,
        "biogas_max_mL":        max(actual_vals) if actual_vals else None,
        "alert_dates":          [r["date"] for r in alert_days],
        "souring_dates":        [r["date"] for r in souring_days],
        "repair_dates":         [r["date"] for r in repair_days],
        "outlier_dates":        [r["date"] for r in outlier_days],
    }


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Replay R1-FLEX dataset through scada_mapper + StateBuffer + MCP + EnKF"
    )
    parser.add_argument("--csv",             type=Path, default=DEFAULT_CSV)
    parser.add_argument("--buffer-db",       type=Path, default=DEFAULT_BUFFER_DB)
    parser.add_argument("--output",          type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--start",           type=str,  default=None,
                        help="Start date YYYY-MM-DD")
    parser.add_argument("--end",             type=str,  default=None,
                        help="End date YYYY-MM-DD")
    parser.add_argument("--digester-volume", type=float, default=1.0,
                        help="Digester volume m3 (default: 1.0, estimated)")
    parser.add_argument("--hrt-days",        type=float, default=17.0,
                        help="HRT days (default: 17, estimated)")
    parser.add_argument("--no-enkf",         action="store_true",
                        help="Skip EnKF updates (faster)")
    parser.add_argument("--dry-run",         action="store_true",
                        help="No MCP calls or DB writes")
    parser.add_argument("--lab-scale",       action="store_true",
                        help="Use lab-scale temp thresholds (20-30°C)")
    args = parser.parse_args()

    # Pass lab-scale flag to MCP server
    if args.lab_scale:
        print("WARNING: Using lab-scale temperature thresholds (20-30°C)")

    if not args.csv.exists():
        print(f"ERROR: CSV not found at {args.csv}")
        sys.exit(1)

    # ── Load and map ──────────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"  SCADA column mapping")
    print(f"{'='*55}")
    df, mapping, date_col = load_and_map(args.csv)
    print(f"  File    : {args.csv.name}  ({len(df)} rows)")
    print(f"  Mapped  : {len(mapping)} variables")
    for internal, actual in mapping.items():
        print(f"    {internal:<32} <- '{actual}'")

    if args.start:
        df = df[df[date_col] >= args.start]
    if args.end:
        df = df[df[date_col] <= args.end]

    df = df.set_index(date_col)

    vs_kg_day = 20.0 * (CATTLE_SLURRY_VS_PCT / 100.0)
    olr = round(vs_kg_day / args.digester_volume, 3)

    print(f"\n  Replay span     : {len(df)} days  "
          f"({df.index.min().date()} -> {df.index.max().date()})")
    print(f"  Outlier days    : {df['is_outlier'].sum()} (kept, flagged in output)")
    print(f"  Digester volume : {args.digester_volume} m3"
          f"  {'(estimated)' if args.digester_volume == 1.0 else '(provided)'}")
    print(f"  HRT             : {args.hrt_days} days"
          f"  {'(estimated)' if args.hrt_days == 17.0 else '(provided)'}")
    print(f"  Estimated OLR   : {olr} kg VS/m3/day")

    if args.dry_run:
        print("\n[DRY RUN] Column mapping shown above. No MCP calls will be made.")
        return

    # ── StateBuffer (persistent across replay) ────────────────────────────────
    args.buffer_db.parent.mkdir(parents=True, exist_ok=True)
    # Use 48h retention (default) so replay with historical timestamps purges properly
    buf = StateBuffer(db_path=str(args.buffer_db), retention_hours=48)

    # ── MCP server ────────────────────────────────────────────────────────────
    mcp = MCPClient()
    print(f"\n{'='*55}")
    print(f"  Starting MCP server...")
    print(f"{'='*55}")
    if not mcp.start():
        buf.close()
        sys.exit(1)
    print("  MCP server ready.\n")

    # ── Day-by-day replay loop ────────────────────────────────────────────────
    records = []
    enkf_initialised = False

    for i, (idx, row) in enumerate(df.iterrows()):
        date_str = str(idx.date()) if hasattr(idx, "date") else str(row.get("date", ""))
        biogas   = row.get("biogas_raw", float("nan"))
        temp     = row.get("temp_raw", float("nan"))
        comment  = row.get("comment", "")

        biogas_str = f"{biogas:.0f}mL" if pd.notna(biogas) else "N/A"
        temp_str   = f"{temp:.1f}C"    if pd.notna(temp)   else "N/A"
        outlier_tag = "  OUTLIER" if row.get("is_outlier") else ""
        
        comment_is_empty = (
            pd.isna(comment) or
            comment == "" or
            str(comment).lower() == "nan"
        )
        comment_tag = f"  [{comment}]" if not comment_is_empty else ""

        print(f"  [{i+1:3d}/{len(df)}] {date_str}  "
              f"biogas={biogas_str}  temp={temp_str}"
              f"{outlier_tag}{comment_tag}", end="  ")

        row_dict = row.to_dict()
        row_dict["date"] = date_str

        record, enkf_initialised = replay_day(
            row_dict, mapping, buf, mcp, enkf_initialised, args
        )
        records.append(record)

        parts = []
        if record["alert_status"]:
            parts.append(record["alert_status"])
        if record["enkf_risk_level"]:
            parts.append(f"EnKF:{record['enkf_risk_level']}")
        if record["errors"]:
            parts.append(f"ERR:{len(record['errors'])}")
        print(" | ".join(parts) if parts else "OK")

    mcp.stop()
    buf.close()

    # ── Write output ──────────────────────────────────────────────────────────
    summary = compute_summary(records)
    output  = {
        "meta": {
            "reactor":       "R1-FLEX",
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "csv_source":    str(args.csv),
            "buffer_db":     str(args.buffer_db),
            "days":          len(records),
            "digester_volume_m3": args.digester_volume,
            "hrt_days":      args.hrt_days,
            "olr_estimated": olr,
        },
        "summary": summary,
        "records": records,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2))

    print(f"\n{'='*55}")
    print(f"  Days processed  : {summary['days_processed']}")
    print(f"  Outlier days    : {summary['days_outlier']}")
    print(f"  Alert days      : {summary['days_with_alerts']}")
    print(f"  Souring events  : {summary['souring_events']}")
    print(f"  Repair events   : {summary['repair_events_in_log']}")
    if summary["biogas_mean_mL"]:
        print(f"  Mean biogas     : {summary['biogas_mean_mL']:.0f} mL/day")
    print(f"\n  Report -> {args.output}")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
