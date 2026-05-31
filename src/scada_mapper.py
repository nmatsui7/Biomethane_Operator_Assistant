"""
scada_mapper.py - Intelligent SCADA Data Mapper
==============================================

Intelligent mapping of SCADA tags to internal model variable names.
Supports multiple formats: CSV, Excel, SQL (future), API (future).

Features:
- Auto-detection of SCADA format/vendor (Siemens, Rockwell, Mitsubishi, Generic)
- Fuzzy column matching (pattern-based, not exact)
- Site configuration support (site_config.json)
- Multiple export formats

Usage:
    python src/scada_mapper.py --file sample_data/ri_flex.csv
    python src/scada_mapper.py --file data.csv --site site_config.json
    python src/scada_mapper.py --test  # Run auto-detection tests
"""

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# =============================================================================
# Pattern-based column matching (fuzzy, not exact)
# =============================================================================

# Pattern maps: internal_var_name -> list of possible column name patterns
PATTERN_MAP = {
    "digester_temp_c": [
        r"digester.*temp", r"temp.*digester", r"tic_101", r"dig_temp",
        r"temperature.*c", r"temp.*c.*09", r"tank.*temp",
        # Indian dataset
        r"digester.*temp.*c", r"temp.*c\)", r"digester.*temp.*\)"
    ],
    "digester_ph": [
        r"digester.*ph", r"ph_101", r"tank.*ph", r"ph.*$", r"^ph$",
        # Indian dataset specific
        r"ph.*\)", r"ph.*\(", r"acid.*$", r"alkalinity"
    ],
    "vfa_mg_l": [
        r"vfa.*mg", r"vfa.*\(-", r"acetate", r"vfa_101"
    ],
    "vfa_mol_l": [
        r"vfa.*mmol", r"vfa.*$", r"^vfa$"
    ],
    "nh4_mg_l": [
        r"tan.*mg", r"nh4.*mg", r"ammonia.*mg", r"tan_101"
    ],
    "ch4_pct": [
        r"ch4.*%", r"ch4.*conc", r"methane.*%", r"at_101",
        r"ch4.*\)"
    ],
    "co2_pct": [
        r"co2.*%", r"co2.*conc", r"carbon.*dioxide", r"at_102"
    ],
    "h2s_ppm": [
        r"h2s.*ppm", r"h2s_101", r"hydrogen.*sulfide"
    ],
    "biogas_flow_nm3h": [
        r"biogas.*flow", r"ft_101", r"gas.*flow.*h",
        r"daily.*biogas", r"biogas.*ml", r"biogas.*stp",  # prefer daily over cumulative
        r"biogas.*reading",  # cumulative meter — last resort for this field
        r"flow.*rate.*m3"
    ],
    "biomethane_purity_pct": [
        r"purity.*%", r"purity_101", r"biomethane.*%"
    ],
    "organic_load_kg_vs_d": [
        r"feed.*rate", r"olr", r"organic.*load",
        r"feed.*weight", r"manure.*fed", r"substrate.*fed"
        # Note: removed r"vs.*d" — too broad, matches VS% columns
    ],
    "hydraulic_retention_days": [
        r"hrt.*calc", r"retention.*day", r"hrt_101",
        r"hrt.*day"
    ],
    # Indian dataset specific
    "biogas_production": [
        r"biogas.*prod", r"gas.*prod", r"biogas_production"
    ],
    "ambient_temp_c": [
        r"temp.*c\)", r"ambient.*temp", r"temperature.*c\)"
    ],
}

# =============================================================================
# Vendor-specific tag mappings (for known SCADA systems)
# =============================================================================

VENDOR_TAG_MAPS = {
    "generic": {
        "digester_temp_c": ["DigesterTemp", "Temp", "Temperature"],
        "digester_ph": ["pH", "Digester_pH"],
        "vfa_mg_l": ["VFA", "VFA (mg/L)"],
        "nh4_mg_l": ["TAN", "TAN (mg N/L)"],
        "ch4_pct": ["CH4", "CH4(%)"],
        "biogas_flow_nm3h": ["Biogas Flow", "Flow Rate"],
    },
    "siemens": {
        "digester_temp_c": ["TIC_101", "DB1.DBD20"],
        "digester_ph": ["PH_101", "DB1.DBD24"],
        "vfa_mmol_l": ["VFA_101"],
        "biogas_flow_nm3h": ["FT_101"],
        "ch4_pct": ["AT_101"],
    },
    "rockwell": {
        "digester_temp_c": ["TEMPERATURE_PV", "FIC101.PV"],
        "digester_ph": ["PH_PV", "AIC101.PV"],
        "biogas_flow_nm3h": ["FLOW_PV", "FI101.PV"],
    },
    "mitsubishi": {
        "digester_temp_c": ["D100", "W10"],
        "digester_ph": ["D200"],
    },
}

# =============================================================================
# Core mapping functions (fuzzy pattern matching)
# =============================================================================

def clean_column_name(col: str) -> str:
    """Normalize column name for matching."""
    return re.sub(r'[^a-z0-9]', '', col.lower())


def find_column_pattern(df_columns: List[str], patterns: List[str]) -> Optional[str]:
    """
    Find a column in df_columns that matches any of the given patterns.
    Returns the ACTUAL column name from df (preserving original case).
    """
    columns_clean = {clean_column_name(c): c for c in df_columns}
    
    for pattern in patterns:
        pattern_clean = clean_column_name(pattern)
        # Try regex match
        for col_clean, col_original in columns_clean.items():
            if re.search(pattern_clean, col_clean):
                return col_original
    return None


def auto_map_dataframe(df: pd.DataFrame, vendor: str = "generic") -> Dict[str, str]:
    """
    Auto-map DataFrame columns to internal variable names using pattern matching.
    
    Args:
        df: Input DataFrame (from CSV, Excel, etc.)
        vendor: SCADA vendor hint ("generic", "siemens", "rockwell", "mitsubishi")
    
    Returns:
        Dict mapping {internal_var_name: actual_column_name}
    """
    result = {}
    columns = list(df.columns)
    
    # Try vendor-specific mapping first
    vendor_patterns = VENDOR_TAG_MAPS.get(vendor, {})
    
    for internal_var, vendor_tags in vendor_patterns.items():
        # Add vendor tags as patterns
        patterns = [t + r'.*' for t in vendor_tags]
        found = find_column_pattern(columns, patterns)
        if found:
            result[internal_var] = found
    
    # Fall back to pattern-based matching for unmapped variables
    for internal_var, patterns in PATTERN_MAP.items():
        if internal_var not in result:
            found = find_column_pattern(columns, patterns)
            if found:
                result[internal_var] = found
    
    return result


def detect_scada_vendor(df: pd.DataFrame) -> str:
    """
    Auto-detect SCADA vendor based on column names.
    Returns: "siemens", "rockwell", "mitsubishi", or "generic"
    """
    columns_clean = [clean_column_name(c) for c in df.columns]
    columns_text = ' '.join(columns_clean)
    
    # Siemens indicators
    siemens_indicators = ['tic_', 'ph_', 'ft_', 'at_', 'db1.']
    if any(ind in columns_text for ind in siemens_indicators):
        return "siemens"
    
    # Rockwell indicators
    rockwell_indicators = ['_pv', 'fic', 'aic', 'fi101']
    if any(ind in columns_text for ind in rockwell_indicators):
        return "rockwell"
    
    # Mitsubishi indicators
    mitsubishi_indicators = ['d100', 'd200', 'w10']
    if any(ind in columns_text for ind in mitsubishi_indicators):
        return "mitsubishi"
    
    # Check for ri_flex.csv style (generic with specific formatting)
    if any('digestertemp' in c for c in columns_clean):
        return "generic"
    
    return "generic"


# =============================================================================
# File readers (multiple formats)
# =============================================================================

def read_csv(file_path: Path) -> pd.DataFrame:
    """Read CSV file, handle various encodings."""
    encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
    
    for enc in encodings:
        try:
            return pd.read_csv(file_path, encoding=enc)
        except UnicodeDecodeError:
            continue
    
    raise ValueError(f"Cannot read {file_path}: tried {encodings}")


def read_excel(file_path: Path) -> pd.DataFrame:
    """Read Excel file (first sheet)."""
    return pd.read_excel(file_path, sheet_name=0)


def read_file(file_path: Path) -> Tuple[pd.DataFrame, str]:
    """
    Read any supported file format.
    Returns: (DataFrame, format_detected)
    """
    suffix = file_path.suffix.lower()
    
    if suffix == '.csv':
        df = read_csv(file_path)
        return df, 'csv'
    elif suffix in ('.xlsx', '.xls'):
        df = read_excel(file_path)
        return df, 'excel'
    else:
        raise ValueError(f"Unsupported file format: {suffix}")


# =============================================================================
# Main mapping function (intelligent)
# =============================================================================

def map_scada_file(
    file_path: Path,
    site_config: Optional[Dict] = None,
    vendor_hint: Optional[str] = None
) -> Dict[str, str]:
    """
    Intelligent mapping of SCADA file to internal variable names.
    
    Args:
        file_path: Path to SCADA export (CSV, Excel)
        site_config: Optional site_config.json dict (for known mappings)
        vendor_hint: Optional vendor hint (overrides auto-detect)
    
    Returns:
        Dict mapping {internal_var_name: actual_column_name}
    """
    logger.info(f"Reading file: {file_path}")
    df, fmt = read_file(file_path)
    logger.info(f"Detected format: {fmt}, shape: {df.shape}")
    logger.info(f"Columns: {list(df.columns)[:10]}...")  # Show first 10
    
    # Use site_config mapping if provided
    if site_config and "tag_mapping" in site_config:
        logger.info("Using site_config.json tag_mapping")
        return site_config["tag_mapping"]
    
    # Auto-detect vendor
    vendor = vendor_hint or detect_scada_vendor(df)
    logger.info(f"Detected vendor: {vendor}")
    
    # Auto-map using patterns
    mapping = auto_map_dataframe(df, vendor)
    
    logger.info(f"Mapped {len(mapping)} variables:")
    for internal, actual in mapping.items():
        logger.info(f"  {internal} <- '{actual}'")
    
    # Check for missing required variables
    required = ["digester_temp_c", "digester_ph"]
    missing = [r for r in required if r not in mapping]
    if missing:
        logger.warning(f"Missing required variables: {missing}")
    
    return mapping


# =============================================================================
# CLI interface
# =============================================================================

def show_mapping(mapping: Dict[str, str], df: Optional[pd.DataFrame] = None):
    """Pretty-print the mapping."""
    print("\n" + "="*60)
    print("SCADA Mapping Results")
    print("="*60)
    
    for internal, actual in mapping.items():
        print(f"  {internal:<25} <- '{actual}'")
        if df is not None and actual in df.columns:
            sample = df[actual].iloc[0] if len(df) > 0 else "N/A"
            print(f"         Sample value: {sample}")
    print("="*60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Intelligent SCADA data mapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/scada_mapper.py --file sample_data/ri_flex.csv
  python src/scada_mapper.py --file data.csv --vendor siemens
  python src/scada_mapper.py --file data.csv --site site_config.json
        """
    )
    parser.add_argument("--file", type=Path, help="SCADA export file (CSV, Excel)")
    parser.add_argument("--vendor", choices=["generic", "siemens", "rockwell", "mitsubishi"],
                        help="SCADA vendor (auto-detect if not specified)")
    parser.add_argument("--site", type=Path, help="Site configuration JSON file")
    parser.add_argument("--test", action="store_true", help="Run auto-detection tests")
    parser.add_argument("--output", type=Path, help="Save mapping to JSON file")
    args = parser.parse_args()
    
    if args.test:
        run_tests()
        return
    
    if not args.file:
        parser.print_help()
        return
    
    # Load site config if provided
    site_config = None
    if args.site and args.site.exists():
        site_config = json.loads(args.site.read_text())
        logger.info(f"Loaded site config: {args.site}")
    
    # Map the file
    mapping = map_scada_file(args.file, site_config, args.vendor)
    
    # Show results
    df, _ = read_file(args.file)
    show_mapping(mapping, df)
    
    # Save if requested
    if args.output:
        args.output.write_text(json.dumps(mapping, indent=2))
        logger.info(f"Saved mapping to: {args.output}")


def run_tests():
    """Test auto-detection on sample files."""
    print("\n" + "="*60)
    print("Running Auto-Detection Tests")
    print("="*60 + "\n")
    
    test_files = [
        Path("sample_data/ri_flex.csv"),
        Path("sample_data/Indian-Biogas-Production-Dataset/biogas_dataset.csv"),
    ]
    
    for test_file in test_files:
        if not test_file.exists():
            logger.warning(f"Test file not found: {test_file}")
            continue
        
        print(f"\nTesting: {test_file}")
        print("-" * 40)
        
        df, fmt = read_file(test_file)
        vendor = detect_scada_vendor(df)
        mapping = auto_map_dataframe(df, vendor)
        
        print(f"Format: {fmt}")
        print(f"Vendor: {vendor}")
        print(f"Columns: {len(df.columns)}")
        print(f"Mapped: {len(mapping)} variables")
        
        # Check required (not all datasets have all)
        required = ["digester_temp_c"]  # Only temp is universal
        missing = [r for r in required if r not in mapping]
        if missing:
            logger.warning(f"Missing required variables: {missing}")
        else:
            print("✓ Required variables present")
        
        for internal, actual in mapping.items():
            print(f"  {internal} <- '{actual}'")
    
    print("\n" + "="*60)
    print("Tests complete (note: not all datasets have pH, VFA, etc.)")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
