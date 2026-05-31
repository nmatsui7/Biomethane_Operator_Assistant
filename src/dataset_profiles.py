"""
dataset_profiles.py — Dataset Profile Configuration System
=======================================================

Defines characteristics of different datasets to allow a single pipeline
to handle multiple datasets with different formats, units, and scales.

Usage:
    from dataset_profiles import get_profile, detect_dataset_profile

    profile = get_profile("indian")
    # or
    profile = detect_dataset_profile(Path("data.csv"))
"""

from typing import Dict, Any, Optional
from pathlib import Path
import pandas as pd

# =============================================================================
# Profile Definitions
# =============================================================================

DATASET_PROFILES: Dict[str, Dict[str, Any]] = {
    "ri_flex": {
        "name": "R1-FLEX Lab Scale",
        "lab_scale": True,
        "temp_range": [20, 30],
        "biogas_unit": "mL/day",
        "biogas_conversion": 24_000_000,  # mL/day → Nm³/h
        "date_columns": None,  # single date column exists
        "date_format": None,  # auto-detect
        "outlier_thresholds": {
            "biogas_min": 0,
            "biogas_max": 5000,  # mL/day
            "temp_min": 5,
            "temp_max": 50,
        },
        "cusum_profile": "lab_scale",
        "enkf_t_ref": 21.0,
        "default_ch4_pct": None,  # use actual if available
        "aggregate_per_day": False,
        "digester_volume_est": 1.0,
        "hrt_days_est": 17.0,
    },
    "indian": {
        "name": "Indian Biogas Plant (Industrial)",
        "lab_scale": False,
        "temp_range": [35, 40],
        "biogas_unit": "m3/day",
        "biogas_conversion": 24,  # m³/day → Nm³/h
        "date_columns": ["Year", "Month", "Day"],
        "date_format": "{Year}-{Month:02d}-{Day:02d}",
        "outlier_thresholds": {
            "biogas_min": 0,
            "biogas_max": 500,  # m³/day
            "temp_min": 25,
            "temp_max": 50,
        },
        "cusum_profile": "industrial",
        "enkf_t_ref": 35.0,
        "default_ch4_pct": 58.0,  # Fixed estimate for Indian dataset
        "aggregate_per_day": True,  # 2.8 readings/day → aggregate
        "digester_volume_est": 100.0,  # Estimate for Indian plant
        "hrt_days_est": 20.0,
    },
}


# =============================================================================
# Profile Access Functions
# =============================================================================

def get_profile(profile_name: str) -> Optional[Dict[str, Any]]:
    """
    Get a dataset profile by name.

    Args:
        profile_name: "ri_flex", "indian", etc.

    Returns:
        Profile dict or None if not found.
    """
    return DATASET_PROFILES.get(profile_name)


def list_profiles() -> list:
    """List available profile names."""
    return list(DATASET_PROFILES.keys())


def detect_dataset_profile(csv_path: Path) -> Optional[str]:
    """
    Auto-detect dataset profile based on CSV structure.

    Detection logic:
    - Indian dataset: Has Year, Month, Day columns + biogas_production
    - ri_flex dataset: Has Date column + Daily Biogas (mL)

    Returns:
        Profile name ("ri_flex", "indian") or None if unknown.
    """
    try:
        # Read just first few rows to check columns
        df = pd.read_csv(csv_path, nrows=5)
        columns = set(df.columns)

        # Check for Indian dataset signature
        if {"Year", "Month", "Day"}.issubset(columns) and "biogas_production" in columns:
            return "indian"

        # Check for ri_flex signature
        col_str = ' '.join(columns).lower()
        if "daily biogas" in col_str or "digestertemp" in col_str:
            return "ri_flex"

    except Exception:
        pass

    return None


def apply_profile_to_args(args, profile: Dict[str, Any]) -> None:
    """
    Apply profile defaults to argparse args if not explicitly set.

    Only sets values if they match the defaults (indicating user didn't set them).
    """
    # Only apply if user didn't explicitly set these
    if hasattr(args, 'digester_volume') and args.digester_volume == 1.0:
        args.digester_volume = profile.get("digester_volume_est", 1.0)

    if hasattr(args, 'hrt_days') and args.hrt_days == 17.0:
        args.hrt_days = profile.get("hrt_days_est", 17.0)

    # Set lab_scale based on profile
    if hasattr(args, 'lab_scale'):
        args.lab_scale = profile.get("lab_scale", False)
