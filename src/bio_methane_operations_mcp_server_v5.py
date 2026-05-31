"""
Biomethane Operations MCP Server - v5

Focuses on:
A. Rule-based alerts (threshold checks, multi-parameter logic)
B. Feedstock blending (stoichiometric calculations, C/N advisory)
C. KPI rollups (daily / weekly / monthly summaries from historian)
D. Lookup & advisory (reference tables, operational guidance)
E. Calibration (Buswell BMP, C/N, OLR - physics-based equations)
F. AD4 simulation (steady-state, critical D, perturbation recovery)
G. EnKF state estimation (ensemble Kalman filter for S2/X2)

Storage — two-tier:
- StateBuffer (data/plant_state.sqlite):  primary store for live/replayed sensor
  data.  All SCADA readings pass through StateBuffer.insert_live_data() which
  applies CUSUM anomaly detection before persisting.  get_plant_state() reads
  the latest clean row from StateBuffer.  update_plant_state() writes through
  StateBuffer so manual LLM updates also go through CUSUM.
- biomethane.db (data/biomethane.db):  legacy key-value store retained for
  default seeding.

Integration:
- scada_mapper.py   maps SCADA CSV column names to internal variable names
- StateBuffer.py    CUSUM-filtered SQLite buffer (single source of truth)
- seed_plant_state.py  seeds StateBuffer from historical CSV (Layer 2)
- replay_dataset.py    day-by-day historical replay (Layer 3)
- site_config.json  per-site CUSUM tuning and DB path

Usage:
  pip install mcp
  python src/bio_methane_operations_mcp_server_v5.py          # stdio
  python src/bio_methane_operations_mcp_server_v5.py --http   # HTTP
  python src/bio_methane_operations_mcp_server_v5.py --site site_config.json
"""

import random
import math
import threading
import argparse
import sqlite3
import sys
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from mcp.server.fastmcp import FastMCP

# ── Project root (anchor-based — works regardless of cwd) ────────────────────
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

# Add src/ to path
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

mcp = FastMCP("biomethane-ops")

# ── Global flags ──────────────────────────────────────────────────────────────
_is_lab_scale = False  # Global flag: True if operating with lab-scale data (20-30°C temps)

# ── Default alert thresholds ──────────────────────────────────────────────────
# These are used if alert_thresholds is not specified in site_config.json
# Option A behavior: explicit config values in site_config override these defaults
#
# Two profiles: "lab_scale" and "industrial"
# _is_lab_scale determines which profile is active
#
# Key differences:
# - Temperature: lab=20-30°C, industrial=35-40°C
# - VFA: lab uses Benyahia model (30/80), industrial uses conservative (8/15)
# - NH4, H2S, pH: same for both profiles

DEFAULT_ALERT_THRESHOLDS = {
    "lab_scale": {
        "digester_temp_c": {"low": 20, "high": 30},
        "vfa_mmol_l": {"medium": 30, "high": 80},
        "nh4_mg_l": {"medium": 300, "high": 800},
        "h2s_ppm": {"medium": 200, "high": 500},
        "digester_ph": {"low": 6.8, "high": 7.8},
        "biomethane_purity_pct": {"min": 95},
        "o2_ppm": {"max": 500}
    },
    "industrial": {
        "digester_temp_c": {"low": 35, "high": 40},
        "vfa_mmol_l": {"medium": 8, "high": 15},
        "nh4_mg_l": {"medium": 300, "high": 800},
        "h2s_ppm": {"medium": 200, "high": 500},
        "digester_ph": {"low": 6.8, "high": 7.8},
        "biomethane_purity_pct": {"min": 95},
        "o2_ppm": {"max": 500}
    }
}

# ── Daily assessment thresholds ───────────────────────────────────────────────
# Used by the background assessment thread started at server startup.
# Override any of these in site_config.json under "daily_assessment".
#
# Literature grounding:
#   souring_probability_watch:  0.10 — early-warning level consistent with
#       Bernard (2001) souring onset; produces an advisory log entry only.
#   souring_probability_action: 0.30 — elevated risk aligned with Benyahia (2012)
#       S2 accumulation onset; triggers ad4_perturbation_test to quantify headroom.
#   s2_rising_days: 2 — consecutive daily S2_mean increases before trend is
#       flagged; single-day increases may be sensor noise.
#   run_hour_utc: 6 — assessment fires at 06:00 UTC (morning briefing window).
DEFAULT_DAILY_ASSESSMENT = {
    "souring_probability_watch":  0.10,
    "souring_probability_action": 0.30,
    "s2_rising_days":             2,
    "run_hour_utc":               6,
}

# ── StateBuffer integration (primary source of truth for plant state) ─────────
try:
    from StateBuffer import StateBuffer
    _HAS_STATE_BUFFER = True
except ImportError:
    _HAS_STATE_BUFFER = False

# ── Legacy SQLite (biomethane.db) — used for seeding defaults only ────────────
DB_PATH = PROJECT_ROOT / "data" / "biomethane.db"

_state_lock = threading.Lock()
_alert_log_lock = threading.Lock()

# ── StateBuffer startup ───────────────────────────────────────────────────────
import json as _json
import os as _os

def _load_site_config(config_path: Optional[Path] = None) -> dict:
    """Load site_config.json if present. Returns empty dict if absent."""
    candidates = [
        config_path,
        PROJECT_ROOT / "site_config.json",
        PROJECT_ROOT / "src" / "site_config.json",
    ]
    for p in candidates:
        if p and Path(p).exists():
            return _json.loads(Path(p).read_text())
    return {}

def _init_state_buffer(site_config: dict, lab_scale: bool = False) -> Optional["StateBuffer"]:
    """
    Initialise StateBuffer from site_config or PROJECT_ROOT defaults.
    Returns None (with a warning) if StateBuffer is unavailable.
    
    Args:
        site_config: Configuration dict from site_config.json
        lab_scale: If True, use lab-scale CUSUM parameters (scaled for ~1.0 m³ digester)
    """
    if not _HAS_STATE_BUFFER:
        print("WARNING: StateBuffer not available — falling back to legacy biomethane.db",
              file=sys.stderr, flush=True)
        return None
    db_path = site_config.get("db_path")
    if db_path and not Path(db_path).is_absolute():
        db_path = str(PROJECT_ROOT / db_path)
    else:
        db_path = str(PROJECT_ROOT / "data" / "plant_state.sqlite")
    retention = site_config.get("retention_hours", 48)
    cusum     = site_config.get("cusum_params")
    lab_scale_flag = lab_scale or site_config.get("lab_scale", False)
    buf = StateBuffer(db_path=db_path, retention_hours=retention,
                      cusum_params=cusum, lab_scale=lab_scale_flag)
    scale_msg = " (lab-scale CUSUM)" if lab_scale_flag else ""
    print(f"StateBuffer ready — {db_path}{scale_msg}", file=sys.stderr, flush=True)
    return buf

_site_config   = _load_site_config()
_state_buffer  = _init_state_buffer(_site_config)   # None if unavailable

def _get_db_connection():
    """Get legacy SQLite connection with Row factory."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _init_plant_state_from_db():
    """Load plant_state from legacy biomethane.db (fallback / seeding only)."""
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM plant_state")
        rows = cursor.fetchall()
        conn.close()
        if rows:
            return {row["key"]: row["value"] for row in rows}
    except Exception:
        pass
    return None

# Module-scope defaults — used as fallback if DB is empty or unavailable
PLANT_STATE_DEFAULTS = {
    "digester_temp_c":        37.2,
    "digester_ph":             7.1,
    "vfa_mmol_l":              8.5,
    "alkalinity_mg_caco3_l": 2800.0,
    "nh4_mg_l":               420.0,
    "biogas_flow_nm3h":       142.0,
    "ch4_pct":                 62.3,
    "co2_pct":                 36.1,
    "h2s_ppm":                380.0,
    "o2_ppm":                 180.0,
    "biomethane_purity_pct":   97.4,
    "grid_injection_nm3h":     98.0,
    "organic_load_kg_vs_d":    2.8,
    "hydraulic_retention_days": 22.0,
    "digestate_ts_pct":         3.2,
}

def _ensure_plant_state_in_db():
    """Ensure legacy biomethane.db plant_state table exists with defaults."""
    try:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='plant_state'"
        )
        if not cursor.fetchone():
            cursor.execute("""
                CREATE TABLE plant_state (
                    key TEXT PRIMARY KEY,
                    value REAL,
                    updated_at TEXT
                )
            """)
        cursor.execute("SELECT COUNT(*) FROM plant_state")
        if cursor.fetchone()[0] == 0:
            now = datetime.now().isoformat()
            for key, value in PLANT_STATE_DEFAULTS.items():
                cursor.execute(
                    "INSERT INTO plant_state (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, value, now),
                )
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"WARNING: legacy DB init failed: {e}", file=sys.stderr, flush=True)

# Initialise legacy DB on module load
_ensure_plant_state_in_db()

# Fallback in-memory state (used only when both StateBuffer and legacy DB are down)
_plant_state = _init_plant_state_from_db() or dict(PLANT_STATE_DEFAULTS)

_alert_log = deque(maxlen=1000)

# ── Layer 3: AD4 simulator (optional — graceful degradation if absent) ────────
try:
    from ad4_simulator import AD4Simulator, AD4Params, AD4State
    _ad4_params = AD4Params()           # Benyahia defaults; replace after calibration
    _ad4_sim    = AD4Simulator(params=_ad4_params)
    _AD4_AVAILABLE = True
except ImportError:
    _AD4_AVAILABLE = False

# ── Layer 3b: EnKF state estimator (requires ad4_enkf.py on path) ─────────────
try:
    from ad4_enkf import AD4EnKFServer, EnKFConfig, Observation
    _enkf_server = AD4EnKFServer(
        digester_volume_m3=2000.0,      # override at runtime via enkf_initialise()
        config=EnKFConfig(n_ensemble=100),
    )
    _ENKF_AVAILABLE = True
except ImportError:
    _ENKF_AVAILABLE = False


_FEEDSTOCKS = {
    "Cattle slurry": {"bmp": 200, "cn": 11, "dm": 8, "vs_dm": 0.80},
    "Pig manure": {"bmp": 310, "cn": 8, "dm": 6, "vs_dm": 0.78},
    "Maize silage": {"bmp": 340, "cn": 25, "dm": 33, "vs_dm": 0.94},
    "Food waste": {"bmp": 480, "cn": 16, "dm": 25, "vs_dm": 0.88},
    "Grass silage": {"bmp": 290, "cn": 18, "dm": 30, "vs_dm": 0.90},
    "Sewage sludge": {"bmp": 220, "cn": 9, "dm": 4, "vs_dm": 0.75},
    "Chicken manure": {"bmp": 350, "cn": 7, "dm": 25, "vs_dm": 0.76},
    "Fat/grease trap": {"bmp": 900, "cn": 30, "dm": 80, "vs_dm": 0.97},
}

_OPERATIONAL_REFERENCE = {
    "fos_tac": {
        "exact": "FOS/TAC ratio measures buffering capacity vs volatile fatty acids. Target 0.3-0.4 for stable mesophilic digestion. >0.6 indicates risk of acidification.",
        "alias": ["fos tac", "acidification", "vfa/alkalinity"],
    },
    "temperature": {
        "exact": "Mesophilic optimal: 35-38C. Thermophilic: 50-55C. Temperature swings >1C/day stress microbes.",
        "alias": ["temp", "digester temp", "operating temperature"],
    },
    "olr": {
        "exact": "Organic Loading Rate. Mesophilic: 1.5-3.5 kg VS/m3/d. >4.5 risks acidification.",
        "alias": ["loading rate", "vs load"],
    },
    "cn_ratio": {
        "exact": "C/N ratio target 20-30. <15 risks ammonia inhibition. >35 slows degradation.",
        "alias": ["carbon nitrogen", "c/n"],
    },
}


@mcp.tool()
def get_plant_state() -> dict:
    """
    Returns the current plant operating state.

    Priority:
      1. StateBuffer.get_latest()  — latest CUSUM-filtered sensor reading
      2. Legacy biomethane.db      — key-value fallback
      3. In-memory PLANT_STATE_DEFAULTS
    """
    with _state_lock:
        # 1. StateBuffer (primary)
        if _state_buffer is not None:
            latest = _state_buffer.get_latest()
            if latest:
                # Merge with legacy state so all keys are present
                base = dict(_plant_state)
                base.update({k: v for k, v in latest.items() if v is not None})
                return base

        # 2. Legacy DB fallback
        try:
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM plant_state")
            rows = cursor.fetchall()
            conn.close()
            if rows:
                return {row["key"]: row["value"] for row in rows}
        except Exception:
            pass

        # 3. In-memory fallback
        return dict(_plant_state)


@mcp.tool()
def update_plant_state(updates: dict) -> dict:
    """
    Update plant state values.

    Writes through StateBuffer (CUSUM-filtered) when available,
    then mirrors accepted values to the legacy biomethane.db so
    all read paths see consistent data.
    """
    accepted = []
    rejected = []

    # Determine which keys are writable (must exist in PLANT_STATE_DEFAULTS
    # or in the StateBuffer schema columns)
    from StateBuffer import PLANT_STATE_COLS as _SB_COLS
    writable_keys = set(PLANT_STATE_DEFAULTS.keys()) | set(_SB_COLS)

    with _state_lock:
        # Validate and split updates
        valid: dict = {}
        for key, value in updates.items():
            if key not in writable_keys:
                rejected.append({"parameter": key, "reason": "Unknown parameter"})
            elif not isinstance(value, (int, float)):
                rejected.append({"parameter": key, "reason": "Must be numeric"})
            else:
                valid[key] = value

        # Write through StateBuffer (CUSUM will filter spikes)
        if _state_buffer is not None and valid:
            _state_buffer.insert_live_data(valid)

        # Mirror to legacy DB
        try:
            conn = _get_db_connection()
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            for key, value in valid.items():
                cursor.execute(
                    "UPDATE plant_state SET value = ?, updated_at = ? WHERE key = ?",
                    (value, now, key),
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        "INSERT INTO plant_state (key, value, updated_at) VALUES (?,?,?)",
                        (key, value, now),
                    )
            conn.commit()
            cursor.execute("SELECT key, value FROM plant_state")
            updated_state = {row["key"]: row["value"] for row in cursor.fetchall()}
            conn.close()
        except Exception:
            updated_state = dict(_plant_state)
            updated_state.update(valid)

        accepted = list(valid.keys())
        return {
            "updated":        accepted,
            "rejected":       rejected,
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "state":          updated_state,
            "via_state_buffer": _state_buffer is not None,
        }


def _get_alert_thresholds() -> dict:
    """Get alert thresholds based on _is_lab_scale flag.
    
    Reads from site_config.alert_thresholds if present, falls back to DEFAULT_ALERT_THRESHOLDS.
    Option A behavior: explicit config values override defaults for any specified keys.
    
    Returns:
        Dictionary of thresholds for the active profile (lab_scale or industrial)
    """
    profile = "lab_scale" if _is_lab_scale else "industrial"
    defaults = DEFAULT_ALERT_THRESHOLDS[profile]
    
    config_thresholds = _site_config.get("alert_thresholds", {})
    profile_config = config_thresholds.get(profile, {})
    
    result = {}
    for key, default_val in defaults.items():
        if key in profile_config:
            if isinstance(default_val, dict) and isinstance(profile_config[key], dict):
                result[key] = {**default_val, **profile_config[key]}
            else:
                result[key] = profile_config[key]
        else:
            result[key] = default_val
    
    return result


@mcp.tool()
def check_alerts() -> list:
    """Check all parameters against thresholds and return active alerts.
    
    Thresholds are determined by:
    1. _is_lab_scale flag (selects lab_scale or industrial profile)
    2. site_config.alert_thresholds (if present, Option A: explicit config overrides defaults)
    3. DEFAULT_ALERT_THRESHOLDS (fallback if no config)
    
    Key differences between profiles:
    - Temperature: lab=20-30°C, industrial=35-40°C
    - VFA: lab uses Benyahia model (30/80), industrial uses conservative (8/15)
    """
    alerts = []

    # Read current state from SQLite
    with _state_lock:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM plant_state")
        rows = cursor.fetchall()
        conn.close()
        current_state = {row["key"]: row["value"] for row in rows}

        temp = current_state.get("digester_temp_c")
        ph = current_state.get("digester_ph")
        vfa = current_state.get("vfa_mmol_l")
        nh4 = current_state.get("nh4_mg_l")
        h2s = current_state.get("h2s_ppm")
        purity = current_state.get("biomethane_purity_pct")
        o2 = current_state.get("o2_ppm")

    # Get thresholds from config (or defaults) based on _is_lab_scale
    thresholds = _get_alert_thresholds()
    profile_name = "lab-scale" if _is_lab_scale else "industrial"

    # Temperature check
    temp_cfg = thresholds.get("digester_temp_c", {"low": 35, "high": 40})
    temp_low, temp_high = temp_cfg["low"], temp_cfg["high"]
    temp_range_str = f"{temp_low}-{temp_high}°C ({profile_name})"

    if temp < temp_low or temp > temp_high:
        alerts.append(
            {
                "parameter": "digester_temp_c",
                "severity": "HIGH",
                "message": f"Temperature {temp}C outside {temp_range_str} range",
            }
        )

    # pH check
    ph_cfg = thresholds.get("digester_ph", {"low": 6.8, "high": 7.8})
    ph_low, ph_high = ph_cfg["low"], ph_cfg["high"]
    if ph < ph_low or ph > ph_high:
        alerts.append(
            {
                "parameter": "digester_ph",
                "severity": "HIGH",
                "message": f"pH {ph} outside optimal {ph_low}-{ph_high} range",
            }
        )

    # VFA check
    vfa_cfg = thresholds.get("vfa_mmol_l", {"medium": 8, "high": 15})
    vfa_medium, vfa_high = vfa_cfg["medium"], vfa_cfg["high"]
    if vfa > vfa_medium:
        severity = "HIGH" if vfa > vfa_high else "MEDIUM"
        alerts.append(
            {
                "parameter": "vfa_mmol_l",
                "severity": severity,
                "message": f"VFA {vfa} mmol/L - acidification risk (threshold: {vfa_medium})",
            }
        )

    # NH4 check
    nh4_cfg = thresholds.get("nh4_mg_l", {"medium": 300, "high": 800})
    nh4_medium, nh4_high = nh4_cfg["medium"], nh4_cfg["high"]
    if nh4 > nh4_medium:
        severity = "HIGH" if nh4 > nh4_high else "MEDIUM"
        alerts.append(
            {
                "parameter": "nh4_mg_l",
                "severity": severity,
                "message": f"Ammonium-N {nh4} mg/L - inhibition risk (threshold: {nh4_medium})",
            }
        )

    # H2S check
    h2s_cfg = thresholds.get("h2s_ppm", {"medium": 200, "high": 500})
    h2s_medium, h2s_high = h2s_cfg["medium"], h2s_cfg["high"]
    if h2s > h2s_medium:
        severity = "HIGH" if h2s > h2s_high else "MEDIUM"
        h2s_desc = "toxicity" if h2s > h2s_high else "elevated"
        alerts.append(
            {
                "parameter": "h2s_ppm",
                "severity": severity,
                "message": f"H2S {h2s} ppm - {h2s_desc} (threshold: {h2s_medium})",
            }
        )

    # Purity check
    purity_cfg = thresholds.get("biomethane_purity_pct", {"min": 95})
    purity_min = purity_cfg["min"]
    if purity < purity_min:
        alerts.append(
            {
                "parameter": "biomethane_purity_pct",
                "severity": "HIGH",
                "message": f"Purity {purity}% below grid spec ({purity_min}%)",
            }
        )

    # O2 check
    o2_cfg = thresholds.get("o2_ppm", {"max": 500})
    o2_max = o2_cfg["max"]
    if o2 > o2_max:
        alerts.append(
            {
                "parameter": "o2_ppm",
                "severity": "HIGH",
                "message": f"O2 {o2} ppm - air ingress detected (threshold: {o2_max})",
            }
        )

    with _alert_log_lock:
        _alert_log.extend(alerts)

    return alerts


@mcp.tool()
def get_vfa_alkalinity_ratio() -> dict:
    """Calculate VFA to alkalinity ratio and assess acidification risk.
    
    Returns fos tac ratio (VFA/alkalinity), status, and advice.
    The ratio indicates process stability - optimal range is 0.1-0.3.
    Ratio > 0.3 indicates risk of acidification.
    """
    with _state_lock:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM plant_state WHERE key IN ('vfa_mmol_l', 'alkalinity_mg_caco3_l')")
        rows = cursor.fetchall()
        conn.close()
        state = {row["key"]: row["value"] for row in rows}
    
    vfa = state.get("vfa_mmol_l", 0)
    alkalinity = state.get("alkalinity_mg_caco3_l", 1)
    
    # Calculate ratio (VFA in mmol/L, alkalinity in mg/L CaCO3)
    # Convert: 1 mmol/L VFA ≈ 1 meq/L, 1 meq/L alkalinity ≈ 50 mg/L CaCO3
    # So ratio = (VFA * 1000) / (alkalinity / 50) = VFA * 50 / alkalinity
    # Simplified: fos_tac = VFA_mM / (Alkalinity_mg_L / 50)
    fos_tac_ratio = (vfa * 50) / alkalinity if alkalinity > 0 else 0
    
    # Assess risk
    if fos_tac_ratio < 0.1:
        status = "LOW"
        advice = "Ratio is low - process is stable but may be underloaded"
    elif fos_tac_ratio <= 0.3:
        status = "OPTIMAL"
        advice = "Process is in healthy range"
    elif fos_tac_ratio <= 0.5:
        status = "HIGH"
        advice = "Ratio elevated - monitor closely for acidification risk"
    else:
        status = "CRITICAL"
        advice = "HIGH acidification risk - take immediate action"
    
    return {
        "fos_tac_ratio": round(fos_tac_ratio, 3),
        "vfa_mmol_l": vfa,
        "alkalinity_mg_caco3_l": alkalinity,
        "status": status,
        "advice": advice,
    }


@mcp.tool()
def get_alert_history(limit: int = 10) -> dict:
    """Get recent alert history log.

    Args:
        limit: Maximum number of alerts to return (default 10)

    Returns:
        Dictionary with total_alerts_in_log, returned count, and records list.
    """
    with _alert_log_lock:
        total = len(_alert_log)
        records = list(_alert_log)[-limit:] if limit > 0 else list(_alert_log)

    return {
        "total_alerts_in_log": total,
        "returned": len(records),
        "records": records,
    }


@mcp.tool()
def blend_feedstocks(recipe: list[dict], target_olr: float = 2.5) -> dict:
    """Calculate blend of feedstocks to achieve target OLR.

    Args:
        recipe: List of feedstock items with "name" and "wet_tonnes"
        target_olr: Target organic loading rate (default 2.5 kg VS/m3/d)

    Returns:
        Dictionary with blend calculations including CH4 yield and C/N ratio.
    """
    with _state_lock:
        # Read current ch4_pct from SQLite
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM plant_state WHERE key = 'ch4_pct'")
        row = cursor.fetchone()
        conn.close()
        ch4_pct = row["value"] if row else 60.0
        ch4_fraction = ch4_pct / 100.0

    total_vs = 0
    streams = []
    for item in recipe:
        name = item.get("name", "")
        wet_t = item.get("wet_tonnes", 0)
        if name not in _FEEDSTOCKS:
            continue
        f = _FEEDSTOCKS[name]
        vs = wet_t * 1000 * (f["dm"] / 100) * f["vs_dm"]
        total_vs += vs
        streams.append(
            {"name": name, "wet_tonnes": wet_t, "vs_kg": round(vs, 1), "bmp": f["bmp"]}
        )

    if total_vs == 0:
        return {"error": "No valid feedstocks in recipe"}

    estimated_ch4 = (
        sum(s["vs_kg"] * s["bmp"] for s in streams) / total_vs * ch4_fraction
    )

    cn_ratio = (
        sum(s["vs_kg"] * _FEEDSTOCKS[s["name"]]["cn"] for s in streams) / total_vs
        if total_vs > 0
        else 0
    )

    # Daily biomethane production
    # estimated_ch4 is NL CH4 / kg VS (already scaled by ch4_fraction)
    # total_vs is in kg → total CH4 NL → convert to Nm3 (/1000)
    LHV_KWH_PER_NM3 = 10.55  # kWh per Nm3 biomethane at 97.4% purity
    biomethane_nm3 = (total_vs * estimated_ch4) / 1000.0
    biomethane_mwh = round(biomethane_nm3 * LHV_KWH_PER_NM3 / 1000.0, 3)

    # Inhibition warnings based on feedstock composition
    inhibition_warnings = []
    feedstock_names = {s["name"] for s in streams}
    if "Food waste" in feedstock_names:
        inhibition_warnings.append(
            "Food waste contributes high H2S risk — monitor H2S ppm and consider iron dosing"
        )
    if "Chicken manure" in feedstock_names or "Pig manure" in feedstock_names:
        inhibition_warnings.append(
            "High-nitrogen feedstock present — monitor NH4 for ammonia inhibition risk"
        )
    if cn_ratio < 15:
        inhibition_warnings.append(
            f"Blend C/N ratio {round(cn_ratio,1)} is below 15 — high ammonia inhibition risk"
        )
    if cn_ratio > 35:
        inhibition_warnings.append(
            f"Blend C/N ratio {round(cn_ratio,1)} exceeds 35 — slow degradation expected"
        )

    return {
        "streams": streams,
        "total_vs_kg": round(total_vs, 1),
        "estimated_ch4_yield_nl_kg_vs": round(estimated_ch4, 1),
        "blend_cn_ratio": round(cn_ratio, 1),
        "ch4_fraction_used": round(ch4_fraction, 4),
        "inhibition_warnings": inhibition_warnings,
        "estimated_daily_output": {
            "biomethane_nm3": round(biomethane_nm3, 1),
            "biomethane_mwh": biomethane_mwh,
        },
        "status": "OK" if 20 <= cn_ratio <= 30 else "ADJUST_CN",
    }


@mcp.tool()
def get_operational_reference(topic: str) -> dict:
    """Look up operational guidance. Topics: fos_tac, temperature, olr, cn_ratio, etc."""
    topic_lower = topic.lower().strip()

    for key, data in _OPERATIONAL_REFERENCE.items():
        if key == topic_lower or topic_lower in data.get("alias", []):
            return {"topic": key, "guidance": data["exact"]}

        for alias in data.get("alias", []):
            if alias in topic_lower or topic_lower in alias:
                return {"topic": key, "guidance": data["exact"]}

    return {"topic": topic, "guidance": "No reference found for this topic"}


@mcp.tool()
def get_kpi_summary(period: str = "daily") -> dict:
    """Get KPI summary: daily, weekly, or monthly."""
    # Read from SQLite
    with _state_lock:
        conn = _get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM plant_state WHERE key IN ('grid_injection_nm3h', 'biomethane_purity_pct')")
        rows = cursor.fetchall()
        conn.close()
        state = {row["key"]: row["value"] for row in rows}
        base_flow = state.get("grid_injection_nm3h", 98)
        purity = state.get("biomethane_purity_pct", 97.4)
    
    uptime = random.uniform(0.92, 0.99)

    if period == "daily":
        production_nm3 = base_flow * 24 * uptime
    elif period == "weekly":
        production_nm3 = base_flow * 24 * 7 * uptime
    else:
        production_nm3 = base_flow * 24 * 30 * uptime

    energy_kwh = production_nm3 * 10.55 * (purity / 100)

    return {
        "period": period,
        "date": datetime.now().isoformat(),
        "grid_injection_nm3": round(production_nm3, 1),
        "energy_kwh": round(energy_kwh, 1),
        "avg_purity_pct": purity,
        "uptime_pct": round(uptime * 100, 1),
    }


@mcp.tool()
def list_feedstocks() -> dict:
    """List available feedstocks with their BMP, C/N, DM, and VS/DM values."""
    return {"feedstocks": _FEEDSTOCKS}


@mcp.tool()
def buswell_bmp(c: float, h: float, o: float, n: float = 0.0, s: float = 0.0) -> dict:
    """
    Calculate theoretical BMP from elemental composition using Buswell equation.

    Args:
        c: Carbon moles (e.g. 6 for glucose C6H12O6)
        h: Hydrogen moles
        o: Oxygen moles
        n: Nitrogen moles (default 0)
        s: Sulfur moles (default 0)

    Returns:
        Dict with CH4/CO2 moles, fractions, and BMP.
        Reference: Buswell & Mueller (1952), Ind. Eng. Chem. 44(3), p.550
    """
    ch4_mol = c / 2 + h / 8 - o / 4 - 3 * n / 8 - s / 4
    co2_mol = c / 2 - h / 8 + o / 4 + 3 * n / 8 + s / 4
    h2o_mol = c - h / 4 - o / 2 + 3 * n / 4 + s / 2

    ch4_mol = max(0.0, ch4_mol)
    co2_mol = max(0.0, co2_mol)

    total_gas_mol = ch4_mol + co2_mol
    ch4_pct = (ch4_mol / total_gas_mol * 100) if total_gas_mol > 0 else 0.0
    co2_pct = (co2_mol / total_gas_mol * 100) if total_gas_mol > 0 else 0.0

    NTP_L_PER_MOL = 24.04
    ch4_nl_per_mol = ch4_mol * NTP_L_PER_MOL

    return {
        "substrate_formula": f"C{c}H{h}O{o}N{n}S{s}",
        "ch4_mol_per_mol_substrate": round(ch4_mol, 4),
        "co2_mol_per_mol_substrate": round(co2_mol, 4),
        "h2o_consumed_mol": round(h2o_mol, 4),
        "ch4_nl_per_mol_substrate": round(ch4_nl_per_mol, 2),
        "ch4_fraction_pct": round(ch4_pct, 1),
        "co2_fraction_pct": round(co2_pct, 1),
    }


_SUBSTRATE_FORMULAS = {
    "carbohydrate_cellulose": {"c": 6, "h": 10, "o": 5, "n": 0, "s": 0, "mw": 162.14},
    "carbohydrate_glucose": {"c": 6, "h": 12, "o": 6, "n": 0, "s": 0, "mw": 180.16},
    "protein_generic": {"c": 5, "h": 7, "o": 2, "n": 1, "s": 0, "mw": 113.12},
    "lipid_tripalmitin": {"c": 51, "h": 98, "o": 6, "n": 0, "s": 0, "mw": 807.32},
    "lipid_triolein": {"c": 57, "h": 104, "o": 6, "n": 0, "s": 0, "mw": 885.43},
}


@mcp.tool()
def buswell_bmp_by_class(substrate_class: str) -> dict:
    """
    Calculate Buswell BMP for named substrate class.

    Args:
        substrate_class: One of carbohydrate_cellulose, carbohydrate_glucose,
                      protein_generic, lipid_tripalmitin, lipid_triolein

    Returns:
        Dict with BMP in NL CH4/g VS and NL CH4/kg VS.
    """
    if substrate_class not in _SUBSTRATE_FORMULAS:
        return {
            "error": f"Unknown class '{substrate_class}'.",
            "available": list(_SUBSTRATE_FORMULAS.keys()),
        }

    f = _SUBSTRATE_FORMULAS[substrate_class]
    result = buswell_bmp(c=f["c"], h=f["h"], o=f["o"], n=f["n"], s=f["s"])
    mw = f["mw"]

    bmp_nl_g_vs = result["ch4_nl_per_mol_substrate"] / mw

    result["substrate_class"] = substrate_class
    result["molecular_weight"] = mw
    result["bmp_nl_per_g_vs"] = round(bmp_nl_g_vs, 1)
    result["bmp_nl_per_kg_vs"] = round(bmp_nl_g_vs * 1000, 0)
    return result


@mcp.tool()
def calculate_energy_conversion_factor(ch4_fraction: float = 0.974) -> dict:
    """
    Derive kWh/Nm3 conversion factor from CH4 fraction.

    Args:
        ch4_fraction: CH4 mole fraction (default 0.974 = 97.4%)

    Returns:
        Dict with conversion factor and derivation.
        Reference: Perry & Green (2008), Table 2-150. LHV CH4 = 35.88 MJ/Nm3.
    """
    LHV_CH4_MJ_PER_NM3_STP = 35.88
    MJ_TO_KWH = 1 / 3.6
    STP_TO_NTP = 273.15 / 288.15

    LHV_CH4_MJ_PER_NM3_NTP = LHV_CH4_MJ_PER_NM3_STP / STP_TO_NTP
    LHV_biomethane_MJ = LHV_CH4_MJ_PER_NM3_NTP * ch4_fraction
    LHV_biomethane_kWh = LHV_biomethane_MJ * MJ_TO_KWH

    SCRIPT_VALUE = 10.55

    return {
        "ch4_fraction_input": round(ch4_fraction, 4),
        "LHV_pure_CH4_MJ_per_Nm3_STP": LHV_CH4_MJ_PER_NM3_STP,
        "STP_to_NTP_correction": round(STP_TO_NTP, 4),
        "LHV_pure_CH4_MJ_per_Nm3_NTP": round(LHV_CH4_MJ_PER_NM3_NTP, 3),
        "LHV_biomethane_MJ_per_Nm3": round(LHV_biomethane_MJ, 3),
        "LHV_biomethane_kWh_per_Nm3": round(LHV_biomethane_kWh, 3),
        "script_current_value_kWh_per_Nm3": SCRIPT_VALUE,
        "difference_pct": round(
            (LHV_biomethane_kWh - SCRIPT_VALUE) / SCRIPT_VALUE * 100, 2
        ),
        "recommendation": (
            f"For {ch4_fraction*100:.1f}% CH4, use {round(LHV_biomethane_kWh, 3)} kWh/Nm3."
        ),
    }


@mcp.tool()
def cn_ratio_from_composition(carbon_pct_of_vs: float, nitrogen_pct_of_vs: float) -> dict:
    """
    Calculate C/N ratio from elemental analysis.

    Args:
        carbon_pct_of_vs: % carbon by mass of volatile solids (e.g. 44.0)
        nitrogen_pct_of_vs: % nitrogen by mass of VS (e.g. 1.8)

    Returns:
        Dict with C/N ratio and operational advisory.
        Reference: Drosg (2013), IEA Bioenergy Task 37.
    """
    if nitrogen_pct_of_vs <= 0:
        return {"error": "Nitrogen fraction must be > 0."}

    cn = carbon_pct_of_vs / nitrogen_pct_of_vs

    if 20 <= cn <= 30:
        status = "OPTIMAL"
        advice = "C/N ratio in ideal range (20-30). Good stability expected."
    elif cn < 20:
        status = "LOW — ammonia inhibition risk"
        advice = "Add carbon-rich co-substrate (maize silage, grass)."
    elif cn <= 35:
        status = "HIGH — slow degradation"
        advice = "Add nitrogen-rich co-substrate (manure, slurry)."
    else:
        status = "VERY HIGH — significant degradation limitation"
        advice = "Substrate is heavily lignocellulosic. Pre-treatment required."

    return {
        "carbon_pct_vs": round(carbon_pct_of_vs, 2),
        "nitrogen_pct_vs": round(nitrogen_pct_of_vs, 2),
        "cn_ratio": round(cn, 1),
        "status": status,
        "advice": advice,
    }


@mcp.tool()
def olr_from_recipe(
    recipe: list[dict],
    digester_volume_m3: float,
) -> dict:
    """
    Calculate OLR from feedstock recipe.

    Args:
        recipe: List of dicts with "name" and "wet_tonnes"
        digester_volume_m3: Working volume of digester

    Returns:
        Dict with OLR (kg VS/m3/day) and status.
        Reference: Weiland (2010), optimal 2-4 kg VS/m3/day.
    """
    streams, errors = [], []
    for r in recipe:
        name = r.get("name", "")
        if name not in _FEEDSTOCKS:
            errors.append(f"'{name}' not in feedstock table.")
            continue
        f = _FEEDSTOCKS[name]
        wet_kg = r["wet_tonnes"] * 1000
        dm_kg = wet_kg * (f["dm"] / 100)
        vs_kg = dm_kg * f["vs_dm"]
        streams.append({"name": name, "vs_kg_per_day": round(vs_kg, 1)})

    if errors:
        return {"errors": errors}

    total_vs = sum(s["vs_kg_per_day"] for s in streams)
    olr = total_vs / digester_volume_m3

    if olr < 1.5:
        status = "UNDERLOADED"
        advice = "OLR low. Increase feedstock throughput."
    elif olr <= 3.5:
        status = "OPTIMAL"
        advice = "OLR in ideal mesophilic range (1.5-3.5)."
    elif olr <= 4.5:
        status = "HIGH"
        advice = "Approaching upper limit. Monitor VFA daily."
    else:
        status = "OVERLOADED — acidification risk"
        advice = "Reduce feeding immediately. OLR exceeds 4.5."

    return {
        "recipe_streams": streams,
        "total_vs_kg_per_day": round(total_vs, 1),
        "digester_volume_m3": digester_volume_m3,
        "olr_kg_vs_m3_day": round(olr, 3),
        "status": status,
        "advice": advice,
    }


@mcp.tool()
def biodegradability_coefficient(
    substrate_class: str,
    empirical_bmp_nl_per_kg_vs: float,
) -> dict:
    """
    Calculate biodegradability coefficient = empirical / theoretical.

    Args:
        substrate_class: One of carbohydrate_cellulose, etc.
        empirical_bmp_nl_per_kg_vs: Measured BMP from lab or literature

    Returns:
        Dict with coefficient and assessment.
        Reference: Angelidaki et al. (2009), Water Sci. Tech. 59(5), 927-934.
    """
    theoretical = buswell_bmp_by_class(substrate_class)
    if "error" in theoretical:
        return theoretical

    theo_bmp = theoretical["bmp_nl_per_kg_vs"]
    eta = empirical_bmp_nl_per_kg_vs / theo_bmp

    if eta > 1.0:
        flag = "IMPOSSIBLE — empirical > theoretical"
    elif eta >= 0.80:
        flag = "GOOD — highly degradable substrate"
    elif eta >= 0.50:
        flag = "MODERATE — partially recalcitrant"
    else:
        flag = "LOW — recalcitrant substrate"

    return {
        "substrate_class": substrate_class,
        "theoretical_bmp_nl_per_kg_vs": theo_bmp,
        "empirical_bmp_nl_per_kg_vs": empirical_bmp_nl_per_kg_vs,
        "biodegradability_coefficient_eta": round(eta, 3),
        "flag": flag,
    }



# ══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — AD4 Simulation Tools
# Requires: ad4_simulator.py on the Python path
# Falls back gracefully with {"error": "AD4 simulator not available"} if absent
# ══════════════════════════════════════════════════════════════════════════════

def _ad4_unavailable() -> dict:
    return {
        "error": "AD4 simulator not available",
        "hint": "Ensure ad4_simulator.py is on the Python path alongside this server.",
    }


@mcp.tool()
def ad4_simulate(
    dilution_rate: float,
    influent_cod_g_per_l: float,
    days: float = 100.0,
    digester_temp_c: Optional[float] = None,
) -> dict:
    """
    Run the 4-state AM2 digestion simulation at steady operating conditions.

    Simulates the CSTR digester ODE system (Bernard 2001) over the given
    time horizon and returns the final steady-state values plus health flags.
    mu2_max is corrected for temperature via Arrhenius (theta=1.035).

    Args:
        dilution_rate:        D in d⁻¹. HRT = 1/D. Typical farm scale: 0.04-0.07.
        influent_cod_g_per_l: S1_in — influent COD in g/L. Typical: 15-50.
        days:                 Simulation horizon in days. Use ≥100 for steady state.
        digester_temp_c:      Digester temperature (°C). If None, reads from
                              plant_state. Used for Arrhenius mu2_max correction.

    Returns:
        Dict with steady-state S1, S2, X1, X2, methane flow rate,
        washout flag, souring flag, healthy flag, temperature correction applied,
        and effective mu2_max used.
    """
    if not _AD4_AVAILABLE:
        return _ad4_unavailable()

    if dilution_rate <= 0 or dilution_rate > 2.0:
        return {"error": f"dilution_rate must be in (0, 2.0], got {dilution_rate}"}
    if influent_cod_g_per_l <= 0 or influent_cod_g_per_l > 200:
        return {"error": f"influent_cod_g_per_l must be in (0, 200], got {influent_cod_g_per_l}"}
    if days < 10 or days > 1000:
        return {"error": f"days must be in [10, 1000], got {days}"}

    try:
        # Read temperature from plant_state if not provided
        if digester_temp_c is None:
            with _state_lock:
                conn = _get_db_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM plant_state WHERE key = ?",
                               ("digester_temp_c",))
                row = cursor.fetchone()
                conn.close()
            digester_temp_c = float(row["value"]) if row else 35.0

        # Temperature-corrected mu2_max via Arrhenius
        mu2_max_eff = _ad4_params.mu2_max_at_temp(digester_temp_c)
        temp_corrected = abs(digester_temp_c - 35.0) > 0.1

        # Build temperature-corrected params and simulator
        from ad4_simulator import AD4Params, AD4Simulator
        t_params = AD4Params(
            mu1_max=_ad4_params.mu1_max, Ks1=_ad4_params.Ks1,
            k1=_ad4_params.k1,           k2=_ad4_params.k2,
            mu2_max=mu2_max_eff,          Ks2=_ad4_params.Ks2,
            Ki2=_ad4_params.Ki2,          k3=_ad4_params.k3,
            alpha=_ad4_params.alpha,      k6=_ad4_params.k6,
        )
        t_sim = AD4Simulator(params=t_params)

        result  = t_sim.run(days=days, D=dilution_rate,
                            S1_in=influent_cod_g_per_l)
        summary = result.summary()

        ss = summary["steady_state"]
        summary["interpretation"] = {
            "S2_status": (
                "HEALTHY"   if ss["S2_mmol_per_L"] < 30  else
                "WATCH"     if ss["S2_mmol_per_L"] < 80  else
                "WARNING"   if ss["S2_mmol_per_L"] < 150 else
                "CRITICAL"
            ),
            "X2_status": (
                "ROBUST"       if ss["X2_g_per_L"] > 2.0 else
                "NORMAL"       if ss["X2_g_per_L"] > 1.0 else
                "LOW"          if ss["X2_g_per_L"] > 0.1 else
                "NEAR_WASHOUT" if ss["X2_g_per_L"] > 0.05 else
                "WASHOUT"
            ),
            "methane_status": (
                "GOOD"      if summary["methane_mL_per_L_per_d"] > 300 else
                "MODERATE"  if summary["methane_mL_per_L_per_d"] > 150 else
                "LOW"       if summary["methane_mL_per_L_per_d"] > 50  else
                "VERY_LOW"
            ),
            "temperature_correction_applied": temp_corrected,
            "digester_temp_c":    round(digester_temp_c, 1),
            "mu2_max_effective":  round(mu2_max_eff, 4),
            "mu2_max_at_35c":     _ad4_params.mu2_max,
            "kinetic_params": (
                f"Benyahia defaults with Arrhenius T-correction "
                f"(T={digester_temp_c:.1f}°C, mu2_max={mu2_max_eff:.4f} d⁻¹)"
            ),
        }
        return summary

    except Exception as e:
        return {"error": str(e),
                "dilution_rate": dilution_rate,
                "influent_cod_g_per_l": influent_cod_g_per_l}


@mcp.tool()
def ad4_critical_dilution_rate(
    influent_cod_g_per_l: float = 25.0,
    digester_temp_c: Optional[float] = None,
) -> dict:
    """
    Find the washout dilution rate threshold, corrected for current temperature.

    D_crit scales with the Haldane peak of mu2, which itself scales with
    mu2_max via Arrhenius. In winter, a cooler digester has a lower D_crit —
    the safe operating envelope shrinks. This tool makes that explicit.

    Args:
        influent_cod_g_per_l: Influent COD concentration in g/L (default 25).
        digester_temp_c:      Digester temperature (°C). If None, reads from
                              plant_state. Pass explicitly to test winter/summer
                              scenarios without changing plant_state.

    Returns:
        Temperature-corrected D_crit, current plant D, safety margin %,
        and comparison to the 35°C reference value.
    """
    if not _AD4_AVAILABLE:
        return _ad4_unavailable()

    if influent_cod_g_per_l <= 0 or influent_cod_g_per_l > 200:
        return {"error": f"influent_cod_g_per_l must be in (0, 200], got {influent_cod_g_per_l}"}

    try:
        # Read temperature and HRT from plant_state
        with _state_lock:
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT key, value FROM plant_state WHERE key IN (?,?)",
                ("hydraulic_retention_days", "digester_temp_c"),
            )
            rows = {r["key"]: float(r["value"]) for r in cursor.fetchall()}
            conn.close()

        if digester_temp_c is None:
            digester_temp_c = rows.get("digester_temp_c", 35.0)
        current_HRT = rows.get("hydraulic_retention_days", 22.0)
        current_D   = round(1.0 / current_HRT, 5)

        # Analytical D_crit at reference temp (35°C)
        D_crit_ref = _ad4_params.critical_dilution_rate()

        # Analytical D_crit at actual temperature (fast, no ODE)
        D_crit_T = _ad4_params.critical_dilution_rate_at_temp(digester_temp_c)

        # Numerical D_crit by bisection using T-corrected simulator
        from ad4_simulator import AD4Params, AD4Simulator
        mu2_max_eff = _ad4_params.mu2_max_at_temp(digester_temp_c)
        t_params = AD4Params(
            mu1_max=_ad4_params.mu1_max, Ks1=_ad4_params.Ks1,
            k1=_ad4_params.k1,           k2=_ad4_params.k2,
            mu2_max=mu2_max_eff,          Ks2=_ad4_params.Ks2,
            Ki2=_ad4_params.Ki2,          k3=_ad4_params.k3,
            alpha=_ad4_params.alpha,      k6=_ad4_params.k6,
        )
        t_sim = AD4Simulator(params=t_params)
        D_crit_numerical = t_sim.critical_D(S1_in=influent_cod_g_per_l)

        safety_margin_pct = round(
            (D_crit_numerical - current_D) / D_crit_numerical * 100, 1
        )

        return {
            "influent_cod_g_per_l":        influent_cod_g_per_l,
            "digester_temp_c":             round(digester_temp_c, 1),
            "mu2_max_at_35c":              _ad4_params.mu2_max,
            "mu2_max_effective":           round(mu2_max_eff, 4),
            "D_crit_at_35c_per_d":         round(D_crit_ref, 4),
            "D_crit_at_temp_analytical":   round(D_crit_T, 4),
            "D_crit_numerical_per_d":      D_crit_numerical,
            "HRT_crit_days":               round(1.0 / D_crit_numerical, 2),
            "current_plant_HRT_days":      current_HRT,
            "current_plant_D_per_d":       current_D,
            "safety_margin_pct":           safety_margin_pct,
            "D_crit_reduction_vs_35c_pct": round(
                (D_crit_ref - D_crit_numerical) / D_crit_ref * 100, 1),
            "status": (
                "SAFE"    if safety_margin_pct > 40 else
                "CAUTION" if safety_margin_pct > 20 else
                "WARNING — operating close to washout threshold"
            ),
            "recommended_max_D_per_d":  round(D_crit_numerical * 0.40, 5),
            "recommended_min_HRT_days": round(1.0 / (D_crit_numerical * 0.40), 1),
            "temperature_note": (
                f"At {digester_temp_c:.1f}°C, D_crit is "
                f"{round((D_crit_ref - D_crit_numerical)/D_crit_ref*100, 1)}% "
                f"{'lower' if digester_temp_c < 35 else 'higher'} than at 35°C reference. "
                "Arrhenius theta=1.035."
            ),
        }

    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ad4_perturbation_test(
    overload_cod_g_per_l: float,
    overload_days: float = 10.0,
    recovery_days: float = 30.0,
    baseline_cod_g_per_l: float = 0.0,
    digester_temp_c: Optional[float] = None,
) -> dict:
    """
    Simulate a substrate overload spike and test digester recovery.

    Runs a three-segment simulation: baseline → overload → recovery.
    Uses the plant's current HRT and temperature from plant_state.
    mu2_max is Arrhenius-corrected for temperature throughout.

    Args:
        overload_cod_g_per_l:  Elevated influent COD during the spike (g/L).
        overload_days:         Duration of the overload event (days, default 10).
        recovery_days:         Time simulated after returning to normal (days, default 30).
        baseline_cod_g_per_l:  Normal influent COD (g/L). If 0, uses 25.0 g/L.
        digester_temp_c:       Temperature (°C). If None, reads from plant_state.

    Returns:
        Baseline steady state, peak stress during overload, post-recovery state,
        washout/souring flags, and temperature correction details.
    """
    if not _AD4_AVAILABLE:
        return _ad4_unavailable()

    if overload_cod_g_per_l <= 0 or overload_cod_g_per_l > 200:
        return {"error": "overload_cod_g_per_l must be in (0, 200]"}
    if overload_days < 1 or overload_days > 60:
        return {"error": "overload_days must be in [1, 60]"}
    if recovery_days < 5 or recovery_days > 200:
        return {"error": "recovery_days must be in [5, 200]"}

    if baseline_cod_g_per_l <= 0:
        baseline_cod_g_per_l = 25.0

    try:
        # Read HRT and temperature from plant_state
        with _state_lock:
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT key, value FROM plant_state WHERE key IN (?,?,?)",
                ("hydraulic_retention_days", "organic_load_kg_vs_d",
                 "digester_temp_c"),
            )
            rows = {r["key"]: float(r["value"]) for r in cursor.fetchall()}
            conn.close()

        current_HRT = rows.get("hydraulic_retention_days", 22.0)
        D = round(1.0 / current_HRT, 5)
        if digester_temp_c is None:
            digester_temp_c = rows.get("digester_temp_c", 35.0)

        # Build temperature-corrected simulator
        from ad4_simulator import AD4Params, AD4Simulator
        mu2_max_eff = _ad4_params.mu2_max_at_temp(digester_temp_c)
        t_params = AD4Params(
            mu1_max=_ad4_params.mu1_max, Ks1=_ad4_params.Ks1,
            k1=_ad4_params.k1,           k2=_ad4_params.k2,
            mu2_max=mu2_max_eff,          Ks2=_ad4_params.Ks2,
            Ki2=_ad4_params.Ki2,          k3=_ad4_params.k3,
            alpha=_ad4_params.alpha,      k6=_ad4_params.k6,
        )
        t_sim = AD4Simulator(params=t_params)

        # Phase 1: run to steady state at baseline
        baseline_state = t_sim.find_steady_state(D=D, S1_in=baseline_cod_g_per_l)

        # Phase 2-3: overload then recovery
        schedule = [
            {"days": overload_days,  "D": D, "S1_in": overload_cod_g_per_l},
            {"days": recovery_days,  "D": D, "S1_in": baseline_cod_g_per_l},
        ]
        result = t_sim.run_perturbation(initial=baseline_state, schedule=schedule)

        # Extract peak stress during overload window
        n_overload = int(len(result.t) * overload_days / (overload_days + recovery_days))
        S2_peak = float(result.S2[:n_overload].max()) if n_overload > 0 else float(result.S2[0])
        X2_min  = float(result.X2[:n_overload].min()) if n_overload > 0 else float(result.X2[0])

        post = result.steady_state()

        return {
            "scenario":               "overload_spike",
            "plant_HRT_days":         current_HRT,
            "dilution_rate_per_d":    D,
            "digester_temp_c":        round(digester_temp_c, 1),
            "mu2_max_effective":      round(mu2_max_eff, 4),
            "mu2_max_at_35c":         _ad4_params.mu2_max,
            "baseline_cod_g_per_l":   baseline_cod_g_per_l,
            "overload_cod_g_per_l":   overload_cod_g_per_l,
            "overload_duration_days": overload_days,
            "recovery_duration_days": recovery_days,
            "baseline_steady_state": {
                "S1_g_per_L":    round(baseline_state.S1, 4),
                "S2_mmol_per_L": round(baseline_state.S2, 4),
                "X1_g_per_L":    round(baseline_state.X1, 4),
                "X2_g_per_L":    round(baseline_state.X2, 4),
            },
            "peak_stress_during_overload": {
                "S2_peak_mmol_per_L":      round(S2_peak, 2),
                "X2_min_g_per_L":          round(X2_min, 4),
                "souring_during_overload": bool(S2_peak > 150.0),
            },
            "post_recovery_state": {
                "S1_g_per_L":    round(post.S1, 4),
                "S2_mmol_per_L": round(post.S2, 4),
                "X1_g_per_L":    round(post.X1, 4),
                "X2_g_per_L":    round(post.X2, 4),
            },
            "washout_detected":  result.washout_detected(),
            "souring_detected":  result.souring_detected(),
            "recovered_healthy": post.is_healthy(),
            "interpretation": (
                "RECOVERED — digester returned to healthy state after overload."
                if post.is_healthy() and not result.washout_detected()
                else "FAILED — digester did not recover. "
                     "Reduce overload duration or magnitude."
            ),
            "temperature_note": (
                f"mu2_max corrected from {_ad4_params.mu2_max:.4f} (35°C) "
                f"to {mu2_max_eff:.4f} d⁻¹ at {digester_temp_c:.1f}°C. "
                "Lower temperature → slower methanogen recovery after overload."
                if abs(digester_temp_c - 35.0) > 0.5 else
                "Temperature at reference 35°C — no Arrhenius correction applied."
            ),
        }

    except Exception as e:
        return {"error": str(e)}



# ══════════════════════════════════════════════════════════════════════════════
# LAYER 3b — EnKF State Estimation Tools
# Estimates hidden states (S2, X2) from observable Q_CH4 and temperature.
# Requires ad4_enkf.py on the Python path.
# ══════════════════════════════════════════════════════════════════════════════

def _enkf_unavailable() -> dict:
    return {
        "error": "EnKF not available",
        "hint": "Ensure ad4_enkf.py is on the Python path alongside this server.",
    }


@mcp.tool()
def enkf_initialise(
    digester_volume_m3: float = 2000.0,
    hrt_days: float = 22.0,
    s1_in_g_per_l: float = 25.0,
    n_ensemble: int = 100,
    t_ref_celsius: float = 35.0,
) -> dict:
    """
    Initialise (or re-initialise) the Ensemble Kalman Filter for state estimation.

    Call this once at startup, or whenever the digester configuration changes
    significantly (new feedstock regime, major HRT change, restart after washout).

    The EnKF estimates hidden states S2 (VFA) and X2 (methanogen biomass) from
    observable Q_CH4 and temperature using the AD4 ODE as a process model.
    All estimates come with uncertainty bounds — the filter is honest about
    what it cannot directly observe.

    Args:
        digester_volume_m3: Total digester volume (m³). Critical for unit conversion.
        hrt_days:           Current hydraulic retention time (days).
        s1_in_g_per_l:      Estimated influent COD (g/L) from feedstock records.
        n_ensemble:         Ensemble size. 100 is a good default; 200 is more
                            accurate but ~4× slower per update.
        t_ref_celsius:      Reference temperature at which AD4 kinetics were
                            calibrated (default 35°C for Benyahia params).

    Returns:
        Confirmation dict with filter configuration.
    """
    if not _ENKF_AVAILABLE:
        return _enkf_unavailable()

    global _enkf_server
    try:
        from ad4_enkf import AD4EnKFServer, EnKFConfig
        from ad4_simulator import AD4Params
        _enkf_server = AD4EnKFServer(
            digester_volume_m3=digester_volume_m3,
            params=_ad4_params if _AD4_AVAILABLE else AD4Params(),
            config=EnKFConfig(
                n_ensemble=n_ensemble,
                T_ref_celsius=t_ref_celsius,
            ),
        )
        return _enkf_server.initialise(
            hrt_days=hrt_days,
            S1_in=s1_in_g_per_l,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def enkf_update(
    fos_mg_per_l: Optional[float] = None,
    new_hrt_days: Optional[float] = None,
    new_s1_in_g_per_l: Optional[float] = None,
) -> dict:
    """
    Advance the EnKF by one day using current plant_state sensor values.

    Call this once per day (e.g. from a cron job or after morning SCADA reading).
    Reads biogas_flow_nm3h, ch4_pct, and digester_temp_c automatically from
    plant_state (SQLite). You only need to provide optional inputs that are
    not in plant_state.

    The filter returns estimated S2 (VFA) and X2 (methanogen biomass) with
    uncertainty intervals, souring/washout probabilities, and plain-language
    guidance. These are MODEL ESTIMATES, not measurements.

    Args:
        fos_mg_per_l:      FOS (volatile acids) in mg/L if you measured it today.
                           Converts to S2 proxy via S2 ≈ FOS/60. Optional —
                           if provided weekly, dramatically tightens S2 estimate.
        new_hrt_days:      Provide if HRT changed today (feedstock recipe change).
        new_s1_in_g_per_l: Provide if influent COD changed today.

    Returns:
        Posterior state estimate with S2_mean, S2_std, X2_mean, X2_std,
        souring_probability, washout_probability, risk_level, and guidance text.
    """
    if not _ENKF_AVAILABLE:
        return _enkf_unavailable()

    if not _enkf_server._initialised:
        return {
            "error": "EnKF not initialised. Call enkf_initialise() first.",
            "hint": "Typical call: enkf_initialise(digester_volume_m3=2000, hrt_days=22)",
        }

    # Read current plant_state from SQLite
    try:
        with _state_lock:
            conn = _get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT key, value FROM plant_state WHERE key IN (?,?,?)",
                ("biogas_flow_nm3h", "ch4_pct", "digester_temp_c"),
            )
            rows = {r["key"]: float(r["value"]) for r in cursor.fetchall()}
            conn.close()
    except Exception as e:
        return {"error": f"Failed to read plant_state: {e}"}

    biogas = rows.get("biogas_flow_nm3h", 142.0)
    ch4    = rows.get("ch4_pct", 62.0)
    temp   = rows.get("digester_temp_c", 35.0)

    try:
        result = _enkf_server.step(
            biogas_flow_nm3h  = biogas,
            ch4_pct           = ch4,
            digester_temp_c   = temp,
            fos_mg_per_l      = fos_mg_per_l,
            new_hrt_days      = new_hrt_days,
            new_S1_in         = new_s1_in_g_per_l,
        )
        # Attach the plant_state values used so the LLM can see context
        result["plant_state_used"] = {
            "biogas_flow_nm3h": round(biogas, 2),
            "ch4_pct":          round(ch4, 1),
            "digester_temp_c":  round(temp, 1),
            "fos_provided":     fos_mg_per_l is not None,
        }
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def enkf_status() -> dict:
    """
    Return the current EnKF filter status, latest estimate, and 7-day S2 trend.

    Use this to check the running state of the filter without advancing it.
    Useful for: morning briefings, LLM-generated daily summaries, checking
    whether the filter has been initialised and how many days it has tracked.

    Returns:
        Filter status, days tracked, current risk level, S2 trend direction,
        and the latest full estimate if available.
    """
    if not _ENKF_AVAILABLE:
        return _enkf_unavailable()

    summary = _enkf_server.summary()

    # Attach latest estimate if available
    if (_enkf_server._filter is not None
            and _enkf_server._filter.current_estimate() is not None):
        latest = _enkf_server._filter.current_estimate()
        summary["latest_estimate"] = latest.to_dict()

        # Add concise operator summary for LLM
        s2_lo, s2_hi = latest.S2_interval_95()
        summary["operator_summary"] = (
            f"Day {latest.day}: "
            f"Estimated VFA S2={latest.S2_mean:.1f} mmol/L "
            f"(95% CI: {s2_lo:.0f}–{s2_hi:.0f}), "
            f"Methanogens X2={latest.X2_mean:.2f} g/L, "
            f"Souring risk={latest.souring_probability*100:.0f}%, "
            f"Risk: {latest.risk_level()}."
        )

    return summary


@mcp.tool()
def ingest_scada_file(file_path: str, vendor: Optional[str] = None) -> dict:
    """
    Ingest a SCADA export file (CSV or Excel) into the StateBuffer.

    Runs the full scada_mapper -> StateBuffer pipeline:
      1. Auto-detect or use provided vendor hint
      2. Fuzzy-map column names to internal variable names
      3. Pass each row through StateBuffer.insert_live_data() (CUSUM filter)

    Args
    ----
    file_path : Absolute or PROJECT_ROOT-relative path to CSV / Excel file.
    vendor    : Optional SCADA vendor hint: "generic", "siemens",
                "rockwell", "mitsubishi". Auto-detected if omitted.

    Returns
    -------
    Dict with rows_accepted, rows_skipped, mapping used, and cusum_status.
    """
    if not _HAS_STATE_BUFFER or _state_buffer is None:
        return {"error": "StateBuffer not available — cannot ingest SCADA file"}

    try:
        from scada_mapper import read_file, detect_scada_vendor, auto_map_dataframe
        import pandas as pd
    except ImportError as e:
        return {"error": f"scada_mapper not available: {e}"}

    path = Path(file_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        return {"error": f"File not found: {path}"}

    try:
        df, fmt = read_file(path)
    except Exception as e:
        return {"error": f"Could not read file: {e}"}

    detected_vendor = vendor or detect_scada_vendor(df)
    mapping = auto_map_dataframe(df, detected_vendor)

    if not mapping:
        return {
            "error": "No columns could be mapped to internal variable names.",
            "columns_in_file": list(df.columns),
        }

    accepted = skipped = 0
    for _, row in df.iterrows():
        mapped_row: dict = {}
        for internal, actual_col in mapping.items():
            raw_val = row.get(actual_col)
            if raw_val is None or (isinstance(raw_val, float) and pd.isna(raw_val)):
                continue
            try:
                mapped_row[internal] = float(
                    str(raw_val).replace("kg", "").strip()
                )
            except (ValueError, TypeError):
                continue
        if mapped_row:
            _state_buffer.insert_live_data(mapped_row)
            accepted += 1
        else:
            skipped += 1

    return {
        "file":           str(path),
        "format":         fmt,
        "vendor":         detected_vendor,
        "rows_accepted":  accepted,
        "rows_skipped":   skipped,
        "mapping":        mapping,
        "cusum_status":   _state_buffer.get_cusum_status(),
    }


@mcp.tool()
def get_state_buffer_status() -> dict:
    """
    Return StateBuffer health, latest clean readings, and CUSUM accumulator
    status for all monitored sensors.

    Useful for diagnosing sensor drift or anomaly detection behaviour.
    """
    if not _HAS_STATE_BUFFER or _state_buffer is None:
        return {
            "available":    False,
            "reason":       "StateBuffer not initialised (StateBuffer.py not found or "
                            "site_config.json missing)",
            "fallback":     "Using legacy biomethane.db",
        }

    latest  = _state_buffer.get_latest()
    cusum   = _state_buffer.get_cusum_status()
    df      = _state_buffer.get_model_dataframe()

    return {
        "available":      True,
        "db_path":        _state_buffer.db_path,
        "rows_in_buffer": len(df),
        "latest_reading": latest,
        "cusum_status":   cusum,
        "sensors_near_threshold": [
            tag for tag, s in cusum.items()
            if s["H"] and (s["s_pos"] > s["H"] * 0.7 or s["s_neg"] > s["H"] * 0.7)
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Biomethane MCP Server v5")
    parser.add_argument(
        "--http", action="store_true", help="Run HTTP transport (default is stdio)"
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for HTTP transport")
    parser.add_argument(
        "--port", type=int, default=3000, help="Port for HTTP transport"
    )
    parser.add_argument(
        "--site", type=Path, default=None,
        help="Path to site_config.json (overrides PROJECT_ROOT/site_config.json)"
    )
    parser.add_argument(
        "--lab-scale", action="store_true",
        help="Use lab-scale temp thresholds (20-30°C)"
    )
    args, _ = parser.parse_known_args()

    # Set _is_lab_scale from site_config first, then allow CLI flag to override
    _is_lab_scale = _site_config.get("lab_scale", False)
    
    # CLI flag --lab-scale overrides site_config
    if args.lab_scale:
        _is_lab_scale = True
    
    if _is_lab_scale:
        print("Lab-scale mode: temperature thresholds 20-30°C", file=sys.stderr, flush=True)

    # Reload site config and StateBuffer if --site was passed explicitly
    if args.site and args.site.exists():
        _site_config.update(_json.loads(args.site.read_text()))
        _state_buffer = _init_state_buffer(_site_config, lab_scale=_is_lab_scale)
        print(f"Loaded site config: {args.site}", file=sys.stderr, flush=True)

    # Auto-initialise EnKF from site_config estimated_geometry
    if _ENKF_AVAILABLE and not _enkf_server._initialised:
        geo = _site_config.get("estimated_geometry", {})
        if geo:
            result = enkf_initialise(
                digester_volume_m3=geo.get("digester_volume_m3", 2000.0),
                hrt_days=geo.get("hrt_days", 22.0),
                s1_in_g_per_l=25.0,
                n_ensemble=100,
                t_ref_celsius=35.0,
            )
            status = result.get("status", "") if isinstance(result, dict) else ""
            print(f"EnKF auto-initialised: {status}", file=sys.stderr, flush=True)

    # ── Daily assessment background thread ────────────────────────────────────
    # Runs once per day at the configured UTC hour. Uses the live _enkf_server
    # already in memory — no HTTP call needed. Fires enkf_update() then
    # conditionally ad4_perturbation_test() based on souring_probability.
    # Runs as a daemon thread so it does not prevent clean server shutdown.

    def _daily_assessment_loop():
        import time as _time

        da_cfg       = _site_config.get("daily_assessment", {})
        run_hour_utc = int(da_cfg.get(
            "run_hour_utc",
            DEFAULT_DAILY_ASSESSMENT["run_hour_utc"]
        ))
        action_prob  = float(da_cfg.get(
            "souring_probability_action",
            DEFAULT_DAILY_ASSESSMENT["souring_probability_action"]
        ))
        watch_prob   = float(da_cfg.get(
            "souring_probability_watch",
            DEFAULT_DAILY_ASSESSMENT["souring_probability_watch"]
        ))

        last_run_date = None  # Track which calendar date we last ran on

        while True:
            try:
                now_utc = datetime.utcnow()
                today   = now_utc.date()

                if now_utc.hour >= run_hour_utc and today != last_run_date:
                    print(
                        f"[daily_assessment] Running at {now_utc.isoformat()} UTC",
                        file=sys.stderr, flush=True
                    )

                    # Step 1: advance EnKF by one day
                    enkf_result = enkf_update()

                    if "error" in enkf_result:
                        print(
                            f"[daily_assessment] enkf_update error: {enkf_result['error']}",
                            file=sys.stderr, flush=True
                        )
                        last_run_date = today
                        _time.sleep(3600)
                        continue

                    souring_prob = float(enkf_result.get("souring_probability", 0.0))
                    risk_level   = enkf_result.get("risk_level", "UNKNOWN")
                    s2_mean      = enkf_result.get("S2_mean", 0.0)

                    print(
                        f"[daily_assessment] souring_prob={souring_prob:.2f}  "
                        f"risk={risk_level}  S2_mean={s2_mean:.1f} mmol/L",
                        file=sys.stderr, flush=True
                    )

                    # Step 2: conditionally run perturbation test
                    if souring_prob >= action_prob and _AD4_AVAILABLE:
                        # Read s1_in persisted by enkf_initialise (fallback 25 g/L)
                        try:
                            with _state_lock:
                                conn = _get_db_connection()
                                cursor = conn.cursor()
                                cursor.execute(
                                    "SELECT value FROM plant_state WHERE key='enkf_s1_in_g_per_l'"
                                )
                                row = cursor.fetchone()
                                conn.close()
                            s1_in = float(row["value"]) if row else 25.0
                        except Exception:
                            s1_in = 25.0

                        print(
                            f"[daily_assessment] souring_prob >= {action_prob} — "
                            f"running perturbation test (s1_in={s1_in} g/L)",
                            file=sys.stderr, flush=True
                        )
                        pert_result = ad4_perturbation_test(
                            overload_cod_g_per_l=s1_in,
                            overload_days=10.0,
                            recovery_days=30.0,
                        )
                        interpretation = pert_result.get("interpretation", "")
                        print(
                            f"[daily_assessment] perturbation: {interpretation}",
                            file=sys.stderr, flush=True
                        )
                    elif souring_prob >= watch_prob:
                        print(
                            f"[daily_assessment] WATCH — souring_prob={souring_prob:.2f} "
                            f"exceeds watch threshold ({watch_prob}). Monitor closely.",
                            file=sys.stderr, flush=True
                        )
                    else:
                        print(
                            "[daily_assessment] HEALTHY — no action required.",
                            file=sys.stderr, flush=True
                        )

                    last_run_date = today

            except Exception as exc:
                print(
                    f"[daily_assessment] Unhandled exception: {exc}",
                    file=sys.stderr, flush=True
                )

            # Sleep 30 minutes between checks — fine-grained enough to catch
            # the run_hour_utc window without busy-waiting.
            _time.sleep(1800)

    if _ENKF_AVAILABLE and _AD4_AVAILABLE:
        _assessment_thread = threading.Thread(
            target=_daily_assessment_loop,
            name="daily-assessment",
            daemon=True,
        )
        _assessment_thread.start()
        print("[daily_assessment] Background thread started.", file=sys.stderr, flush=True)
    else:
        print(
            "[daily_assessment] Skipped — EnKF or AD4 not available.",
            file=sys.stderr, flush=True
        )

    sb_status = (
        f"StateBuffer: {_state_buffer.db_path}" if _state_buffer
        else "StateBuffer: unavailable (legacy DB only)"
    )

    if args.http:
        try:
            import uvicorn
        except ImportError:
            print("Error: uvicorn not installed. Run: pip install uvicorn", file=sys.stderr)
            exit(1)

        print(f"\n=== Biomethane MCP Server v5 (HTTP) ===", file=sys.stderr)
        print(f"Host: {args.host}  Port: {args.port}", file=sys.stderr)
        print(f"URL:  http://{args.host}:{args.port}/mcp", file=sys.stderr)
        print(f"{sb_status}", file=sys.stderr)
        print(f"========================================\n", file=sys.stderr)

        app = mcp.streamable_http_app()
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    else:
        print(f"\n=== Biomethane MCP Server v5 (stdio) ===", file=sys.stderr)
        print(f"{sb_status}", file=sys.stderr)
        print(f"========================================\n", file=sys.stderr)
        mcp.run()
