#!/usr/bin/env python3
"""
Test biomethane_calibration.py with sample CSV data.

Includes:
1. CUSUM outlier detection
2. Unit conversion fixes
3. Fraction to percentage conversion
"""

import sys
import pandas as pd
import numpy as np
sys.path.insert(0, "src")

from biomethane_calibration import (
    buswell_bmp,
    buswell_bmp_by_class,
    cn_ratio_from_composition,
    olr_from_recipe,
)

CSV_PATH = "sample_data/ri_flex.csv"


def clean_numeric(series: pd.Series) -> pd.Series:
    """Clean and convert to numeric, removing whitespace."""
    return pd.to_numeric(
        series.astype(str).str.strip().str.replace(r'\s+', '', regex=True),
        errors="coerce"
    )


def cusum_outliers(series: pd.Series, threshold: float = 3.0) -> pd.Series:
    """
    Detect outliers using CUSUM method.
    
    Args:
        series: Numeric series
        threshold: Number of standard deviations (default 3.0)
    
    Returns:
        Boolean series marking outliers (same index as input)
    """
    # Work on non-NaN values
    valid = series.dropna()
    if len(valid) < 2:
        return pd.Series([False] * len(series), index=series.index)
    
    mean = valid.mean()
    std = valid.std()
    if std == 0:
        return pd.Series([False] * len(series), index=series.index)
    
    z_scores = (valid - mean).abs() / std
    outlier_mask = z_scores > threshold
    
    # Create result with same index as input
    result = pd.Series([False] * len(series), index=series.index)
    result.loc[valid.index] = outlier_mask.values
    
    return result


def convert_biogas_to_flow(series: pd.Series) -> pd.Series:
    """
    Convert cumulative biogas readings to daily flow.
    
    Assumes: input is cumulative counter, output is difference (daily).
    Returns Nm3/h assuming hourly readings.
    """
    # First, get daily differences
    diff = series.diff()
    
    # Handle negative values (counter reset) and zeros
    diff = diff.where(diff > 0, 0)
    
    # Convert to flow rate (Nm3/h) - divide by hours between readings
    # Assuming hourly readings, this is direct flow
    return diff


def convert_fraction_to_percent(series: pd.Series, threshold: float = 1.0) -> pd.Series:
    """
    Convert fraction to percentage if values are < threshold.
    
    E.g., 0.56 → 56% if median < 1.0
    """
    median = series.median()
    if median < threshold:
        return series * 100
    return series


def load_and_clean_csv(path: str) -> pd.DataFrame:
    """Load and clean CSV data with outlier handling."""
    df = pd.read_csv(path)
    
    # Clean key columns
    clean_columns = {
        'ch4': 'CH4(%)',
        'co2': 'CO2(%)', 
        'biogas': 'Biogas Reading (No.)time 09:10am',
        'temp': 'DigesterTemp (C)09:25am',
        'ph': 'pH',
    }
    
    cleaned = {}
    
    # Process each column
    for key, col in clean_columns.items():
        if col not in df.columns:
            continue
        series = clean_numeric(df[col])
        
        # Remove obvious bad data (zeros, negatives for some columns)
        if key in ['temp', 'biogas']:
            # Keep only reasonable values
            series = series.where(series > 0, np.nan)
            if key == 'temp':
                series = series.where(series < 100, np.nan)  # Remove unrealistic temps
        
        cleaned[key] = series
    
    return cleaned


def map_csv_to_plant_state_v2(csv_path: str, use_outlier_detection: bool = True) -> dict:
    """
    Map CSV to plant_state with improved cleaning.
    
    Args:
        csv_path: Path to CSV
        use_outlier_detection: Whether to remove outliers using CUSUM
    """
    data = load_and_clean_csv(csv_path)
    
    result = {}
    
    # Process each column with cleaning
    if 'temp' in data and len(data['temp']) > 0:
        series = data['temp']
        
        if use_outlier_detection:
            # Remove CUSUM outliers (3 sigma)
            outlier_mask = cusum_outliers(series, threshold=3.0)
            series = series[~outlier_mask]
        
        if len(series) > 0:
            result['digester_temp_c'] = round(series.median(), 1)
    
    if 'ph' in data and len(data['ph']) > 0:
        series = data['ph']
        
        if use_outlier_detection:
            outlier_mask = cusum_outliers(series, threshold=3.0)
            series = series[~outlier_mask]
        
        if len(series) > 0:
            result['digester_ph'] = round(series.median(), 2)
    
    if 'biogas' in data and len(data['biogas']) > 0:
        series = data['biogas']
        
        # Convert cumulative to flow rate
        series = convert_biogas_to_flow(series)
        
        if use_outlier_detection:
            outlier_mask = cusum_outliers(series, threshold=3.0)
            series = series[~outlier_mask]
        
        # Remove zeros and extreme outliers
        series = series.where(series > 0, np.nan)
        series = series.where(series < series.quantile(0.99), np.nan)
        
        if len(series) > 0:
            result['biogas_flow_nm3h'] = round(series.median(), 1)
    
    if 'ch4' in data and len(data['ch4']) > 0:
        series = data['ch4']
        
        # Convert fraction to percentage
        series = convert_fraction_to_percent(series)
        
        if use_outlier_detection:
            outlier_mask = cusum_outliers(series, threshold=3.0)
            series = series[~outlier_mask]
        
        if len(series) > 0:
            result['ch4_pct'] = round(series.median(), 1)
    
    if 'co2' in data and len(data['co2']) > 0:
        series = data['co2']
        
        # Convert fraction to percentage
        series = convert_fraction_to_percent(series)
        
        if use_outlier_detection:
            outlier_mask = cusum_outliers(series, threshold=3.0)
            series = series[~outlier_mask]
        
        if len(series) > 0:
            result['co2_pct'] = round(series.median(), 1)
    
    return result


def main():
    print("=" * 60)
    print("Testing with improved data cleaning")
    print("(CUSUM outliers, unit conversions, fraction→%)")
    print("=" * 60)
    
    # Group 1: Physics-based (no data needed)
    print("\n=== Group 1: Physics-Based Functions ===")
    
    print("\n1a. Buswell BMP - Glucose")
    result = buswell_bmp(c=6, h=12, o=6)
    print(f"  CH4: {result['ch4_fraction_pct']}%, CO2: {result['co2_fraction_pct']}%")
    
    print("\n1b. Buswell by Class - Lipid")
    result = buswell_bmp_by_class("lipid_tripalmitin")
    print(f"  BMP: {result['bmp_nl_per_kg_vs']} NL/kg VS")
    
    print("\n1d. C/N Ratio - typical maize silage")
    result = cn_ratio_from_composition(carbon_pct_of_vs=44.0, nitrogen_pct_of_vs=1.8)
    print(f"  C/N: {result['cn_ratio']}, Status: {result['status']}")
    
    print("\n1e. OLR - Cattle slurry + Maize silage")
    recipe = [
        {"name": "Cattle slurry", "wet_tonnes": 30},
        {"name": "Maize silage", "wet_tonnes": 15},
    ]
    result = olr_from_recipe(recipe, digester_volume_m3=2000)
    print(f"  OLR: {result['olr_kg_vs_m3_day']} kg VS/m3/day")
    print(f"  Status: {result['status']}")
    
    # Group 2: SCADA-dependent
    print("\n=== Group 2: Cleaned CSV Data ===")
    
    print("\n2c. Plant State (with outlier removal)")
    result = map_csv_to_plant_state_v2(CSV_PATH, use_outlier_detection=True)
    print("Cleaned values (CUSUM filtered):")
    for k, v in result.items():
        print(f"  {k}: {v}")
    
    # Compare without cleaning
    print("\n2d. Plant State (raw no cleaning)")
    result_raw = map_csv_to_plant_state_v2(CSV_PATH, use_outlier_detection=False)
    print("Raw values:")
    for k, v in result_raw.items():
        print(f"  {k}: {v}")
    
    print("\n" + "=" * 60)
    print("Done!")


if __name__ == "__main__":
    main()
