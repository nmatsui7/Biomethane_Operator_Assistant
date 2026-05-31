#!/usr/bin/env python3
"""
Biomethane Operations CLI
Exposes core operations as standalone command-line tools.
"""

import argparse
import json
import math
import os
import random
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from tabulate import tabulate
from typing import Optional
from typing import Dict, Any

try:
    import pandas as pd
    import numpy as np
    _PANDAS_AVAILABLE = True
except ImportError:
    _PANDAS_AVAILABLE = False


# ── Project root detection (same as MCP server) ──────────────────────────────
def _find_project_root() -> Path:
    """Find project root using anchor files."""
    anchors = ("pyproject.toml", "setup.py", "setup.cfg", ".git")
    start = Path(__file__).resolve().parent
    for directory in [start, *start.parents]:
        if any((directory / a).exists() for a in anchors):
            return directory
    # Check .env for PROJECT_ROOT
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


# ── Default alert thresholds (same as MCP server v5) ─────────────────────────
# Option A behavior: explicit config in site_config.json overrides these defaults
#
# Two profiles: "lab_scale" and "industrial"
# _is_lab_scale determines which profile is active
#
# Key differences:
# - Temperature: lab=20-30°C, industrial=35-40°C
# - VFA: lab uses Benyahia model (30/80), industrial uses conservative (8/15)
# - NH4, H2S, pH: same for both profiles

DEFAULT_ALERT_THRESHOLDS: Dict[str, Dict[str, Any]] = {
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


# ── Global flags and site config ──────────────────────────────────────────────
_is_lab_scale = False  # Global flag: True if operating with lab-scale data
_site_config: Dict[str, Any] = {}  # Loaded from site_config.json


def _load_site_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load site_config.json if present. Returns empty dict if absent.
    
    Same logic as MCP server v5 for consistency.
    """
    candidates = [
        config_path,
        PROJECT_ROOT / "site_config.json",
        PROJECT_ROOT / "src" / "site_config.json",
    ]
    for p in candidates:
        if p and Path(p).exists():
            return json.loads(Path(p).read_text())
    return {}


def _get_alert_thresholds() -> Dict[str, Any]:
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

# CLI database — retains alert_log and calibration_log only.
# Plant state is read from / written to StateBuffer (plant_state.sqlite).
CLI_DB_PATH = Path("data/biometa_cli.db")

# ── StateBuffer integration ───────────────────────────────────────────────────
# Resolved at import time; can be overridden by BUFFER_DB env variable.
_BUFFER_DB_PATH = Path(os.environ.get("BUFFER_DB", "data/plant_state.sqlite"))

def _get_state_buffer():
    """Return a StateBuffer instance pointed at the shared plant_state.sqlite."""
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _sys.path.insert(0, str(_Path(__file__).resolve().parent))
        from StateBuffer import StateBuffer
        return StateBuffer(db_path=str(_BUFFER_DB_PATH))
    except ImportError:
        return None

PLANT_STATE_DEFAULTS = {
    "digester_temp_c": 37.2,
    "digester_ph": 7.1,
    "vfa_mmol_l": 8.5,
    "alkalinity_mg_caco3_l": 2800.0,
    "nh4_mg_l": 420.0,
    "biogas_flow_nm3h": 142.0,
    "ch4_pct": 62.3,
    "co2_pct": 36.1,
    "h2s_ppm": 380.0,
    "o2_ppm": 180.0,
    "biomethane_purity_pct": 97.4,
    "grid_injection_nm3h": 98.0,
    "organic_load_kg_vs_d": 2.8,
    "hydraulic_retention_days": 22.0,
    "digestate_ts_pct": 3.2,
}

FEEDSTOCKS = {
    "Cattle slurry": {"bmp": 200, "cn": 11, "dm": 8, "vs_dm": 0.80},
    "Pig manure": {"bmp": 310, "cn": 8, "dm": 6, "vs_dm": 0.78},
    "Maize silage": {"bmp": 340, "cn": 25, "dm": 33, "vs_dm": 0.94},
    "Food waste": {"bmp": 480, "cn": 16, "dm": 25, "vs_dm": 0.88},
    "Grass silage": {"bmp": 290, "cn": 18, "dm": 30, "vs_dm": 0.90},
    "Sewage sludge": {"bmp": 220, "cn": 9, "dm": 4, "vs_dm": 0.75},
    "Chicken manure": {"bmp": 350, "cn": 7, "dm": 25, "vs_dm": 0.76},
    "Fat/grease trap": {"bmp": 900, "cn": 30, "dm": 80, "vs_dm": 0.97},
}

OPERATIONAL_REFERENCE = {
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

_SUBSTRATE_FORMULAS = {
    "carbohydrate_cellulose":  {"c": 6,  "h": 10, "o": 5,  "n": 0, "s": 0, "mw": 162.14},
    "carbohydrate_glucose":    {"c": 6,  "h": 12, "o": 6,  "n": 0, "s": 0, "mw": 180.16},
    "protein_generic":         {"c": 5,  "h":  7, "o":  2, "n": 1, "s": 0, "mw": 113.12},
    "lipid_tripalmitin":       {"c": 51, "h": 98, "o":  6, "n": 0, "s": 0, "mw": 807.32},
    "lipid_triolein":          {"c": 57, "h":104, "o":  6, "n": 0, "s": 0, "mw": 885.43},
    "volatile_fatty_acid_acetic": {"c": 2, "h": 4, "o": 2, "n": 0, "s": 0, "mw": 60.05},
}


def init_db():
    """Initialise CLI database (alert_log + calibration_log only).
    Plant state is managed by StateBuffer — no plant_state table here.
    """
    CLI_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CLI_DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS alert_log
           (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
            parameter TEXT, severity TEXT, message TEXT)"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS calibration_log
           (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT,
            cal_type TEXT, inputs TEXT, results TEXT, notes TEXT)"""
    )
    conn.commit()
    conn.close()


def get_state() -> dict:
    """Read latest clean plant state from StateBuffer.
    Falls back to PLANT_STATE_DEFAULTS if StateBuffer is unavailable
    or empty (e.g. before first ingest).
    """
    buf = _get_state_buffer()
    if buf is not None:
        try:
            latest = buf.get_latest()
            buf.close()
            if latest:
                # Merge with defaults so keys not yet in StateBuffer are present
                state = dict(PLANT_STATE_DEFAULTS)
                state.update({k: v for k, v in latest.items() if v is not None})
                return state
        except Exception as exc:
            print(f"[warn] StateBuffer read failed ({exc}), using defaults.", file=sys.stderr)
        finally:
            try:
                buf.close()
            except Exception:
                pass
    return dict(PLANT_STATE_DEFAULTS)


def update_state(updates: dict) -> dict:
    """Write sensor readings into StateBuffer via insert_live_data().
    CUSUM filtering is applied automatically by StateBuffer.
    """
    accepted = []
    rejected = []

    buf = _get_state_buffer()
    if buf is None:
        return {
            "updated": [], "rejected": list(updates.keys()),
            "accepted_count": 0, "rejected_count": len(updates),
            "error": "StateBuffer unavailable — is plant_state.sqlite initialised?",
            "state": get_state(),
        }

    valid: dict = {}
    for key, value in updates.items():
        if key in PLANT_STATE_DEFAULTS:
            if isinstance(value, (int, float)):
                valid[key] = value
                accepted.append(key)
            else:
                rejected.append({"parameter": key, "reason": "Must be numeric"})
        else:
            rejected.append({"parameter": key, "reason": "Unknown parameter"})

    if valid:
        try:
            buf.insert_live_data(valid)
        except Exception as exc:
            print(f"[warn] StateBuffer write failed: {exc}", file=sys.stderr)
            accepted = []
            rejected = list(updates.keys())
        finally:
            buf.close()

    return {
        "updated": accepted,
        "rejected": rejected,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "note": "Values passed through CUSUM — spikes are silently discarded by StateBuffer.",
        "state": get_state(),
    }


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
    state = get_state()
    alerts = []
    temp = state.get("digester_temp_c", 37.2)
    ph = state.get("digester_ph", 7.1)
    vfa = state.get("vfa_mmol_l", 8.5)
    nh4 = state.get("nh4_mg_l", 420)
    h2s = state.get("h2s_ppm", 380)
    purity = state.get("biomethane_purity_pct", 97.4)
    o2 = state.get("o2_ppm", 180)

    # Get thresholds from config (or defaults) based on _is_lab_scale
    thresholds = _get_alert_thresholds()
    profile_name = "lab-scale" if _is_lab_scale else "industrial"

    # Temperature check
    temp_cfg = thresholds.get("digester_temp_c", {"low": 35, "high": 40})
    temp_low, temp_high = temp_cfg["low"], temp_cfg["high"]
    temp_range_str = f"{temp_low}-{temp_high}°C ({profile_name})"

    if temp < temp_low or temp > temp_high:
        alerts.append(
            {"parameter": "digester_temp_c", "severity": "HIGH",
             "message": f"Temperature {temp}C outside {temp_range_str} range"}
        )

    # pH check
    ph_cfg = thresholds.get("digester_ph", {"low": 6.8, "high": 7.8})
    ph_low, ph_high = ph_cfg["low"], ph_cfg["high"]
    if ph < ph_low or ph > ph_high:
        alerts.append(
            {"parameter": "digester_ph", "severity": "HIGH",
             "message": f"pH {ph} outside optimal {ph_low}-{ph_high} range"}
        )

    # VFA check
    vfa_cfg = thresholds.get("vfa_mmol_l", {"medium": 8, "high": 15})
    vfa_medium, vfa_high = vfa_cfg["medium"], vfa_cfg["high"]
    if vfa > vfa_medium:
        severity = "HIGH" if vfa > vfa_high else "MEDIUM"
        alerts.append(
            {"parameter": "vfa_mmol_l", "severity": severity,
             "message": f"VFA {vfa} mmol/L - acidification risk (threshold: {vfa_medium})"}
        )

    # NH4 check
    nh4_cfg = thresholds.get("nh4_mg_l", {"medium": 300, "high": 800})
    nh4_medium, nh4_high = nh4_cfg["medium"], nh4_cfg["high"]
    if nh4 > nh4_medium:
        severity = "HIGH" if nh4 > nh4_high else "MEDIUM"
        alerts.append(
            {"parameter": "nh4_mg_l", "severity": severity,
             "message": f"Ammonium-N {nh4} mg/L - inhibition risk (threshold: {nh4_medium})"}
        )

    # H2S check
    h2s_cfg = thresholds.get("h2s_ppm", {"medium": 200, "high": 500})
    h2s_medium, h2s_high = h2s_cfg["medium"], h2s_cfg["high"]
    if h2s > h2s_medium:
        severity = "HIGH" if h2s > h2s_high else "MEDIUM"
        h2s_desc = "toxicity" if h2s > h2s_high else "elevated"
        alerts.append(
            {"parameter": "h2s_ppm", "severity": severity,
             "message": f"H2S {h2s} ppm - {h2s_desc} (threshold: {h2s_medium})"}
        )

    # Purity check
    purity_cfg = thresholds.get("biomethane_purity_pct", {"min": 95})
    purity_min = purity_cfg["min"]
    if purity < purity_min:
        alerts.append(
            {"parameter": "biomethane_purity_pct", "severity": "HIGH",
             "message": f"Purity {purity}% below grid spec ({purity_min}%)"}
        )

    # O2 check
    o2_cfg = thresholds.get("o2_ppm", {"max": 500})
    o2_max = o2_cfg["max"]
    if o2 > o2_max:
        alerts.append(
            {"parameter": "o2_ppm", "severity": "HIGH",
             "message": f"O2 {o2} ppm - air ingress detected (threshold: {o2_max})"}
        )

    conn = sqlite3.connect(CLI_DB_PATH)
    cur = conn.cursor()
    for alert in alerts:
        cur.execute(
            "INSERT INTO alert_log (timestamp, parameter, severity, message) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), alert["parameter"],
             alert["severity"], alert["message"]),
        )
    conn.commit()
    conn.close()
    return alerts


def get_alert_history(limit: int = 10) -> dict:
    conn = sqlite3.connect(CLI_DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, timestamp, parameter, severity, message FROM alert_log ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return {
        "total": len(rows),
        "records": [
            {"id": r[0], "timestamp": r[1], "parameter": r[2],
             "severity": r[3], "message": r[4]}
            for r in rows
        ],
    }


def blend_feedstocks(recipe: list[dict], target_olr: float = 2.5) -> dict:
    state = get_state()
    ch4_fraction = state.get("ch4_pct", 62.3) / 100.0

    total_vs = 0
    streams = []
    for item in recipe:
        name = item.get("name", "")
        wet_t = item.get("wet_tonnes", 0)
        if name not in FEEDSTOCKS:
            continue
        f = FEEDSTOCKS[name]
        vs = wet_t * 1000 * (f["dm"] / 100) * f["vs_dm"]
        total_vs += vs
        streams.append(
            {"name": name, "wet_tonnes": wet_t, "vs_kg": round(vs, 1), "bmp": f["bmp"]}
        )

    if total_vs == 0:
        return {"error": "No valid feedstocks in recipe"}

    estimated_ch4 = sum(s["vs_kg"] * s["bmp"] for s in streams) / total_vs * ch4_fraction

    cn_ratio = (
        sum(s["vs_kg"] * FEEDSTOCKS[s["name"]]["cn"] for s in streams) / total_vs
        if total_vs > 0
        else 0
    )

    return {
        "streams": streams,
        "total_vs_kg": round(total_vs, 1),
        "estimated_ch4_yield_nl_kg_vs": round(estimated_ch4, 1),
        "blend_cn_ratio": round(cn_ratio, 1),
        "status": "OK" if 20 <= cn_ratio <= 30 else "ADJUST_CN",
    }


def get_reference(topic: str) -> dict:
    topic_lower = topic.lower().strip()
    for key, data in OPERATIONAL_REFERENCE.items():
        if key == topic_lower or topic_lower in data.get("alias", []):
            return {"topic": key, "guidance": data["exact"]}
    return {"topic": topic, "guidance": "No reference found for this topic"}


def get_kpi_summary(period: str = "daily") -> dict:
    import random
    state = get_state()
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


def list_feedstocks() -> dict:
    return {"feedstocks": FEEDSTOCKS}


def buswell_bmp(c: float, h: float, o: float, n: float = 0.0, s: float = 0.0) -> dict:
    ch4_mol = c / 2 + h / 8 - o / 4 - 3 * n / 8 - s / 4
    co2_mol = c / 2 - h / 8 + o / 4 + 3 * n / 8 + s / 4
    h2o_mol = c - h / 4 - o / 2 + 3 * n / 4 + s / 2

    ch4_mol = max(0.0, ch4_mol)
    co2_mol = max(0.0, co2_mol)

    total_gas_mol = ch4_mol + co2_mol
    ch4_pct = (ch4_mol / total_gas_mol * 100) if total_gas_mol > 0 else 0.0
    co2_pct = (co2_mol / total_gas_mol * 100) if total_gas_mol > 0 else 0.0

    NTP_L_PER_MOL = 24.04
    ch4_nl_per_mol_substrate = ch4_mol * NTP_L_PER_MOL

    return {
        "substrate_formula": f"C{c}H{h}O{o}N{n}S{s}",
        "ch4_mol_per_mol_substrate": round(ch4_mol, 4),
        "co2_mol_per_mol_substrate": round(co2_mol, 4),
        "h2o_consumed_mol": round(h2o_mol, 4),
        "ch4_nl_per_mol_substrate": round(ch4_nl_per_mol_substrate, 2),
        "ch4_fraction_pct": round(ch4_pct, 1),
        "co2_fraction_pct": round(co2_pct, 1),
    }


def buswell_bmp_by_class(substrate_class: str) -> dict:
    if substrate_class not in _SUBSTRATE_FORMULAS:
        return {"error": f"Unknown class '{substrate_class}'.", "available": list(_SUBSTRATE_FORMULAS.keys())}

    f = _SUBSTRATE_FORMULAS[substrate_class]
    result = buswell_bmp(c=f["c"], h=f["h"], o=f["o"], n=f["n"], s=f["s"])
    mw = f["mw"]

    bmp_nl_g_vs = result["ch4_nl_per_mol_substrate"] / mw
    result["substrate_class"] = substrate_class
    result["molecular_weight"] = mw
    result["bmp_nl_per_g_vs"] = round(bmp_nl_g_vs, 1)
    result["bmp_nl_per_kg_vs"] = round(bmp_nl_g_vs * 1000, 0)
    return result


def calculate_energy_conversion_factor(ch4_fraction: float = 0.974) -> dict:
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
        "difference_pct": round((LHV_biomethane_kWh - SCRIPT_VALUE) / SCRIPT_VALUE * 100, 2),
    }


def cn_ratio_from_composition(carbon_pct_of_vs: float, nitrogen_pct_of_vs: float) -> dict:
    if nitrogen_pct_of_vs <= 0:
        return {"error": "Nitrogen fraction must be > 0."}

    cn = carbon_pct_of_vs / nitrogen_pct_of_vs

    if 20 <= cn <= 30:
        status = "OPTIMAL"
        advice = "C/N ratio in ideal range (20-30). Good stability expected."
    elif cn < 20:
        status = "LOW — ammonia inhibition risk"
        advice = f"C/N = {cn:.1f}. Add carbon-rich co-substrate."
    elif cn <= 35:
        status = "HIGH — slow degradation"
        advice = f"C/N = {cn:.1f}. Add nitrogen-rich co-substrate."
    else:
        status = "VERY HIGH — significant degradation limitation"
        advice = f"C/N = {cn:.1f}. Substrate is heavily lignocellulosic."

    return {
        "carbon_pct_vs": round(carbon_pct_of_vs, 2),
        "nitrogen_pct_vs": round(nitrogen_pct_of_vs, 2),
        "cn_ratio": round(cn, 1),
        "status": status,
        "advice": advice,
    }


def olr_from_recipe_calc(recipe: list[dict], digester_volume_m3: float) -> dict:
    streams, errors = [], []
    for r in recipe:
        name = r.get("name", "")
        if name not in FEEDSTOCKS:
            errors.append(f"'{name}' not in feedstock table.")
            continue
        f = FEEDSTOCKS[name]
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
        advice = "OLR in ideal mesophilic range (1.5-3.5 kg VS/m3/day)."
    elif olr <= 4.5:
        status = "HIGH"
        advice = "Approaching upper limit. Monitor VFA and FOS/TAC daily."
    else:
        status = "OVERLOADED — acidification risk"
        advice = "Reduce feeding immediately."

    return {
        "recipe_streams": streams,
        "total_vs_kg_per_day": round(total_vs, 1),
        "digester_volume_m3": digester_volume_m3,
        "olr_kg_vs_m3_day": round(olr, 3),
        "status": status,
        "advice": advice,
    }


def biodegradability_coefficient(substrate_class: str, empirical_bmp_nl_per_kg_vs: float) -> dict:
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


def fit_production_distribution(csv_path: Optional[str] = None) -> dict:
    if csv_path is None or not _PANDAS_AVAILABLE:
        return {
            "status": "NO_DATA",
            "message": "Supply a csv_path to fit real parameters. Install pandas and numpy if not present.",
            "placeholder_values": {
                "mean_flow_nm3h": 98.0,
                "std_flow_nm3h": 6.0,
                "mean_purity_pct": 97.5,
                "std_purity_pct": 0.3,
            },
            "how_to_use": "Replace in _simulate_daily_production(): flow = random.gauss(result['mean_flow_nm3h'], result['std_flow_nm3h'])",
        }

    df = pd.read_csv(csv_path)
    df = df.dropna()

    if "flow" in df.columns and "purity" in df.columns:
        flow_clean = df["flow"]
        purity_clean = df["purity"]
    elif "grid_injection_nm3h" in df.columns and "biomethane_purity_pct" in df.columns:
        flow_clean = df["grid_injection_nm3h"]
        purity_clean = df["biomethane_purity_pct"]
    else:
        return {"error": "CSV must have columns: flow/purity or grid_injection_nm3h/biomethane_purity_pct"}

    result = {
        "status": "FITTED",
        "n_rows": len(df),
        "fitted_parameters": {
            "mean_flow_nm3h": round(float(flow_clean.mean()), 2),
            "std_flow_nm3h": round(float(flow_clean.std()), 2),
            "mean_purity_pct": round(float(purity_clean.mean()), 3),
            "std_purity_pct": round(float(purity_clean.std()), 3),
        },
    }
    return result


def fit_uptime_range(csv_path: Optional[str] = None) -> dict:
    if csv_path is None or not _PANDAS_AVAILABLE:
        return {
            "status": "NO_DATA",
            "placeholder_values": {
                "uptime_p5": 0.92,
                "uptime_p95": 0.99,
            },
            "how_to_use": "Replace: uptime = random.uniform(result['uptime_p5'], result['uptime_p95'])",
        }

    df = pd.read_csv(csv_path)
    if "uptime_fraction" in df.columns:
        uptime = df["uptime_fraction"].dropna()
    elif "uptime_pct" in df.columns:
        uptime = df["uptime_pct"].dropna() / 100
    elif "uptime" in df.columns:
        uptime = df["uptime"].dropna()
    else:
        return {"error": "CSV must have column: uptime_fraction, uptime_pct, or uptime"}

    uptime = uptime[(uptime >= 0) & (uptime <= 1)]

    return {
        "status": "FITTED",
        "n_rows": len(uptime),
        "fitted_parameters": {
            "uptime_p5": round(float(np.percentile(uptime, 5)), 3),
            "uptime_p95": round(float(np.percentile(uptime, 95)), 3),
            "uptime_mean": round(float(uptime.mean()), 3),
        },
    }


def fit_steady_state_plant_state(csv_path: Optional[str] = None) -> dict:
    COLUMN_MAP = {
        "digester_temp_c": "digester_temp_c",
        "digester_ph": "digester_ph",
        "vfa_mmol_l": "vfa_mmol_l",
        "alkalinity_mg_caco3_l": "alkalinity_mg_caco3_l",
        "nh4_mg_l": "nh4_mg_l",
        "biogas_flow_nm3h": "biogas_flow_nm3h",
        "ch4_pct": "ch4_pct",
        "co2_pct": "co2_pct",
        "h2s_ppm": "h2s_ppm",
        "o2_ppm": "o2_ppm",
        "biomethane_purity_pct": "biomethane_purity_pct",
        "grid_injection_nm3h": "grid_injection_nm3h",
        "organic_load_kg_vs_d": "organic_load_kg_vs_d",
        "hydraulic_retention_days": "hydraulic_retention_days",
        "digestate_ts_pct": "digestate_ts_pct",
    }

    if csv_path is None or not _PANDAS_AVAILABLE:
        return {
            "status": "NO_DATA",
            "message": "Supply a csv_path with SCADA historian export. Fitted medians will replace defaults.",
            "current_defaults": {k: PLANT_STATE_DEFAULTS.get(k) for k in COLUMN_MAP.keys()},
        }

    df = pd.read_csv(csv_path)
    fitted = {}
    for csv_col, state_key in COLUMN_MAP.items():
        if csv_col in df.columns:
            series = pd.to_numeric(df[csv_col], errors="coerce").dropna()
            if len(series) > 0:
                fitted[state_key] = round(float(series.median()), 3)

    return {
        "status": "FITTED",
        "n_rows": len(df),
        "columns_fitted": list(fitted.keys()),
        "fitted_plant_state": fitted,
    }


def validate_bmp_against_scada(
    feedstock_name: str,
    substrate_class: str,
    measured_bmp: Optional[float] = None,
    csv_path: Optional[str] = None,
) -> dict:
    _FEEDSTOCKS_BMP = {k: v["bmp"] for k, v in FEEDSTOCKS.items()}

    result = {
        "feedstock_name": feedstock_name,
        "substrate_class": substrate_class,
        "script_bmp": _FEEDSTOCKS_BMP.get(feedstock_name, "unknown"),
    }

    theoretical = buswell_bmp_by_class(substrate_class)
    if "bmp_nl_per_kg_vs" in theoretical:
        result["theoretical_bmp_buswell"] = theoretical["bmp_nl_per_kg_vs"]
        if isinstance(result["script_bmp"], (int, float)):
            result["script_vs_theoretical_eta"] = round(result["script_bmp"] / theoretical["bmp_nl_per_kg_vs"], 3)

    if measured_bmp is not None:
        result["measured_bmp_lab"] = measured_bmp

    if csv_path is None or not _PANDAS_AVAILABLE:
        result["scada_status"] = "NO_DATA — supply csv_path to back-calculate yield from SCADA"
    else:
        df = pd.read_csv(csv_path)
        required = ["feedstock_vs_kg_d", "biogas_nm3_d", "ch4_pct"]
        if not all(c in df.columns for c in required):
            result["scada_status"] = f"Missing columns. Need: {required}"
        else:
            df = df[required].dropna()
            df["bm_nm3_d"] = df["biogas_nm3_d"] * (df["ch4_pct"] / 100)
            df["yield_nl_kg_vs"] = (df["bm_nm3_d"] * 1000) / df["feedstock_vs_kg_d"]
            scada_bmp = df["yield_nl_kg_vs"].median()
            result["scada_back_calculated_bmp"] = round(float(scada_bmp), 1)

    return result


def format_output(data: dict, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(data, indent=2)
    if fmt == "table":
        if "state" in data:
            rows = [[k, v] for k, v in data["state"].items()]
            return tabulate(rows, headers=["Parameter", "Value"], tablefmt="simple")
        if "records" in data:
            if data["records"] and "cal_type" in data["records"][0]:
                rows = [[r["timestamp"], r["cal_type"], r["notes"] or ""]
                       for r in data["records"]]
                return tabulate(rows, headers=["Timestamp", "Type", "Notes"], tablefmt="simple")
            else:
                rows = [[r["timestamp"], r["parameter"], r["severity"], r["message"]]
                       for r in data["records"]]
                return tabulate(rows, headers=["Timestamp", "Parameter", "Severity", "Message"], tablefmt="simple")
        if "streams" in data:
            rows = [[s["name"], s["wet_tonnes"], s["vs_kg"], s["bmp"]]
                   for s in data["streams"]]
            return tabulate(rows, headers=["Feedstock", "Wet Tonnes", "VS (kg)", "BMP"], tablefmt="simple")
        return json.dumps(data, indent=2)
    return str(data)


def cmd_get_state(args):
    state = get_state()
    print(format_output({"state": state}, args.format))


def cmd_update_state(args):
    updates = {}
    if args.temp:
        updates["digester_temp_c"] = args.temp
    if args.ph:
        updates["digester_ph"] = args.ph
    if args.vfa:
        updates["vfa_mmol_l"] = args.vfa
    if args.nh4:
        updates["nh4_mg_l"] = args.nh4
    if args.h2s:
        updates["h2s_ppm"] = args.h2s
    if args.purity:
        updates["biomethane_purity_pct"] = args.purity
    if args.flow:
        updates["biogas_flow_nm3h"] = args.flow
    if args.grid:
        updates["grid_injection_nm3h"] = args.grid
    if not updates:
        print("No updates provided")
        return
    result = update_state(updates)
    print(format_output(result, args.format))


def cmd_check_alerts(args):
    alerts = check_alerts()
    if alerts:
        print(format_output({"records": [{"timestamp": datetime.now().isoformat(), **a} for a in alerts]}, args.format))
    else:
        print("No alerts")


def cmd_alert_history(args):
    result = get_alert_history(args.limit)
    print(format_output(result, args.format))


def cmd_blend(args):
    recipe = []
    for item in args.recipe:
        if ":" in item:
            name, tons = item.split(":")
            recipe.append({"name": name.strip(), "wet_tonnes": float(tons)})
    result = blend_feedstocks(recipe, args.olr)
    print(format_output(result, args.format))


def cmd_reference(args):
    result = get_reference(args.topic)
    print(format_output(result, args.format))


def cmd_kpi(args):
    result = get_kpi_summary(args.period)
    print(format_output(result, args.format))


def cmd_list_feedstocks(args):
    result = list_feedstocks()
    print(format_output(result, args.format))


def cmd_calibration_history(args):
    conn = sqlite3.connect(CLI_DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, timestamp, cal_type, inputs, results, notes FROM calibration_log ORDER BY id DESC LIMIT ?",
        (args.limit,),
    )
    rows = cur.fetchall()
    conn.close()
    if rows:
        records = [
            {"id": r[0], "timestamp": r[1], "cal_type": r[2], "inputs": r[3], "results": r[4], "notes": r[5]}
            for r in rows
        ]
        print(format_output({"records": records}, args.format))
    else:
        print("No calibration history")


def save_calibration(cal_type: str, inputs: dict, results: dict, notes: str = "") -> dict:
    conn = sqlite3.connect(CLI_DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO calibration_log (timestamp, cal_type, inputs, results, notes) VALUES (?, ?, ?, ?, ?)",
        (datetime.now().isoformat(), cal_type, json.dumps(inputs), json.dumps(results), notes)
    )
    conn.commit()
    conn.close()
    return {"status": "saved", "cal_type": cal_type, "timestamp": datetime.now().isoformat()}


def cmd_calibrate(args):
    csv_path = getattr(args, 'csv', None)
    cal_type = args.calibrate_cmd
    inputs = {}
    result = {}

    if args.calibrate_cmd == "buswell":
        inputs = {"c": args.c, "h": args.h, "o": args.o, "n": args.n, "s": args.s}
        result = buswell_bmp(c=args.c, h=args.h, o=args.o, n=args.n, s=args.s)
    elif args.calibrate_cmd == "buswell-class":
        inputs = {"substrate_class": args.substrate_class}
        result = buswell_bmp_by_class(args.substrate_class)
    elif args.calibrate_cmd == "energy-factor":
        inputs = {"purity": args.purity}
        result = calculate_energy_conversion_factor(ch4_fraction=args.purity)
    elif args.calibrate_cmd == "cn":
        inputs = {"carbon": args.carbon, "nitrogen": args.nitrogen}
        result = cn_ratio_from_composition(carbon_pct_of_vs=args.carbon, nitrogen_pct_of_vs=args.nitrogen)
    elif args.calibrate_cmd == "olr":
        recipe = []
        for item in args.recipe:
            if ":" in item:
                name, tons = item.split(":")
                recipe.append({"name": name.strip(), "wet_tonnes": float(tons)})
        inputs = {"recipe": args.recipe, "volume": args.volume}
        result = olr_from_recipe_calc(recipe, args.volume)
    elif args.calibrate_cmd == "biodegradability":
        inputs = {"substrate_class": args.substrate_class, "empirical": args.empirical}
        result = biodegradability_coefficient(args.substrate_class, args.empirical)
    elif args.calibrate_cmd == "fit-production":
        inputs = {"csv_path": csv_path}
        result = fit_production_distribution(csv_path)
    elif args.calibrate_cmd == "fit-uptime":
        inputs = {"csv_path": csv_path}
        result = fit_uptime_range(csv_path)
    elif args.calibrate_cmd == "fit-state":
        inputs = {"csv_path": csv_path}
        result = fit_steady_state_plant_state(csv_path)
    elif args.calibrate_cmd == "validate-bmp":
        inputs = {"feedstock": args.feedstock, "substrate_class": args.substrate_class, "lab_bmp": args.lab, "csv": csv_path}
        result = validate_bmp_against_scada(args.feedstock, args.substrate_class, args.lab, csv_path)
    else:
        result = {"error": f"Unknown calibration command: {args.calibrate_cmd}"}

    if args.save:
        save_msg = save_calibration(cal_type, inputs, result, args.notes or "")
        print(format_output(save_msg, args.format))
    print(format_output(result, args.format))


def main():
    global _is_lab_scale, _site_config
    parser = argparse.ArgumentParser(description="Biomethane Operations CLI")
    parser.add_argument("--format", choices=["json", "table"], default="table",
                     help="Output format")
    parser.add_argument("--lab-scale", action="store_true",
                     help="Use lab-scale thresholds (20-30°C temp, Benyahia VFA values)")
    parser.add_argument("--site", type=Path, default=None,
                     help="Path to site_config.json (overrides PROJECT_ROOT/site_config.json)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_state = sub.add_parser("get-state", help="Get current plant state")
    p_state.add_argument("--format", choices=["json", "table"], default="table")

    p_update = sub.add_parser("update", help="Update plant state")
    p_update.add_argument("--temp", type=float, help="Digester temperature (C)")
    p_update.add_argument("--ph", type=float, help="Digester pH")
    p_update.add_argument("--vfa", type=float, help="VFA (mmol/L)")
    p_update.add_argument("--nh4", type=float, help="Ammonium-N (mg/L)")
    p_update.add_argument("--h2s", type=float, help="H2S (ppm)")
    p_update.add_argument("--purity", type=float, help="Biomethane purity (%%)")
    p_update.add_argument("--flow", type=float, help="Biogas flow (nm3/h)")
    p_update.add_argument("--grid", type=float, help="Grid injection (nm3/h)")
    p_update.add_argument("--format", choices=["json", "table"], default="table")

    p_alerts = sub.add_parser("check-alerts", help="Check for alerts")
    p_alerts.add_argument("--format", choices=["json", "table"], default="table")

    p_hist = sub.add_parser("alert-history", help="Get alert history")
    p_hist.add_argument("--limit", type=int, default=10)
    p_hist.add_argument("--format", choices=["json", "table"], default="table")

    p_blend = sub.add_parser("blend", help="Blend feedstocks")
    p_blend.add_argument("recipe", nargs="+", help="Feedstock:tonnes e.g. 'Maize silage:5'")
    p_blend.add_argument("--olr", type=float, default=2.5, help="Target OLR")
    p_blend.add_argument("--format", choices=["json", "table"], default="table")

    p_ref = sub.add_parser("reference", help="Lookup operational reference")
    p_ref.add_argument("topic", help="Topic: fos_tac, temperature, olr, cn_ratio")
    p_ref.add_argument("--format", choices=["json", "table"], default="table")

    p_kpi = sub.add_parser("kpi", help="Get KPI summary")
    p_kpi.add_argument("--period", choices=["daily", "weekly", "monthly"],
                       default="daily")
    p_kpi.add_argument("--format", choices=["json", "table"], default="table")

    p_list = sub.add_parser("list-feedstocks", help="List available feedstocks")
    p_list.add_argument("--format", choices=["json", "table"], default="table")

    p_calib = sub.add_parser("calibrate", help="Calibration operations")
    p_calib.add_argument("--format", choices=["json", "table"], default="table")
    p_calib_sub = p_calib.add_subparsers(dest="calibrate_cmd", required=True)

    p_calib_hist = sub.add_parser("calibration-history", help="View saved calibrations")
    p_calib_hist.add_argument("--limit", type=int, default=10, help="Max records")
    p_calib_hist.add_argument("--format", choices=["json", "table"], default="table")

    p_buswell = p_calib_sub.add_parser("buswell", help="Buswell BMP from formula")
    p_buswell.add_argument("--c", type=float, default=6, help="Carbon atoms")
    p_buswell.add_argument("--h", type=float, default=12, help="Hydrogen atoms")
    p_buswell.add_argument("--o", type=float, default=6, help="Oxygen atoms")
    p_buswell.add_argument("--n", type=float, default=0, help="Nitrogen atoms")
    p_buswell.add_argument("--s", type=float, default=0, help="Sulfur atoms")
    p_buswell.add_argument("--save", action="store_true", help="Save to database")
    p_buswell.add_argument("--notes", type=str, default="", help="Notes")

    p_class = p_calib_sub.add_parser("buswell-class", help="Buswell BMP by substrate class")
    p_class.add_argument("substrate_class", help="Substrate class")
    p_class.add_argument("--save", action="store_true", help="Save to database")
    p_class.add_argument("--notes", type=str, default="", help="Notes")

    p_energy = p_calib_sub.add_parser("energy-factor", help="Energy conversion factor")
    p_energy.add_argument("--purity", type=float, default=0.974, help="CH4 fraction")
    p_energy.add_argument("--save", action="store_true", help="Save to database")
    p_energy.add_argument("--notes", type=str, default="", help="Notes")

    p_cn = p_calib_sub.add_parser("cn", help="C/N from elemental composition")
    p_cn.add_argument("--carbon", type=float, required=True, help="Carbon %% of VS")
    p_cn.add_argument("--nitrogen", type=float, required=True, help="Nitrogen %% of VS")
    p_cn.add_argument("--save", action="store_true", help="Save to database")
    p_cn.add_argument("--notes", type=str, default="", help="Notes")

    p_olr = p_calib_sub.add_parser("olr", help="OLR from feedstock recipe")
    p_olr.add_argument("recipe", nargs="+", help="Feedstock:tonnes e.g. 'Maize silage:5, Cattle slurry:10'")
    p_olr.add_argument("--volume", type=float, required=True, help="Digester volume (m3)")
    p_olr.add_argument("--save", action="store_true", help="Save to database")
    p_olr.add_argument("--notes", type=str, default="", help="Notes")

    p_bio = p_calib_sub.add_parser("biodegradability", help="Biodegradability coefficient")
    p_bio.add_argument("substrate_class", help="Substrate class")
    p_bio.add_argument("--empirical", type=float, required=True, help="Empirical BMP (NL/kg VS)")
    p_bio.add_argument("--save", action="store_true", help="Save to database")
    p_bio.add_argument("--notes", type=str, default="", help="Notes")

    p_fit_prod = p_calib_sub.add_parser("fit-production", help="Fit production distribution from CSV")
    p_fit_prod.add_argument("csv", nargs="?", default=None, help="Path to SCADA CSV")
    p_fit_prod.add_argument("--save", action="store_true", help="Save to database")
    p_fit_prod.add_argument("--notes", type=str, default="", help="Notes")

    p_fit_uptime = p_calib_sub.add_parser("fit-uptime", help="Fit uptime range from CSV")
    p_fit_uptime.add_argument("csv", nargs="?", default=None, help="Path to SCADA CSV")
    p_fit_uptime.add_argument("--save", action="store_true", help="Save to database")
    p_fit_uptime.add_argument("--notes", type=str, default="", help="Notes")

    p_fit_state = p_calib_sub.add_parser("fit-state", help="Fit plant state from CSV")
    p_fit_state.add_argument("csv", nargs="?", default=None, help="Path to SCADA CSV")
    p_fit_state.add_argument("--save", action="store_true", help="Save to database")
    p_fit_state.add_argument("--notes", type=str, default="", help="Notes")

    p_val_bmp = p_calib_sub.add_parser("validate-bmp", help="Validate BMP against SCADA")
    p_val_bmp.add_argument("feedstock", help="Feedstock name")
    p_val_bmp.add_argument("substrate_class", help="Substrate class")
    p_val_bmp.add_argument("--lab", type=float, help="Lab BMP assay (NL/kg VS)")
    p_val_bmp.add_argument("csv", nargs="?", default=None, help="Path to SCADA CSV")
    p_val_bmp.add_argument("--save", action="store_true", help="Save to database")
    p_val_bmp.add_argument("--notes", type=str, default="", help="Notes")

    # ── AD4 Simulation Commands ─────────────────────────────────────
    p_ad4 = sub.add_parser("simulate", help="AD4 4-state simulation")
    p_ad4_sub = p_ad4.add_subparsers(dest="simulate_cmd", required=True)

    # simulate run
    p_ad4_run = p_ad4_sub.add_parser("run", help="Run AD4 at steady state")
    p_ad4_run.add_argument("--D", type=float, required=True, help="Dilution rate D in d^-1")
    p_ad4_run.add_argument("--S1", type=float, required=True, help="Influent COD (g/L)")
    p_ad4_run.add_argument("--days", type=float, default=100, help="Simulation days")
    p_ad4_run.add_argument("--format", choices=["json", "text", "csv"], default="text", help="Output format")
    p_ad4_run.add_argument("--output", type=str, default=None, help="CSV output file")

    # simulate critical-d
    p_ad4_crit = p_ad4_sub.add_parser("critical-d", help="Find washout threshold")
    p_ad4_crit.add_argument("--S1", type=float, default=25, help="Influent COD (g/L)")
    p_ad4_crit.add_argument("--format", choices=["json", "text", "csv"], default="text", help="Output format")

    # simulate perturb
    p_ad4_pert = p_ad4_sub.add_parser("perturb", help="Test overload/recovery")
    p_ad4_pert.add_argument("--overload", type=float, required=True, help="COD during spike (g/L)")
    p_ad4_pert.add_argument("--days", type=float, default=10, help="Overload duration (days)")
    p_ad4_pert.add_argument("--recovery", type=float, default=30, help="Recovery days")
    p_ad4_pert.add_argument("--baseline", type=float, default=25, help="Baseline COD (g/L)")
    p_ad4_pert.add_argument("--format", choices=["json", "text", "csv"], default="text", help="Output format")
    p_ad4_pert.add_argument("--output", type=str, default=None, help="CSV output file")

    # simulate fit
    p_ad4_fit = p_ad4_sub.add_parser("fit", help="Fit AD4 parameters to measured data")
    p_ad4_fit.add_argument("--recipe", nargs="+", required=True, help="Feedstock:tonnes e.g. 'Cattle slurry:30'")
    p_ad4_fit.add_argument("--volume", type=float, required=True, help="Digester volume (m3)")
    p_ad4_fit.add_argument("--measured", type=float, required=True, help="Measured CH4 production (Nm3/d)")
    p_ad4_fit.add_argument("--format", choices=["json", "text", "csv"], default="text", help="Output format")
    p_ad4_fit.add_argument("--output", type=str, default=None, help="CSV output file")
    p_ad4_fit.add_argument("--save", action="store_true", help="Save to database")

    args = parser.parse_args()

    # Load site_config.json (same logic as MCP server v5)
    if args.site and args.site.exists():
        _site_config.update(json.loads(args.site.read_text()))
        print(f"Loaded site config: {args.site}")
    else:
        _site_config.update(_load_site_config())
    
    # Set _is_lab_scale from site_config first, then allow CLI flag to override
    _is_lab_scale = _site_config.get("lab_scale", False)
    if args.lab_scale:
        _is_lab_scale = True
    
    if _is_lab_scale:
        print("Lab-scale mode: temperature thresholds 20-30°C, VFA thresholds 30/80 mmol/L")

    if not CLI_DB_PATH.exists():
        init_db()

    if args.cmd == "get-state":
        cmd_get_state(args)
    elif args.cmd == "update":
        cmd_update_state(args)
    elif args.cmd == "check-alerts":
        cmd_check_alerts(args)
    elif args.cmd == "alert-history":
        cmd_alert_history(args)
    elif args.cmd == "blend":
        cmd_blend(args)
    elif args.cmd == "reference":
        cmd_reference(args)
    elif args.cmd == "kpi":
        cmd_kpi(args)
    elif args.cmd == "list-feedstocks":
        cmd_list_feedstocks(args)
    elif args.cmd == "calibrate":
        cmd_calibrate(args)
    elif args.cmd == "calibration-history":
        cmd_calibration_history(args)
    elif args.cmd == "simulate":
        cmd_simulate(args)


# ══════════════════════════════════════════════════════════════════════════════
# AD4 Simulation Handler
# ══════════════════════════════════════════════════════════════════════════════

def cmd_simulate(args):
    """Handle AD4 simulation commands."""
    # Import AD4 tools (lazy import to avoid issues if not available)
    import sys
    from pathlib import Path
    try:
        from bio_methane_operations_mcp_server_v5 import (
            ad4_simulate, ad4_critical_dilution_rate, ad4_perturbation_test
        )
    except ImportError:
        print("Error: AD4 simulator not available")
        print("Ensure ad4_simulator.py and bio_methane_operations_mcp_server_v5.py are in src/")
        return

    if args.simulate_cmd == "run":
        result = ad4_simulate(
            dilution_rate=args.D,
            influent_cod_g_per_l=args.S1,
            days=args.days,
        )
        output_result(result, args)

    elif args.simulate_cmd == "critical-d":
        result = ad4_critical_dilution_rate(influent_cod_g_per_l=args.S1)
        output_result(result, args)

    elif args.simulate_cmd == "perturb":
        result = ad4_perturbation_test(
            overload_cod_g_per_l=args.overload,
            overload_days=args.days,
            recovery_days=args.recovery,
            baseline_cod_g_per_l=args.baseline,
        )
        output_result(result, args)

    elif args.simulate_cmd == "fit":
        print("simulate fit is not available in this version.")
        print("Use the MCP server or calibrate through the chat interface instead.")


def output_result(result, args):
    """Output result in json, text, or csv format."""
    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
        return

    if args.format == "csv":
        write_csv_output(result, args)
        return

    # text format
    format_text_output(result, args)


def format_text_output(result, args):
    """Format output as human-readable text."""
    if "error" in result:
        print(f"Error: {result.get('error')}")
        if "hint" in result:
            print(f"Hint: {result.get('hint')}")
        return

    cmd = args.simulate_cmd

    if cmd == "run":
        ss = result.get("steady_state", {})
        interp = result.get("interpretation", {})
        print(f"\n{'='*50}")
        print(f"  AD4 Simulation Results")
        print(f"{'='*50}")
        print(f"D = {result.get('dilution_rate_per_d'):.4f} d^-1  (HRT = {result.get('HRT_days'):.1f} days)")
        print(f"S1_in = {result.get('influent_COD_g_per_L'):.1f} g/L")
        print(f"\nSteady State:")
        print(f"  S1: {ss.get('S1_g_per_L'):.4f} g/L")
        print(f"  S2: {ss.get('S2_mmol_per_L'):.4f} mmol/L ({interp.get('S2_status', 'N/A')})")
        print(f"  X1: {ss.get('X1_g_per_L'):.4f} g/L")
        print(f"  X2: {ss.get('X2_g_per_L'):.4f} g/L ({interp.get('X2_status', 'N/A')})")
        print(f"\nMethane: {result.get('methane_mL_per_L_per_d'):.2f} mL/L/d ({interp.get('methane_status', 'N/A')})")
        print(f"Solver: {'OK' if result.get('solver_ok') else 'FAILED'}")
        print(f"Healthy: {'YES' if result.get('healthy') else 'NO'}")

    elif cmd == "critical-d":
        print(f"\n{'='*50}")
        print(f"  Critical Dilution Rate")
        print(f"{'='*50}")
        print(f"D_crit (numerical): {result.get('D_crit_numerical_per_d'):.4f} d^-1")
        print(f"D_crit (theoretical): {result.get('D_crit_theoretical_per_d'):.4f} d^-1")
        print(f"HRT_crit: {result.get('HRT_crit_days'):.2f} days")
        print(f"\nCurrent plant: HRT = {result.get('current_plant_HRT_days'):.1f} days, D = {result.get('current_plant_D_per_d'):.5f}")
        print(f"Safety margin: {result.get('safety_margin_pct'):.1f}% ({result.get('status')})")
        print(f"Recommended max D: {result.get('recommended_max_D_per_d'):.4f} d^-1")
        print(f"Recommended min HRT: {result.get('recommended_min_HRT_days'):.1f} days")

    elif cmd == "perturb":
        baseline = result.get("baseline_steady_state", {})
        peak = result.get("peak_stress_during_overload", {})
        post = result.get("post_recovery_state", {})
        print(f"\n{'='*50}")
        print(f"  Overload Perturbation Test")
        print(f"{'='*50}")
        print(f"Scenario: {result.get('scenario')}")
        print(f"Baseline COD: {result.get('baseline_cod_g_per_L')} g/L")
        print(f"Overload COD: {result.get('overload_cod_g_per_L')} g/L for {result.get('overload_duration_days')} days")
        print(f"\nBaseline steady state:")
        print(f"  S1: {baseline.get('S1_g_per_L'):.4f}, S2: {baseline.get('S2_mmol_per_L'):.4f}")
        print(f"  X2: {baseline.get('X2_g_per_L'):.4f}")
        print(f"\nPeak stress during overload:")
        print(f"  S2 peak: {peak.get('S2_peak_mmol_per_L'):.2f} mmol/L")
        print(f"  X2 min: {peak.get('X2_min_g_per_L'):.4f}")
        print(f"\nPost-recovery:")
        print(f"  S1: {post.get('S1_g_per_L'):.4f}, S2: {post.get('S2_mmol_per_L'):.4f}")
        print(f"  X2: {post.get('X2_g_per_L'):.4f}")
        print(f"\nRecovered: {'YES' if result.get('recovered_healthy') else 'NO'}")
        print(f"Washout: {'YES' if result.get('washout_detected') else 'NO'}")
        print(f"Souring: {'YES' if result.get('souring_detected') else 'NO'}")
        print(f"Interpretation: {result.get('interpretation')}")


def write_csv_output(result, args):
    """Write time series to CSV for plotting."""
    if not args.output:
        print("Error: --output required for CSV format")
        return

    cmd = args.simulate_cmd
    rows = []

    if cmd == "run":
        # Need full time series - run without summary only
        import sys
        from ad4_simulator import AD4Simulator, AD4Params
        sim = AD4Simulator(params=AD4Params())
        res = sim.run(days=int(args.days), D=args.D, S1_in=args.S1)
        for i in range(len(res.t)):
            rows.append({
                "day": round(res.t[i], 1),
                "S1_g_L": round(res.S1[i], 4),
                "S2_mmol_L": round(res.S2[i], 4),
                "X1_g_L": round(res.X1[i], 4),
                "X2_g_L": round(res.X2[i], 4),
                "Q_CH4_mL_L_d": round(res.methane_flow()[i], 2),
            })

    elif cmd == "perturb":
        import sys
        from ad4_simulator import AD4Simulator, AD4Params
        sim = AD4Simulator(params=AD4Params())
        D = 1.0 / 22.0  # Assume HRT=22 from plant
        schedule = [
            {"days": 30, "D": D, "S1_in": args.baseline},
            {"days": args.days, "D": D, "S1_in": args.overload},
            {"days": args.recovery, "D": D, "S1_in": args.baseline},
        ]
        res = sim.run_perturbation(AD4State(), schedule)
        for i in range(len(res.t)):
            rows.append({
                "day": round(res.t[i], 1),
                "S1_g_L": round(res.S1[i], 4),
                "S2_mmol_L": round(res.S2[i], 4),
                "X1_g_L": round(res.X1[i], 4),
                "X2_g_L": round(res.X2[i], 4),
                "Q_CH4_mL_L_d": round(res.methane_flow()[i], 2),
            })

    elif cmd == "critical-d":
        print("Warning: --format csv not supported for critical-d, switching to text")
        format_text_output(result, args)
        return

    import csv
    with open(args.output, "w", newline="") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()