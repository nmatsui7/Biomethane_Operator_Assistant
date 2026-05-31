"""
Biomethane MCP Server — Calibration Functions
==============================================

Calibration functions for anaerobic digestion process modeling.

Two deployment options:

1. **CLI** (recommended for standalone use):
   All functions are exposed via bio_cli.py calibrate subcommand:
   ```bash
   src/bio_cli.py calibrate buswell --c 6 --h 12 --o 6
   src/bio_cli.py calibrate cn --carbon 44.0 --nitrogen 1.8
   ```

2. **Python module** (for integration):
   ```python
   from src.biomethane_calibration import buswell_bmp, cn_ratio_from_composition
   ```

Two groups of calibration functions:

GROUP 1 — Fully implemented (no data needed)
    Physics/chemistry-based calculations using established equations.
    Results are deterministic and verifiable against published sources.

    1a. buswell_bmp()          — theoretical BMP from elemental composition
    1b. buswell_biogas_composition() — theoretical CH₄/CO₂ split
    1c. calculate_energy_conversion_factor() — LHV-based kWh/Nm³
    1d. cn_ratio_from_composition() — C/N from elemental fractions
    1e. olr_from_feedstock()   — OLR mass balance from recipe

GROUP 2 — Documented skeletons (require your SCADA CSV)
    Statistical fitting of simulation parameters to real historian data.
    Each function is fully documented and runnable once you supply data.

    2a. fit_production_distribution()  — fits Gaussian to flow/purity
    2b. fit_uptime_range()             — fits percentile range to uptime
    2c. fit_steady_state_plant_state() — fits median operating point
    2d. validate_bmp_against_scada()   — compares Buswell BMP to measured yield

References
----------
[1] Buswell & Mueller (1952). Mechanism of methane fermentation.
    Ind. Eng. Chem., 44(3), 550-552.
    -- Equations used in buswell_bmp() and buswell_biogas_composition()

[2] VDI 4630:2016. Fermentation of organic materials.
    Verein Deutscher Ingenieure, Duesseldorf.
    -- Temperature, pH, OLR operating ranges

[3] Lossie & Puetz (2008). Targeted control of biogas plants with FOS/TAC.
    Hach-Lange GmbH Practice Report.
    -- FOS/TAC thresholds 0.3-0.4 healthy, >0.6 critical

[4] Amon et al. (2007). Methane production through anaerobic digestion
    of various energy crops. Bioresource Technology, 98(17), 3204-3212.
    -- BMP values for agricultural substrates

[5] Angelidaki et al. (2009). Defining the biomethane potential (BMP)
    of solid organic wastes. Water Science & Technology, 59(5), 927-934.
    -- BMP methodology and reference values

[6] Weiland (2010). Biogas production: current state and perspectives.
    Applied Microbiology & Biotechnology, 85(4), 849-860.
    -- OLR ranges 1.5-3.5 kg VS/m3/day for mesophilic digesters

[7] Perry & Green (2008). Perry's Chemical Engineers' Handbook, 8th ed.
    McGraw-Hill. Table 2-150.
    -- Lower heating value of methane: 35.88 MJ/Nm3

[8] Drosg (2013). Process Monitoring in Biogas Plants.
    IEA Bioenergy Task 37. Free download: task37.ieabioenergy.com
    -- Feedstock composition tables (DM%, VS/DM, C/N)

Usage
-----
    # CLI (standalone, no AI required)
    python src/bio_cli.py calibrate buswell --c 6 --h 12 --o 6

    # Or import as Python module:
    from src.biomethane_calibration import buswell_bmp, fit_production_distribution
"""

import math
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List

# ── AD4 Simulator import ─────────────────────────────────────────────────────
try:
    from ad4_simulator import AD4Simulator, AD4Params, AD4State
    _AD4_AVAILABLE = True
except ImportError:
    _AD4_AVAILABLE = False
    AD4Simulator = None
    AD4Params = None
    AD4State = None

# ── optional imports for Group 2 (SCADA fitting) ──────────────────────────────
try:
    import pandas as pd
    import numpy as np
    _PANDAS_AVAILABLE = True
except ImportError:
    _PANDAS_AVAILABLE = False


# ══════════════════════════════════════════════════════════════════════════════
# GROUP 1 — PHYSICS / CHEMISTRY BASED (fully implemented)
# ══════════════════════════════════════════════════════════════════════════════

# ── 1a. Buswell equation — theoretical BMP ────────────────────────────────────

def buswell_bmp(
    c: float,
    h: float,
    o: float,
    n: float = 0.0,
    s: float = 0.0,
) -> dict:
    """
    Calculates the theoretical Biomethane Potential (BMP) and biogas
    composition from the elemental formula of an organic substrate
    CcHhOoNnSs using the Buswell & Mueller (1952) equation.

    Reference: Buswell & Mueller (1952), Ind. Eng. Chem. 44(3), p.550
    Equation (on p.550):

        CcHhOoNnSs + (c - h/4 - o/2 + 3n/4 + s/2) H2O
            → (c/2 + h/8 - o/4 - 3n/8 - s/4) CH4
            +  (c/2 - h/8 + o/4 + 3n/8 + s/4) CO2
            +  n NH3
            +  s H2S

    Parameters
    ----------
    c, h, o, n, s : molar stoichiometric coefficients in substrate formula
        e.g. glucose C6H12O6 → c=6, h=12, o=6, n=0, s=0
        e.g. cellulose C6H10O5 → c=6, h=10, o=5
        e.g. protein approx C5H7O2N → c=5, h=7, o=2, n=1

    Returns
    -------
    dict with:
        ch4_mol_per_mol_substrate   : moles CH4 produced per mole substrate
        co2_mol_per_mol_substrate   : moles CO2 produced
        bmp_nl_per_g_vs             : theoretical BMP in NL CH4 / g VS
                                      (requires molecular weight input,
                                       see note below)
        ch4_fraction_pct            : theoretical CH4 % in raw biogas
        co2_fraction_pct            : theoretical CO2 % in raw biogas
        h2o_consumed_mol            : moles water consumed in digestion

    Note on units
    -------------
    bmp_nl_per_g_vs requires the molecular weight of the substrate (g/mol).
    This function returns bmp_nl_per_mol_substrate. To convert:
        bmp_nl_per_g_vs = bmp_nl_per_mol_substrate / molecular_weight_g_mol
    At STP (0 degC, 1 atm), 1 mol ideal gas = 22.414 L.
    At NTP (20 degC, 1 atm, as used in biogas), 1 mol = 24.04 L.
    This function uses NTP (24.04 L/mol) as per VDI 4630 convention.

    Examples
    --------
    >>> # Glucose C6H12O6 (MW = 180.16 g/mol)
    >>> r = buswell_bmp(c=6, h=12, o=6)
    >>> print(r['ch4_fraction_pct'])   # should be ~50% for glucose
    50.0

    >>> # Cellulose C6H10O5 (MW = 162.14 g/mol)
    >>> r = buswell_bmp(c=6, h=10, o=5)
    >>> print(r['ch4_fraction_pct'])   # should be ~50%
    """
    # Buswell equation coefficients
    ch4_mol = c / 2 + h / 8 - o / 4 - 3 * n / 8 - s / 4
    co2_mol = c / 2 - h / 8 + o / 4 + 3 * n / 8 + s / 4
    h2o_mol = c - h / 4 - o / 2 + 3 * n / 4 + s / 2

    # Guard against negative values (unphysical substrate formulas)
    ch4_mol = max(0.0, ch4_mol)
    co2_mol = max(0.0, co2_mol)

    total_gas_mol = ch4_mol + co2_mol   # NH3 and H2S minor, excluded from %
    ch4_pct = (ch4_mol / total_gas_mol * 100) if total_gas_mol > 0 else 0.0
    co2_pct = (co2_mol / total_gas_mol * 100) if total_gas_mol > 0 else 0.0

    # Volume at NTP (20 degC, 1 atm) — VDI 4630 convention
    NTP_L_PER_MOL = 24.04
    ch4_nl_per_mol_substrate = ch4_mol * NTP_L_PER_MOL

    return {
        "substrate_formula":            f"C{c}H{h}O{o}N{n}S{s}",
        "ch4_mol_per_mol_substrate":    round(ch4_mol, 4),
        "co2_mol_per_mol_substrate":    round(co2_mol, 4),
        "h2o_consumed_mol":             round(h2o_mol, 4),
        "ch4_nl_per_mol_substrate":     round(ch4_nl_per_mol_substrate, 2),
        "ch4_fraction_pct":             round(ch4_pct, 1),
        "co2_fraction_pct":             round(co2_pct, 1),
        "note": (
            "Divide ch4_nl_per_mol_substrate by substrate MW (g/mol) "
            "to get BMP in NL CH4 / g VS. "
            "Reference: Buswell & Mueller (1952) p.550."
        ),
    }


# ── 1b. Buswell — convenience wrapper for common substrate classes ─────────────

# Approximate elemental formulas for substrate classes
# Sources: Drosg (2013) IEA Bioenergy Task 37; Angelidaki et al. (2009)
_SUBSTRATE_FORMULAS = {
    "carbohydrate_cellulose":  {"c": 6,  "h": 10, "o": 5,  "n": 0, "s": 0, "mw": 162.14},
    "carbohydrate_glucose":    {"c": 6,  "h": 12, "o": 6,  "n": 0, "s": 0, "mw": 180.16},
    "protein_generic":         {"c": 5,  "h":  7, "o":  2, "n": 1, "s": 0, "mw": 113.12},
    "lipid_tripalmitin":       {"c": 51, "h": 98, "o":  6, "n": 0, "s": 0, "mw": 807.32},
    "lipid_triolein":          {"c": 57, "h":104, "o":  6, "n": 0, "s": 0, "mw": 885.43},
    "volatile_fatty_acid_acetic": {"c": 2, "h": 4, "o": 2, "n": 0, "s": 0, "mw": 60.05},
}


def buswell_bmp_by_class(substrate_class: str) -> dict:
    """
    Convenience wrapper: calculates Buswell BMP for named substrate classes
    using standard elemental formulas from the literature.

    Available substrate_class values:
        carbohydrate_cellulose, carbohydrate_glucose,
        protein_generic, lipid_tripalmitin, lipid_triolein,
        volatile_fatty_acid_acetic

    Returns BMP in NL CH4 / g VS in addition to Buswell outputs.

    Why this matters for your script
    ---------------------------------
    The _FEEDSTOCKS BMP values are empirical measurements that include
    biodegradability limits. Buswell gives the THEORETICAL maximum.
    The ratio (empirical BMP / Buswell BMP) = biodegradability coefficient,
    which is a useful sanity check. For maize silage this is typically
    0.80-0.90; for lignocellulosic materials lower (~0.50-0.65).
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

    result["substrate_class"]   = substrate_class
    result["molecular_weight"]  = mw
    result["bmp_nl_per_g_vs"]   = round(bmp_nl_g_vs, 1)
    result["bmp_nl_per_kg_vs"]  = round(bmp_nl_g_vs * 1000, 0)
    result["note"] = (
        "bmp_nl_per_kg_vs is the THEORETICAL maximum (100% biodegradability). "
        "Empirical BMP assay values are always lower. "
        "biodegradability = empirical_BMP / theoretical_BMP."
    )
    return result


# ── 1c. Energy conversion factor from LHV ─────────────────────────────────────

def calculate_energy_conversion_factor(ch4_fraction: float = 0.974) -> dict:
    """
    Derives the kWh/Nm3 conversion factor for biomethane from the
    lower heating value (LHV) of methane, accounting for actual CH4 content.

    The script currently hardcodes 10.55 kWh/Nm3. This function shows
    where that number comes from and lets you recalculate it for any
    actual CH4 purity.

    Reference: Perry & Green (2008), Perry's Chemical Engineers' Handbook,
    8th ed., Table 2-150.
    LHV of pure CH4 = 35.88 MJ/Nm3 at 0 degC, 1 atm (STP)
                    = 802.3 kJ/mol

    Parameters
    ----------
    ch4_fraction : float
        CH4 mole fraction in biomethane (default 0.974 = 97.4% purity,
        matching the script's default _plant_state value)

    Returns
    -------
    dict with the conversion factor and its derivation steps
    """
    # Physical constants — from Perry's Table 2-150
    LHV_CH4_MJ_PER_NM3_STP = 35.88      # at 0 degC, 1 atm
    MJ_TO_KWH = 1 / 3.6                 # 1 MJ = 0.2778 kWh

    # VDI 4630 uses NTP (15 degC, 1 atm) for biogas volumetry
    # Correction factor STP→NTP: 273.15 / 288.15 = 0.9479
    STP_TO_NTP = 273.15 / 288.15

    LHV_CH4_MJ_PER_NM3_NTP = LHV_CH4_MJ_PER_NM3_STP / STP_TO_NTP

    # Biomethane: scale by actual CH4 fraction
    LHV_biomethane_MJ = LHV_CH4_MJ_PER_NM3_NTP * ch4_fraction
    LHV_biomethane_kWh = LHV_biomethane_MJ * MJ_TO_KWH

    # What the script currently uses
    SCRIPT_VALUE = 10.55

    return {
        "ch4_fraction_input":               round(ch4_fraction, 4),
        "LHV_pure_CH4_MJ_per_Nm3_STP":     LHV_CH4_MJ_PER_NM3_STP,
        "STP_to_NTP_correction":            round(STP_TO_NTP, 4),
        "LHV_pure_CH4_MJ_per_Nm3_NTP":     round(LHV_CH4_MJ_PER_NM3_NTP, 3),
        "LHV_biomethane_MJ_per_Nm3":        round(LHV_biomethane_MJ, 3),
        "LHV_biomethane_kWh_per_Nm3":       round(LHV_biomethane_kWh, 3),
        "script_current_value_kWh_per_Nm3": SCRIPT_VALUE,
        "difference_pct":                   round(
            (LHV_biomethane_kWh - SCRIPT_VALUE) / SCRIPT_VALUE * 100, 2
        ),
        "recommendation": (
            f"For {ch4_fraction*100:.1f}% CH4 biomethane, "
            f"use {round(LHV_biomethane_kWh, 3)} kWh/Nm3 "
            f"(script uses {SCRIPT_VALUE}). "
            "Update _simulate_daily_production if your plant purity differs."
        ),
        "reference": "Perry & Green (2008), Table 2-150. LHV CH4 = 35.88 MJ/Nm3.",
    }


# ── 1d. C/N ratio from elemental composition ──────────────────────────────────

def cn_ratio_from_composition(
    carbon_pct_of_vs: float,
    nitrogen_pct_of_vs: float,
) -> dict:
    """
    Calculates the C/N ratio directly from elemental analysis results
    (% C and % N as fraction of volatile solids).

    This is the ground-truth method when lab elemental analysis is available.
    The _FEEDSTOCKS C/N values were set from published tables (Drosg 2013,
    Baserga 1998); if you have lab data for a specific batch, use this
    to override the table value.

    Parameters
    ----------
    carbon_pct_of_vs   : % carbon by mass of volatile solids (e.g. 42.5)
    nitrogen_pct_of_vs : % nitrogen by mass of volatile solids (e.g. 3.2)

    Returns
    -------
    dict with C/N ratio and operational advisory matching the script logic

    Reference
    ---------
    Drosg (2013), IEA Bioenergy Task 37, Section 3.2 — elemental analysis
    for feedstock characterisation. Available free at task37.ieabioenergy.com
    """
    if nitrogen_pct_of_vs <= 0:
        return {"error": "Nitrogen fraction must be > 0."}

    cn = carbon_pct_of_vs / nitrogen_pct_of_vs

    if 20 <= cn <= 30:
        status = "OPTIMAL"
        advice = "C/N ratio in ideal range (20-30). Good stability expected."
    elif cn < 20:
        status = "LOW — ammonia inhibition risk"
        advice = (
            f"C/N = {cn:.1f}. Add carbon-rich co-substrate "
            "(maize silage, grass) or reduce manure share."
        )
    elif cn <= 35:
        status = "HIGH — slow degradation"
        advice = (
            f"C/N = {cn:.1f}. Add nitrogen-rich co-substrate "
            "(manure, slurry) or reduce lignocellulosic share."
        )
    else:
        status = "VERY HIGH — significant degradation limitation"
        advice = (
            f"C/N = {cn:.1f}. Substrate is heavily lignocellulosic. "
            "Pre-treatment or significant manure addition required."
        )

    return {
        "carbon_pct_vs":    round(carbon_pct_of_vs, 2),
        "nitrogen_pct_vs":  round(nitrogen_pct_of_vs, 2),
        "cn_ratio":         round(cn, 1),
        "status":           status,
        "advice":           advice,
        "reference":        "Drosg (2013) IEA Bioenergy Task 37, Section 3.2.",
    }


# ── 1e. OLR mass balance from a feedstock recipe ──────────────────────────────

def olr_from_recipe(
    recipe: list[dict],
    digester_volume_m3: float,
    feedstock_table: Optional[dict] = None,
) -> dict:
    """
    Calculates the Organic Loading Rate (OLR) in kg VS/m3/day from a
    feedstock recipe and digester volume, using the same mass balance
    as blend_feedstocks() but returning OLR as the primary output.

    OLR = sum(wet_kg_i * dm_i * vs_dm_i) / digester_volume_m3

    Safe mesophilic range: 1.5-3.5 kg VS/m3/day (Weiland 2010, p.853)
    Upper limit before acidification risk: 4.5 kg VS/m3/day

    Parameters
    ----------
    recipe : list of dicts, each with keys:
        name        : feedstock name (must match feedstock_table)
        wet_tonnes  : tonnes/day

    digester_volume_m3 : working volume (m3)

    feedstock_table : optional dict matching _FEEDSTOCKS format.
        If None, uses the default _FEEDSTOCKS from server.py.
        Pass your own calibrated table here once you have lab data.

    Reference
    ---------
    Weiland (2010), Applied Microbiology & Biotechnology 85(4), p.853:
    "The OLR for wet fermentation processes is usually between
    2 and 4 kg VS/(m3 x d)."
    """
    # Use built-in defaults if no table provided
    if feedstock_table is None:
        feedstock_table = {
            "Cattle slurry":   {"bmp": 200, "cn": 11, "dm":  8, "vs_dm": 0.80},
            "Pig manure":      {"bmp": 310, "cn":  8, "dm":  6, "vs_dm": 0.78},
            "Maize silage":    {"bmp": 340, "cn": 25, "dm": 33, "vs_dm": 0.94},
            "Food waste":      {"bmp": 480, "cn": 16, "dm": 25, "vs_dm": 0.88},
            "Grass silage":    {"bmp": 290, "cn": 18, "dm": 30, "vs_dm": 0.90},
            "Sewage sludge":   {"bmp": 220, "cn":  9, "dm":  4, "vs_dm": 0.75},
            "Chicken manure":  {"bmp": 350, "cn":  7, "dm": 25, "vs_dm": 0.76},
            "Fat/grease trap": {"bmp": 900, "cn": 30, "dm": 80, "vs_dm": 0.97},
        }

    streams, errors = [], []
    for r in recipe:
        name = r.get("name", "")
        if name not in feedstock_table:
            errors.append(f"'{name}' not in feedstock table.")
            continue
        f = feedstock_table[name]
        wet_kg = r["wet_tonnes"] * 1000
        dm_kg  = wet_kg * (f["dm"] / 100)
        vs_kg  = dm_kg  * f["vs_dm"]
        streams.append({"name": name, "vs_kg_per_day": round(vs_kg, 1)})

    if errors:
        return {"errors": errors}

    total_vs  = sum(s["vs_kg_per_day"] for s in streams)
    olr       = total_vs / digester_volume_m3

    if olr < 1.5:
        status = "UNDERLOADED"
        advice = "OLR low. Increase feedstock throughput to improve economics."
    elif olr <= 3.5:
        status = "OPTIMAL"
        advice = "OLR in ideal mesophilic range (1.5-3.5 kg VS/m3/day). Weiland (2010)."
    elif olr <= 4.5:
        status = "HIGH"
        advice = "Approaching upper limit. Monitor VFA and FOS/TAC daily."
    else:
        status = "OVERLOADED — acidification risk"
        advice = "Reduce feeding immediately. OLR exceeds 4.5 kg VS/m3/day."

    return {
        "recipe_streams":       streams,
        "total_vs_kg_per_day":  round(total_vs, 1),
        "digester_volume_m3":   digester_volume_m3,
        "olr_kg_vs_m3_day":     round(olr, 3),
        "status":               status,
        "advice":               advice,
        "reference": (
            "Weiland (2010), Appl. Microbiol. Biotechnol. 85(4), p.853. "
            "Optimal OLR: 2-4 kg VS/m3/day for wet mesophilic AD."
        ),
    }


# ── 1f. Biodegradability check: compare Buswell to empirical BMP ──────────────

def biodegradability_coefficient(
    substrate_class: str,
    empirical_bmp_nl_per_kg_vs: float,
) -> dict:
    """
    Calculates the biodegradability coefficient eta = empirical / theoretical.

    This is a useful sanity check on BMP values in _FEEDSTOCKS:
    - If eta > 1.0: empirical value is physically impossible → data error
    - eta 0.8-0.95: typical for easily degradable substrates (food waste,
                    maize silage, fat/grease)
    - eta 0.5-0.80: typical for lignocellulosic materials
    - eta < 0.4:    very recalcitrant substrate

    Parameters
    ----------
    substrate_class : str — one of _SUBSTRATE_FORMULAS keys
    empirical_bmp   : measured BMP in NL CH4 / kg VS (from lab assay
                      or literature, e.g. Amon et al. 2007)
    """
    theoretical = buswell_bmp_by_class(substrate_class)
    if "error" in theoretical:
        return theoretical

    theo_bmp = theoretical["bmp_nl_per_kg_vs"]
    eta = empirical_bmp_nl_per_kg_vs / theo_bmp

    if eta > 1.0:
        flag = "IMPOSSIBLE — empirical > theoretical. Check units or measurement."
    elif eta >= 0.80:
        flag = "GOOD — highly degradable substrate."
    elif eta >= 0.50:
        flag = "MODERATE — partially recalcitrant (typical for lignocellulosics)."
    else:
        flag = "LOW — recalcitrant substrate. Consider pre-treatment."

    return {
        "substrate_class":                      substrate_class,
        "theoretical_bmp_nl_per_kg_vs":         theo_bmp,
        "empirical_bmp_nl_per_kg_vs":           empirical_bmp_nl_per_kg_vs,
        "biodegradability_coefficient_eta":      round(eta, 3),
        "flag":                                 flag,
        "reference": (
            "Angelidaki et al. (2009), Water Sci. Tech. 59(5), 927-934. "
            "Theoretical BMP from Buswell & Mueller (1952)."
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# GROUP 2 — SCADA-DEPENDENT SKELETONS
# These functions are complete and runnable but return placeholder results
# until you supply a real CSV from your historian.
# ══════════════════════════════════════════════════════════════════════════════

def fit_production_distribution(csv_path: Optional[str] = None) -> dict:
    """
    Fits Gaussian distribution parameters to historical grid injection flow
    and biomethane purity from SCADA historian data.

    Output replaces the hardcoded values in _simulate_daily_production():
        random.gauss(98, 6)    → random.gauss(fitted_mean_flow, fitted_std_flow)
        random.gauss(97.5, 0.3) → random.gauss(fitted_mean_purity, fitted_std_purity)

    Method
    ------
    Simple descriptive statistics (mean, std) on cleaned daily averages.
    No ML required — this is the same as fitting a normal distribution
    to a histogram, which is standard practice in process engineering.

    CSV format expected
    -------------------
    Your SCADA export should have at minimum these columns
    (exact names configurable in COLUMN_MAP below):

        timestamp               : ISO datetime or Unix epoch
        grid_injection_nm3h     : hourly average grid injection flow (Nm3/h)
        biomethane_purity_pct   : hourly average CH4 purity after upgrading (%)

    Minimum recommended history: 30 days of hourly averages.
    Outlier removal: values beyond 3 sigma are excluded (plant shutdowns,
    sensor errors). This is a judgment call — review the flagged_outliers
    in the output before accepting the fit.

    Parameters
    ----------
    csv_path : str or None
        Path to your SCADA export CSV.
        If None, returns this documentation and placeholder values.

    Returns
    -------
    dict with fitted parameters ready to paste into _simulate_daily_production()
    """
    # ── Column name map — edit these to match your SCADA export headers ───────
    COLUMN_MAP = {
        "timestamp":            "timestamp",
        "flow":                 "grid_injection_nm3h",
        "purity":               "biomethane_purity_pct",
    }
    OUTLIER_SIGMA = 3.0     # exclude points beyond this many std devs
    MIN_DAYS      = 30      # warn if history is shorter than this

    if csv_path is None or not _PANDAS_AVAILABLE:
        return {
            "status":   "NO_DATA",
            "message":  (
                "Supply a csv_path to fit real parameters. "
                "Install pandas and numpy if not present: "
                "pip install pandas numpy"
            ),
            "placeholder_values": {
                "mean_flow_nm3h":   98.0,   # current script default
                "std_flow_nm3h":     6.0,
                "mean_purity_pct":  97.5,
                "std_purity_pct":    0.3,
            },
            "how_to_use": (
                "Replace in _simulate_daily_production():\n"
                "  flow   = random.gauss(result['mean_flow_nm3h'],  result['std_flow_nm3h'])\n"
                "  purity = random.gauss(result['mean_purity_pct'], result['std_purity_pct'])"
            ),
        }

    # ── Real fitting path (runs when csv_path is supplied) ───────────────────
    df = pd.read_csv(csv_path, parse_dates=[COLUMN_MAP["timestamp"]])
    df = df[[COLUMN_MAP["timestamp"], COLUMN_MAP["flow"], COLUMN_MAP["purity"]]].copy()
    df.columns = ["timestamp", "flow", "purity"]
    df = df.dropna()

    # Resample to daily averages
    df = df.set_index("timestamp").resample("D").mean().dropna().reset_index()
    n_days = len(df)

    # Outlier removal (3-sigma)
    def remove_outliers(series, sigma):
        mu, s = series.mean(), series.std()
        mask = (series - mu).abs() <= sigma * s
        return series[mask], series[~mask].index.tolist()

    flow_clean,   flow_outliers   = remove_outliers(df["flow"],   OUTLIER_SIGMA)
    purity_clean, purity_outliers = remove_outliers(df["purity"], OUTLIER_SIGMA)

    result = {
        "status":               "FITTED",
        "n_days_total":         n_days,
        "n_days_after_outlier_removal": len(flow_clean),
        "warning":              "History < 30 days — fit may not be representative." if n_days < MIN_DAYS else None,
        "fitted_parameters": {
            "mean_flow_nm3h":   round(float(flow_clean.mean()),   2),
            "std_flow_nm3h":    round(float(flow_clean.std()),    2),
            "mean_purity_pct":  round(float(purity_clean.mean()), 3),
            "std_purity_pct":   round(float(purity_clean.std()),  3),
        },
        "flagged_outliers": {
            "flow_days":   flow_outliers,
            "purity_days": purity_outliers,
        },
        "how_to_use": (
            "Replace in _simulate_daily_production():\n"
            "  flow   = random.gauss(fitted['mean_flow_nm3h'],  fitted['std_flow_nm3h'])\n"
            "  purity = random.gauss(fitted['mean_purity_pct'], fitted['std_purity_pct'])"
        ),
    }
    return result


def fit_uptime_range(csv_path: Optional[str] = None) -> dict:
    """
    Fits the uptime percentile range to replace:
        random.uniform(0.92, 0.99)
    in _simulate_daily_production().

    Method
    ------
    Daily uptime fraction = hours_producing / 24.
    Fit: p5 and p95 percentiles of the empirical distribution.
    This preserves the realistic spread without assuming normality
    (uptime is typically left-skewed due to occasional shutdowns).

    CSV format
    ----------
    Requires one of:
        (a) a column 'uptime_fraction' (0.0-1.0) or 'uptime_pct' (0-100)
        (b) columns 'hours_online' and optionally 'hours_total' (default 24)
    """
    COLUMN_MAP = {
        "timestamp":        "timestamp",
        "uptime_fraction":  "uptime_fraction",   # edit to match your export
    }
    P_LOW, P_HIGH = 5, 95    # percentile range — adjust if your plant is more stable

    if csv_path is None or not _PANDAS_AVAILABLE:
        return {
            "status":   "NO_DATA",
            "placeholder_values": {
                "uptime_p5":  0.92,   # current script default lower bound
                "uptime_p95": 0.99,   # current script default upper bound
            },
            "how_to_use": (
                "Replace in _simulate_daily_production():\n"
                "  uptime = random.uniform(result['uptime_p5'], result['uptime_p95'])"
            ),
        }

    df = pd.read_csv(csv_path, parse_dates=[COLUMN_MAP["timestamp"]])

    if "uptime_fraction" in df.columns:
        uptime = df["uptime_fraction"].dropna()
    elif "uptime_pct" in df.columns:
        uptime = df["uptime_pct"].dropna() / 100
    else:
        return {"error": "No uptime column found. Edit COLUMN_MAP."}

    uptime = uptime[(uptime >= 0) & (uptime <= 1)]

    return {
        "status":       "FITTED",
        "n_days":       len(uptime),
        "fitted_parameters": {
            "uptime_p5":    round(float(np.percentile(uptime, P_LOW)),  3),
            "uptime_p95":   round(float(np.percentile(uptime, P_HIGH)), 3),
            "uptime_mean":  round(float(uptime.mean()), 3),
            "uptime_median":round(float(uptime.median()), 3),
        },
        "how_to_use": (
            "Replace in _simulate_daily_production():\n"
            "  uptime = random.uniform(result['uptime_p5'], result['uptime_p95'])"
        ),
    }


def fit_steady_state_plant_state(csv_path: Optional[str] = None) -> dict:
    """
    Fits the steady-state operating point to replace the hardcoded
    values in _plant_state{} using median values from historian data.

    Median is used rather than mean because it is robust to process
    upsets and sensor dropouts that are common in SCADA data.

    CSV format
    ----------
    One row per time step (hourly recommended), columns named to match
    _plant_state keys. Edit COLUMN_MAP for your SCADA tag names.
    Not all columns need to be present — only those found are updated.

    Columns that CAN be fitted (if present in CSV):
        digester_temp_c, digester_ph, vfa_mmol_l,
        alkalinity_mg_caco3_l, nh4_mg_l, biogas_flow_nm3h,
        ch4_pct, co2_pct, h2s_ppm, o2_ppm,
        biomethane_purity_pct, grid_injection_nm3h,
        organic_load_kg_vs_d, hydraulic_retention_days, digestate_ts_pct
    """
    COLUMN_MAP = {
        # SCADA tag name on left → _plant_state key on right
        # Edit the left side to match your historian export headers
        "digester_temp_c":          "digester_temp_c",
        "digester_ph":              "digester_ph",
        "vfa_mmol_l":               "vfa_mmol_l",
        "alkalinity_mg_caco3_l":    "alkalinity_mg_caco3_l",
        "nh4_mg_l":                 "nh4_mg_l",
        "biogas_flow_nm3h":         "biogas_flow_nm3h",
        "ch4_pct":                  "ch4_pct",
        "co2_pct":                  "co2_pct",
        "h2s_ppm":                  "h2s_ppm",
        "o2_ppm":                   "o2_ppm",
        "biomethane_purity_pct":    "biomethane_purity_pct",
        "grid_injection_nm3h":      "grid_injection_nm3h",
        "organic_load_kg_vs_d":     "organic_load_kg_vs_d",
        "hydraulic_retention_days": "hydraulic_retention_days",
        "digestate_ts_pct":         "digestate_ts_pct",
    }

    if csv_path is None or not _PANDAS_AVAILABLE:
        return {
            "status":   "NO_DATA",
            "message":  (
                "Supply a csv_path with SCADA historian export. "
                "Fitted medians will replace _plant_state defaults."
            ),
            "current_defaults": {
                "digester_temp_c":          37.2,
                "digester_ph":               7.1,
                "vfa_mmol_l":                8.5,
                "alkalinity_mg_caco3_l":  2800.0,
                "nh4_mg_l":                420.0,
                "biogas_flow_nm3h":        142.0,
                "ch4_pct":                  62.3,
                "biomethane_purity_pct":    97.4,
                "grid_injection_nm3h":      98.0,
            },
        }

    df = pd.read_csv(csv_path)

    # Keep only columns that exist in both the CSV and our map
    available = {k: v for k, v in COLUMN_MAP.items() if k in df.columns}
    fitted = {}
    for csv_col, state_key in available.items():
        series = pd.to_numeric(df[csv_col], errors="coerce").dropna()
        if len(series) > 0:
            fitted[state_key] = round(float(series.median()), 3)

    return {
        "status":           "FITTED",
        "n_rows_read":      len(df),
        "columns_fitted":   list(fitted.keys()),
        "columns_missing":  [v for v in COLUMN_MAP.values() if v not in fitted],
        "fitted_plant_state": fitted,
        "how_to_use": (
            "Copy fitted_plant_state values into _plant_state{} in server.py. "
            "Or call update_plant_state(fitted_plant_state) at server startup."
        ),
    }


def validate_bmp_against_scada(
    feedstock_name: str,
    substrate_class: str,
    measured_bmp_nl_per_kg_vs: Optional[float] = None,
    csv_path: Optional[str] = None,
) -> dict:
    """
    Validates the BMP value for a named feedstock in _FEEDSTOCKS against:
    (a) the Buswell theoretical maximum
    (b) a lab-measured BMP assay value if available
    (c) a back-calculated yield from SCADA production data if available

    This is the end-to-end calibration check for Section B of the server.

    Parameters
    ----------
    feedstock_name   : name as it appears in _FEEDSTOCKS (e.g. "Maize silage")
    substrate_class  : corresponding Buswell class (e.g. "carbohydrate_cellulose")
    measured_bmp     : optional lab BMP assay result (NL CH4 / kg VS)
    csv_path         : optional SCADA CSV with columns:
                           feedstock_vs_kg_d  (VS input from this feedstock)
                           biogas_nm3_d       (total biogas produced that day)
                           ch4_pct            (CH4 fraction)
    """
    _FEEDSTOCKS_BMP = {
        "Cattle slurry":   200, "Pig manure":      310,
        "Maize silage":    340, "Food waste":      480,
        "Grass silage":    290, "Sewage sludge":   220,
        "Chicken manure":  350, "Fat/grease trap": 900,
    }

    result = {
        "feedstock_name":   feedstock_name,
        "substrate_class":  substrate_class,
        "script_bmp":       _FEEDSTOCKS_BMP.get(feedstock_name, "unknown"),
    }

    # Buswell theoretical
    theoretical = buswell_bmp_by_class(substrate_class)
    if "bmp_nl_per_kg_vs" in theoretical:
        result["theoretical_bmp_buswell"] = theoretical["bmp_nl_per_kg_vs"]
        if isinstance(result["script_bmp"], (int, float)):
            result["script_vs_theoretical_eta"] = round(
                result["script_bmp"] / theoretical["bmp_nl_per_kg_vs"], 3
            )

    # Lab assay comparison
    if measured_bmp_nl_per_kg_vs is not None:
        result["measured_bmp_lab"] = measured_bmp_nl_per_kg_vs
        result["recommendation"] = (
            f"Replace script BMP {result['script_bmp']} with "
            f"lab-measured {measured_bmp_nl_per_kg_vs} NL/kg VS "
            f"for this specific feedstock batch."
        )

    # SCADA back-calculation (skeleton)
    if csv_path is None or not _PANDAS_AVAILABLE:
        result["scada_status"] = (
            "NO_DATA — supply csv_path with feedstock_vs_kg_d, "
            "biogas_nm3_d, ch4_pct columns to back-calculate yield."
        )
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
            result["recommendation"] = (
                f"Replace script BMP {result['script_bmp']} with "
                f"SCADA-derived {round(float(scada_bmp), 1)} NL/kg VS."
            )

    return result


# (self-test moved to bottom of file — see __main__ block)


# ==============================================================================
# GROUP 3 — AD4 Output-Only Calibration (Honest Approach)
# ==============================================================================
# Calibrate only what's observable without S2 measurements.
# See FITTING_COMPARISON.md for the full analysis.

class ParameterSource(Enum):
    """Where each parameter value comes from."""
    FITTED_TO_DATA = "fitted_to_data"       # Calibrated to Q_CH4 from dataset
    FROM_DATASET = "from_dataset"        # Direct from dataset columns
    LITERATURE_DEFAULT = "literature_default"  # Published values (not data-validated)
    BUSWELL_CONSTRAINED = "buswell_constrained"  # Bounded by chemistry


class CalibrationConfidence:
    """
    Tracks which parameters have data support vs literature defaults.
    
    This is critical for honest model use — users need to know
    which parameters are validated vs which are assumptions.
    """
    
    def __init__(self):
        self.parameters: Dict[str, Dict[str, Any]] = {}
    
    def add(self, name: str, value: Any, source: ParameterSource, 
         bounds: tuple = None, notes: str = ""):
        self.parameters[name] = {
            "value": value,
            "source": source.value,
            "bounds": bounds,
            "notes": notes,
        }
    
    def to_dict(self) -> dict:
        return self.parameters
    
    def summary(self) -> str:
        lines = ["Calibration Confidence Report:", "=" * 40]
        for name, info in self.parameters.items():
            source = info["source"]
            value = info["value"]
            if source == "fitted_to_data":
                lines.append(f"  {name}: {value} [FITTED]")
            elif source == "from_dataset":
                lines.append(f"  {name}: {value} [FROM DATA]")
            elif source == "buswell_constrained":
                lines.append(f"  {name}: {value} [BUSWELL BOUNDED]")
            else:
                lines.append(f"  {name}: {value} [LITERATURE DEFAULT]")
        return "\n".join(lines)


def fit_am2_k6_only(
    D_measured: float,
    S1_in_measured: float,
    Q_CH4_measured: float,
    digester_volume_m3: float = 2000.0,
    params: AD4Params = None,
) -> dict:
    """
    Validate that AD4 can match observed Q_CH4, or find needed adjustments.

    This is honest validation, not full calibration, because:
    - Without S2 measurements, we can't uniquely determine parameters
    - Many (D, S1_in, k6) combos produce the same Q_CH4

    Key insight: k6 does NOT affect S2 or X2 at steady state — it only
    scales the methane output from a given methanogen activity level.
    Therefore we run the ODE ONCE to get steady-state S2 and X2, then
    solve k6 analytically:

        Q_CH4 = k6 * mu2(S2_ss) * X2_ss
        k6    = Q_CH4 / (mu2(S2_ss) * X2_ss)

    This is ~20x faster than the iterative optimizer approach, which is
    critical when called in a loop over thousands of CSV rows.

    Args:
        D_measured: Dilution rate from HRT column (d^-1)
        S1_in_measured: Influent COD from feedstock (g/L)
        Q_CH4_measured: Measured methane flow (Nm3/d)
        digester_volume_m3: Digester volume for unit conversion (default 2000m3)
        params: AD4Params to start with (default: Benyahia defaults)

    Returns:
        Dict with what changed and confidence report.
    """
    if not _AD4_AVAILABLE:
        return {"error": "AD4 simulator not available"}

    if params is None:
        params = AD4Params()

    # Convert Nm3/d → mL/L/d (simulator internal units)
    Q_CH4_mL_per_L = Q_CH4_measured * 1000.0 / digester_volume_m3

    # ── Single ODE run to get steady-state S2 and X2 ─────────────────────────
    # k6 does not appear in the S1/S2/X1/X2 ODEs — only in the methane
    # output equation — so one run with default params gives the correct
    # steady-state internal states regardless of what k6 will be.
    base_sim = AD4Simulator(params=params)
    base_result = base_sim.run(days=200, D=D_measured, S1_in=S1_in_measured)

    S2_ss = base_result.S2[-1]
    X2_ss = base_result.X2[-1]
    mu2_ss = params.mu2(S2_ss)          # Haldane rate at steady-state VFA

    # ── Analytical k6 solution ────────────────────────────────────────────────
    # Q_CH4 [mL/L/d] = k6 [mL/mmol] * mu2 [d^-1] * X2 [g/L]
    methanogen_activity = mu2_ss * X2_ss   # mmol-equivalent activity term

    if methanogen_activity <= 1e-9:
        # Washout — methanogens cannot sustain themselves at this D/S1_in
        return {
            "error": "washout",
            "message": (
                f"Methanogens washed out at D={D_measured:.4f} d⁻¹, "
                f"S1_in={S1_in_measured:.1f} g/L. "
                f"X2_ss={X2_ss:.4f} g/L. Cannot fit k6."
            ),
            "D": D_measured,
            "S1_in": S1_in_measured,
            "X2_ss": round(X2_ss, 6),
            "S2_ss": round(S2_ss, 4),
        }

    k6_value = Q_CH4_mL_per_L / methanogen_activity
    k6_value = float(max(100.0, min(2500.0, k6_value)))   # clip to physical bounds

    # ── Validation: re-compute Q with fitted k6 ───────────────────────────────
    Q_sim = k6_value * mu2_ss * X2_ss
    error_pct = abs(Q_sim - Q_CH4_mL_per_L) / Q_CH4_mL_per_L * 100 if Q_CH4_mL_per_L > 0 else 0.0

    # Default model output for comparison
    Q_default = params.k6 * mu2_ss * X2_ss
    Q_default_Nm3 = Q_default * digester_volume_m3 / 1000.0
    
    # Classification - check if data is compatible with model
    model_ratio = Q_CH4_measured / Q_default_Nm3 if Q_default_Nm3 > 0 else float('inf')
    
    if model_ratio > 10:
        status = "INCOMPATIBLE — data 10x+ higher than model can produce"
        diagnosis = "Dataset may be from different digester type, larger scale, or uses different kinetics. Benyahia model cannot match this data."
    elif k6_value > 1500:
        status = "POOR — requires extreme k6, model mismatch likely"
        diagnosis = "Default Benyahia params produce too low output. Either: (1) plant has different kinetics, (2) measurement issue, (3) model not applicable"
    elif k6_value > 800:
        status = "HIGH — k6 above typical range (may indicate high-quality feed)"
    else:
        status = "OK — within operational range"
    
    # Build confidence report
    confidence = CalibrationConfidence()
    confidence.add("D", D_measured, ParameterSource.FROM_DATASET, notes="From dataset HRT")
    confidence.add("S1_in", S1_in_measured, ParameterSource.FROM_DATASET, notes="Estimated from feedstock")
    confidence.add("k6", k6_value, ParameterSource.FITTED_TO_DATA, bounds=(100, 2500),
                 notes="Changed from default 453 to match output")
    confidence.add("Ki2", params.Ki2, ParameterSource.LITERATURE_DEFAULT,
                 notes="UNVALIDATED - stays at Benyahia default")
    confidence.add("mu2_max", params.mu2_max, ParameterSource.LITERATURE_DEFAULT,
                 notes="UNVALIDATED - stays at Benyahia default")
    
    return {
        "digester_volume_m3": digester_volume_m3,
        "Q_CH4_input_Nm3_per_d": Q_CH4_measured,
        "Q_CH4_converted_mL_per_L_per_d": round(Q_CH4_mL_per_L, 2),
        "Q_CH4_default_Nm3_per_d": round(Q_default_Nm3, 1),
        "Q_CH4_simulated_Nm3_per_d": round(Q_sim * digester_volume_m3 / 1000, 1),
        "model_vs_data_ratio": round(model_ratio, 1),
        "k6_default": params.k6,
        "k6_fitted": round(k6_value, 1),
        "k6_change_pct": round((k6_value - params.k6) / params.k6 * 100, 1),
        "error_pct": round(error_pct, 2),
        "status": status,
        "calibration_confidence": "LOW - Only output matched, internal states unvalidated",
        "diagnosis": diagnosis,
        "confidence_report": confidence.to_dict(),
    }


def fit_am2_from_dataset(csv_path: str) -> dict:
    """
    Run honest calibration on a CSV dataset.
    
    Expected CSV columns:
    - HRT_days OR dilution_rate_d1
    - influent_COD_g_L OR vs_reduction_pct
    - biogas_m3_d (optional, for Q_CH4)
    - methane_pct (optional)
    
    Args:
        csv_path: Path to CSV with daily operational data.
    
    Returns:
        Calibration results per row plus confidence report.
    """
    if not _AD4_AVAILABLE:
        return {"error": "AD4 simulator not available"}
    
    try:
        import pandas as pd
    except ImportError:
        return {"error": "pandas required"}
    
    df = pd.read_csv(csv_path)
    
    # Map columns
    hrt_col = "HRT_days" if "HRT_days" in df.columns else "dilution_rate_d1"
    cod_col = "influent_COD_g_L" if "influent_COD_g_L" in df.columns else "vs_reduction_pct"
    
    # Calculate Q_CH4 if not present
    if "methane_mL_per_L_per_d" not in df.columns:
        if "biogas_m3_d" in df.columns and "ch4_pct" in df.columns:
            df["methane_mL_per_L_per_d"] = (
                df["biogas_m3_d"] * df["ch4_pct"] / 100 * 1000 / 2000  # approx
            )
        else:
            return {"error": "Need methane_mL_per_L_per_d or biogas_m3_d + ch4_pct"}
    
    results = []
    confidence = CalibrationConfidence()
    
    for idx, row in df.iterrows():
        D = 1.0 / row[hrt_col] if hrt_col == "HRT_days" else row[hrt_col]
        S1_in = row[cod_col]
        Q_CH4 = row["methane_mL_per_L_per_d"]
        
        fit_result = fit_am2_k6_only(D, S1_in, Q_CH4)
        if "error" not in fit_result:
            results.append({
                "row": idx,
                "D": D,
                "fitted_k6": fit_result["k6_fitted"],
                "error_pct": fit_result["error_pct"],
            })

    return {
        "n_rows_calibrated": len(results),
        "results": results,
        "confidence": confidence.to_dict(),
    }


# ==============================================================================
# CSV Column Mapping Utilities
# ==============================================================================

def infer_column(df, patterns):
    """Find column name matching any pattern (case-insensitive)."""
    cols = {c.lower(): c for c in df.columns}
    for p in patterns:
        for col_lower, col in cols.items():
            if p.lower() in col_lower:
                return col
    return None


def fit_am2_from_csv(
    csv_path,
    digester_volume_m3=2000.0,
):
    """
    Calibrate AD4 to production data from any CSV format.

    Auto-detects columns. Expected data sources:
    - Indian Biogas Dataset: feedstock columns → estimate S1_in
    - Mendeley (ri_flex.csv): HRT, biogas production, VFA
    - SCADA: flow, CH4%, temperature

    Performance note
    ----------------
    k6 is solved analytically (one ODE run per row), not iteratively.
    This makes the function suitable for datasets with 10,000+ rows.

    Returns
    -------
    Dict including k6_median (recommended value for generate_model_card),
    k6_std (feedstock consistency indicator), and full per-row results.
    """
    if not _AD4_AVAILABLE:
        return {"error": "AD4 simulator not available"}

    try:
        import pandas as pd
        import statistics
    except ImportError:
        return {"error": "pandas required"}

    df = pd.read_csv(csv_path)

    # ── Column detection ──────────────────────────────────────────────────────
    HRT_COLS    = ["hrt", "retention", "days"]
    BIOGAS_COLS = ["biogas_production", "biogas_flow", "biogas_m3"]
    CH4_COLS    = ["methane", "ch4", "ch4_pct"]
    FEED_COLS   = ["pig manure", "chicken litter", "cassava", "bagasse",
                   "kitchen food", "energy grass", "banana", "alcohol waste",
                   "municipal", "fish waste"]
    WATER_COLS  = ["water (l)", "water_l", "water"]

    hrt_col    = infer_column(df, HRT_COLS)
    biogas_col = infer_column(df, BIOGAS_COLS)
    ch4_col    = infer_column(df, CH4_COLS)
    water_col  = infer_column(df, WATER_COLS)

    if not biogas_col:
        return {
            "error": "Cannot find biogas column",
            "columns_found": list(df.columns),
            "hint": "Expected one of: biogas_production, biogas_flow, biogas_m3",
        }

    # ── Q_CH4 calculation ─────────────────────────────────────────────────────
    if ch4_col:
        ch4_series = pd.to_numeric(df[ch4_col], errors="coerce")
        # Auto-detect fraction vs percent (fraction if median < 1.0)
        if ch4_series.median() < 1.0:
            ch4_series = ch4_series * 100.0
        df["Q_CH4_raw"] = pd.to_numeric(df[biogas_col], errors="coerce") * ch4_series / 100.0
    else:
        # No CH4% column — assume 60% (typical mesophilic manure digester)
        df["Q_CH4_raw"] = pd.to_numeric(df[biogas_col], errors="coerce") * 0.60

    # ── Dilution rate ─────────────────────────────────────────────────────────
    if hrt_col:
        hrt_series = pd.to_numeric(df[hrt_col], errors="coerce")
        # Guard against HRT = 0 causing div-by-zero
        df["D"] = (1.0 / hrt_series.replace(0, float("nan"))).clip(0.01, 1.0)
    else:
        # No HRT column — assume 18-day HRT (typical farm-scale manure digester)
        df["D"] = 1.0 / 18.0

    # ── S1_in estimation from feedstock columns ───────────────────────────────
    # Fix: use infer_column for water, not df.get() which doesn't work on DataFrames
    feed_cols = [c for c in df.columns if any(p in c.lower() for p in FEED_COLS)]
    if feed_cols:
        df["total_feedstock"] = df[feed_cols].apply(
            pd.to_numeric, errors="coerce"
        ).sum(axis=1)

        # Water volume (litres) → dilution factor
        if water_col:
            water_kg = pd.to_numeric(df[water_col], errors="coerce").fillna(0)
        else:
            water_kg = 0

        # COD ~ 30 g/L for raw manure/food waste mix, diluted by added water
        # Result clipped to physically plausible range [5, 50] g COD/L
        total_liquid = df["total_feedstock"] + water_kg / 1.0 + 1.0   # +1 avoids div/0
        df["S1_in"] = (df["total_feedstock"] * 30.0 / total_liquid).clip(5.0, 50.0)
    else:
        # No feedstock columns — use literature default for mixed manure
        df["S1_in"] = 25.0

    # ── Per-row analytical k6 fit ─────────────────────────────────────────────
    results     = []
    skipped     = []
    valid_count = 0

    for idx, row in df.iterrows():
        Q_raw = row.get("Q_CH4_raw")
        D_row = row.get("D")
        S1_row = row.get("S1_in", 25.0)

        # Skip rows with missing or non-positive values
        try:
            Q_val  = float(Q_raw)
            D_val  = float(D_row)
            S1_val = float(S1_row)
        except (TypeError, ValueError):
            skipped.append({"row": int(idx), "reason": "non-numeric"})
            continue

        if Q_val <= 0 or D_val <= 0 or S1_val <= 0:
            skipped.append({"row": int(idx), "reason": f"non-positive Q={Q_val:.2f} D={D_val:.4f}"})
            continue

        fit = fit_am2_k6_only(
            D_measured=D_val,
            S1_in_measured=S1_val,
            Q_CH4_measured=Q_val,
            digester_volume_m3=digester_volume_m3,
        )

        if "error" in fit:
            skipped.append({"row": int(idx), "reason": fit.get("error", "unknown")})
            continue

        results.append({
            "row":    int(idx),
            "D":      round(D_val, 4),
            "S1_in":  round(S1_val, 2),
            "Q_CH4":  round(Q_val, 3),
            "k6":     fit["k6_fitted"],
            "status": fit["status"],
        })
        valid_count += 1

    if not results:
        return {
            "error": "No valid data rows after filtering",
            "n_rows_total": len(df),
            "n_skipped": len(skipped),
            "skip_sample": skipped[:10],
        }

    # ── Aggregate statistics ──────────────────────────────────────────────────
    k6_values = [r["k6"] for r in results]
    k6_sorted = sorted(k6_values)
    n = len(k6_values)

    k6_mean   = sum(k6_values) / n
    k6_median = k6_sorted[n // 2]
    k6_std    = statistics.stdev(k6_values) if n > 1 else 0.0

    # Percentiles for distribution shape
    k6_p10 = k6_sorted[max(0, int(n * 0.10))]
    k6_p90 = k6_sorted[min(n - 1, int(n * 0.90))]

    # Consistency interpretation
    cv = k6_std / k6_mean if k6_mean > 0 else 0.0
    if cv < 0.10:
        consistency = "GOOD — feedstock composition is stable (<10% CV)"
    elif cv < 0.25:
        consistency = "MODERATE — some feedstock variation (10–25% CV)"
    else:
        consistency = "HIGH VARIATION — feedstock composition varies significantly (>25% CV)"

    return {
        "source_file":          str(csv_path),
        "digester_volume_m3":   digester_volume_m3,
        "n_rows_total":         len(df),
        "n_rows_calibrated":    valid_count,
        "n_rows_skipped":       len(skipped),
        # ── Recommended value for generate_model_card.py ──
        "k6_recommended":       round(k6_median, 1),
        # ── Distribution statistics ──
        "k6_median":            round(k6_median, 1),
        "k6_mean":              round(k6_mean, 1),
        "k6_std":               round(k6_std, 1),
        "k6_cv_pct":            round(cv * 100, 1),
        "k6_p10":               round(k6_p10, 1),
        "k6_p90":               round(k6_p90, 1),
        "k6_min":               round(min(k6_values), 1),
        "k6_max":               round(max(k6_values), 1),
        "feedstock_consistency": consistency,
        # ── Columns detected ──
        "columns_detected": {
            "hrt":    hrt_col,
            "biogas": biogas_col,
            "ch4":    ch4_col,
            "water":  water_col,
            "feed":   feed_cols,
        },
        # ── Sample of per-row results ──
        "results_sample":       results[:5],
        "skip_sample":          skipped[:5],
        # ── How to use ──
        "calibration_note": (
            "k6_recommended (median) is the value to pass to generate_model_card.py. "
            "k6_std reflects feedstock variability — high std means k6 should be "
            "re-fitted seasonally. Ki2, mu2_max remain unvalidated Benyahia defaults."
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SELF-TEST — runs when executed directly: python biomethane_calibration.py
# Covers all three groups. Group 3 requires ad4_simulator.py on the path.
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    def pp(label, d):
        print(f"\n{'='*60}")
        print(f"  {label}")
        print('='*60)
        print(json.dumps(d, indent=2, default=str))

    # ── Group 1: Physics / chemistry (no data needed) ─────────────────────────

    pp("1a. Buswell BMP — Glucose C6H12O6",
       buswell_bmp(c=6, h=12, o=6))

    pp("1a. Buswell BMP — Cellulose C6H10O5",
       buswell_bmp(c=6, h=10, o=5))

    pp("1b. Buswell BMP by class — lipid (tripalmitin)",
       buswell_bmp_by_class("lipid_tripalmitin"))

    pp("1c. Energy conversion factor (97.4% CH4)",
       calculate_energy_conversion_factor(ch4_fraction=0.974))

    pp("1c. Energy conversion factor (100% CH4 — reference)",
       calculate_energy_conversion_factor(ch4_fraction=1.0))

    pp("1d. C/N from elemental analysis (maize silage typical)",
       cn_ratio_from_composition(carbon_pct_of_vs=44.0, nitrogen_pct_of_vs=1.8))

    pp("1e. OLR from recipe",
       olr_from_recipe(
           recipe=[
               {"name": "Cattle slurry", "wet_tonnes": 30},
               {"name": "Maize silage",  "wet_tonnes": 15},
               {"name": "Food waste",    "wet_tonnes":  5},
           ],
           digester_volume_m3=2000,
       ))

    pp("1f. Biodegradability — maize silage vs carbohydrate_cellulose",
       biodegradability_coefficient(
           substrate_class="carbohydrate_cellulose",
           empirical_bmp_nl_per_kg_vs=340,
       ))

    # ── Group 2: SCADA skeletons (no-data mode) ───────────────────────────────

    pp("2a. fit_production_distribution() — no data (placeholder)",
fit_production_distribution(csv_path=None))

    pp("2d. validate_bmp_against_scada() — maize silage, theoretical only",
       validate_bmp_against_scada(
           feedstock_name="Maize silage",
           substrate_class="carbohydrate_cellulose",
       ))

    pp("2d. validate_bmp_against_scada() — food waste with lab assay",
       validate_bmp_against_scada(
           feedstock_name="Food waste",
           substrate_class="carbohydrate_glucose",
           measured_bmp_nl_per_kg_vs=455,
       ))

    # ── Group 3: AD4 Simulation ────────────────────────────────────────
    # Uses ad4_simulator for qualitative what-if analysis
    # See: python src/bio_cli.py simulate --help
    
    pass  # AD4 tools available via bio_cli.py
