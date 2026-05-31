# SCADA Mapper — API Reference

**Module**: `src/scada_mapper.py`

---

## Overview

The SCADA mapper intelligently maps column headers from SCADA system exports
(CSV, Excel) to internal model variable names. It supports **four vendors**
(generic, Siemens, Rockwell, Mitsubishi) and falls back to **regex-based fuzzy
pattern matching** for unknown formats.

### Pipeline

```
SCADA file (CSV/Excel)
       │
       ▼
  read_file()        → auto-detects CSV vs Excel, multiple encoding fallbacks
       │
       ▼
  detect_scada_vendor()  → checks column names for vendor-specific indicators
       │
       ▼
  auto_map_dataframe()   → vendor-specific tags first, then PATTERN_MAP fallback
       │
       ▼
  mapping dict       → { digester_temp_c: "TIC_101", digester_ph: "PH_101", ... }
```

---

## Module-level constants

### `PATTERN_MAP`

```python
PATTERN_MAP: Dict[str, List[str]]
```

A dictionary of **14 internal variable names** mapped to lists of regex patterns.
Patterns are normalised via `clean_column_name()` before matching (lowercased,
non-alphanumeric stripped), so regex syntax uses clean tokens. The first matching
pattern wins.

| Internal variable | Unit | Typical patterns matched |
|---|---|---|
| `digester_temp_c` | °C | `digester.*temp`, `tic_101`, `dig_temp`, `temperature.*c`, `tank.*temp` |
| `digester_ph` | — | `digester.*ph`, `ph_101`, `^ph$`, `acid.*`, `alkalinity` |
| `vfa_mg_l` | mg/L | `vfa.*mg`, `vfa.*\(-`, `acetate`, `vfa_101` |
| `vfa_mol_l` | mmol/L | `vfa.*mmol`, `^vfa$` |
| `nh4_mg_l` | mg N/L | `tan.*mg`, `nh4.*mg`, `ammonia.*mg`, `tan_101` |
| `ch4_pct` | % | `ch4.*%`, `ch4.*conc`, `methane.*%`, `at_101` |
| `co2_pct` | % | `co2.*%`, `co2.*conc`, `carbon.*dioxide`, `at_102` |
| `h2s_ppm` | ppm | `h2s.*ppm`, `h2s_101`, `hydrogen.*sulfide` |
| `biogas_flow_nm3h` | Nm³/h | `biogas.*flow`, `ft_101`, `daily.*biogas`, `flow.*rate.*m3` |
| `biomethane_purity_pct` | % | `purity.*%`, `purity_101`, `biomethane.*%` |
| `organic_load_kg_vs_d` | kg VS/d | `feed.*rate`, `olr`, `organic.*load`, `manure.*fed` |
| `hydraulic_retention_days` | days | `hrt.*calc`, `retention.*day`, `hrt_101`, `hrt.*day` |
| `biogas_production` | — | `biogas.*prod`, `gas.*prod`, `biogas_production` |
| `ambient_temp_c` | °C | `temp.*c\)`, `ambient.*temp`, `temperature.*c\)` |

### `VENDOR_TAG_MAPS`

```python
VENDOR_TAG_MAPS: Dict[str, Dict[str, List[str]]]
```

Exact tag name lists for known SCADA vendors. Used **first** in
`auto_map_dataframe()`, before falling back to `PATTERN_MAP`.

| Vendor | Variable | Known tags |
|--------|----------|------------|
| `generic` | `digester_temp_c` | `DigesterTemp`, `Temp`, `Temperature` |
| `generic` | `digester_ph` | `pH`, `Digester_pH` |
| `generic` | `vfa_mg_l` | `VFA`, `VFA (mg/L)` |
| `generic` | `nh4_mg_l` | `TAN`, `TAN (mg N/L)` |
| `generic` | `ch4_pct` | `CH4`, `CH4(%)` |
| `generic` | `biogas_flow_nm3h` | `Biogas Flow`, `Flow Rate` |
| `siemens` | `digester_temp_c` | `TIC_101`, `DB1.DBD20` |
| `siemens` | `digester_ph` | `PH_101`, `DB1.DBD24` |
| `siemens` | `vfa_mmol_l` | `VFA_101` |
| `siemens` | `biogas_flow_nm3h` | `FT_101` |
| `siemens` | `ch4_pct` | `AT_101` |
| `rockwell` | `digester_temp_c` | `TEMPERATURE_PV`, `FIC101.PV` |
| `rockwell` | `digester_ph` | `PH_PV`, `AIC101.PV` |
| `rockwell` | `biogas_flow_nm3h` | `FLOW_PV`, `FI101.PV` |
| `mitsubishi` | `digester_temp_c` | `D100`, `W10` |
| `mitsubishi` | `digester_ph` | `D200` |

---

## Functions

### `clean_column_name`

```python
def clean_column_name(col: str) -> str
```

Normalise a column name for matching. Strips all non-alphanumeric characters and
lowercases.

#### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `col` | `str` | Raw column name from the file |

#### Returns

Lowercase alphanumeric string (e.g. `"TIC_101"` → `"tic101"`,
`"pH (Digester)"` → `"phdigester"`).

---

### `find_column_pattern`

```python
def find_column_pattern(df_columns: List[str], patterns: List[str]) -> Optional[str]
```

Find a column that matches any of the given regex patterns.

#### Algorithm

1. Build `{clean_name: original_name}` lookup from `df_columns` via
   `clean_column_name()`.
2. Clean each pattern via `clean_column_name()`.
3. For each pattern, iterate the lookup dictionary and return the **original**
   column name on the first `re.search()` hit.
4. Return `None` if no pattern matches.

#### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `df_columns` | `List[str]` | Actual column names from the DataFrame |
| `patterns` | `List[str]` | Regex patterns to try (in order) |

#### Returns

The original (un-normalised) column name from the DataFrame, or `None`.

---

### `auto_map_dataframe`

```python
def auto_map_dataframe(df: pd.DataFrame, vendor: str = "generic") -> Dict[str, str]
```

Auto-map DataFrame columns to internal variable names.

#### Algorithm

1. Apply **vendor-specific** mapping from `VENDOR_TAG_MAPS[vendor]` first
   (vendor tags are suffixed with `.*` to allow trailing characters).
2. Fill remaining unmapped variables by iterating `PATTERN_MAP` in definition
   order.

#### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `df` | `pd.DataFrame` | — | Input DataFrame |
| `vendor` | `str` | `"generic"` | One of `"generic"`, `"siemens"`, `"rockwell"`, `"mitsubishi"` |

#### Returns

`Dict[str, str]` — mapping `{internal_variable_name: actual_column_name}`.

#### Example

```python
df = pd.read_csv("plant_export.csv")
mapping = auto_map_dataframe(df, vendor="siemens")
# -> {"digester_temp_c": "TIC_101", "digester_ph": "PH_101", ...}
```

---

### `detect_scada_vendor`

```python
def detect_scada_vendor(df: pd.DataFrame) -> str
```

Auto-detect the SCADA vendor by inspecting column names for SCADA-naming
convention indicators.

#### Detection logic

| Vendor | Indicators checked |
|--------|-------------------|
| `"siemens"` | Column names contain `tic_`, `ph_`, `ft_`, `at_`, `db1.` (Siemens S7 tag convention) |
| `"rockwell"` | Column names contain `_pv`, `fic`, `aic`, `fi101` (PlantPAX / ControlLogix convention) |
| `"mitsubishi"` | Column names contain `d100`, `d200`, `w10` (MELSEC soft element convention) |
| `"generic"` | Fallback — also triggered by `digestertemp`-style compound names |

Checked in this order. First match wins.

#### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `df` | `pd.DataFrame` | DataFrame with columns to inspect |

#### Returns

One of `"siemens"`, `"rockwell"`, `"mitsubishi"`, `"generic"`.

#### Example

```python
df = pd.read_csv("siemens_export.csv")
vendor = detect_scada_vendor(df)  # -> "siemens"
```

---

### `read_csv`

```python
def read_csv(file_path: Path) -> pd.DataFrame
```

Read a CSV file with automatic encoding fallback.

#### Encoding fallback order

1. `utf-8`
2. `latin-1`
3. `cp1252`
4. `iso-8859-1`

#### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `file_path` | `Path` | Path to CSV file |

#### Returns

`pd.DataFrame`

#### Raises

`ValueError` — if none of the four encodings succeed.

---

### `read_excel`

```python
def read_excel(file_path: Path) -> pd.DataFrame
```

Read an Excel file (`.xlsx` / `.xls`), returning the **first sheet**.

#### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `file_path` | `Path` | Path to `.xlsx` or `.xls` file |

#### Returns

`pd.DataFrame`

---

### `read_file`

```python
def read_file(file_path: Path) -> Tuple[pd.DataFrame, str]
```

Dispatch to CSV or Excel reader based on file extension.

#### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `file_path` | `Path` | Path to a `.csv`, `.xlsx`, or `.xls` file |

#### Returns

`Tuple[pd.DataFrame, str]` — `(dataframe, format)` where `format` is
`"csv"` or `"excel"`.

#### Raises

`ValueError` — for any unsupported extension.

---

### `map_scada_file`

```python
def map_scada_file(
    file_path: Path,
    site_config: Optional[Dict] = None,
    vendor_hint: Optional[str] = None
) -> Dict[str, str]
```

Full mapping pipeline: read file → detect vendor → auto-map.

#### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | `Path` | — | Path to SCADA export file |
| `site_config` | `Optional[Dict]` | `None` | Dict parsed from `site_config.json`. If it contains a `"tag_mapping"` key, that mapping is returned directly (skipping auto-detection) |
| `vendor_hint` | `Optional[str]` | `None` | Override auto-detected vendor. One of `"generic"`, `"siemens"`, `"rockwell"`, `"mitsubishi"` |

#### Returns

`Dict[str, str]` — mapping `{internal_variable_name: actual_column_name}`.

#### Logging

Logs file path, detected format and shape, first 10 columns, vendor, mapped
variables, and warns if `"digester_temp_c"` or `"digester_ph"` are missing.

---

### `show_mapping`

```python
def show_mapping(mapping: Dict[str, str], df: Optional[pd.DataFrame] = None)
```

Pretty-print the mapping results to stdout. If a DataFrame is provided, shows a
sample value for each mapped column.

#### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `mapping` | `Dict[str, str]` | Mapping dict (internal → actual column name) |
| `df` | `Optional[pd.DataFrame]` | Original data for sample value display |

#### Output format

```
============================================================
SCADA Mapping Results
============================================================
  digester_temp_c            <- 'TIC_101'
         Sample value: 38.5
  digester_ph                <- 'PH_101'
         Sample value: 7.2
============================================================
```

---

### `run_tests`

```python
def run_tests()
```

Run auto-detection tests on sample data files. Reads each file, detects the
vendor, auto-maps columns, and prints results. Checks for the universal required
variable `digester_temp_c`.

#### Test files attempted

1. `sample_data/ri_flex.csv`
2. `sample_data/Indian-Biogas-Production-Dataset/biogas_dataset.csv`

Files that do not exist are silently skipped with a warning.

---

## CLI entry point (`main`)

```python
python src/scada_mapper.py [options]
```

| Argument | Type | Description |
|----------|------|-------------|
| `--file` | `Path` | Path to SCADA export file (CSV or Excel) |
| `--vendor` | `str` | Vendor hint: `generic`, `siemens`, `rockwell`, or `mitsubishi` (bypasses auto-detect) |
| `--site` | `Path` | Path to `site_config.json` — if `tag_mapping` key exists, it is used directly |
| `--test` | `flag` | Run auto-detection tests against sample files |
| `--output` | `Path` | Save the resulting mapping dict as JSON |

### Behaviour

- `--test` runs `run_tests()` and exits immediately (ignores other args).
- Without `--file`, prints help text and exits.
- With `--site` and `--file` both provided, `site_config["tag_mapping"]` takes
  priority and no auto-detection is performed.

### CLI examples

```bash
# Basic usage — auto-detect vendor, auto-map
python src/scada_mapper.py --file sample_data/ri_flex.csv

# Force Siemens vendor, save mapping
python src/scada_mapper.py --file siemens_export.csv --vendor siemens --output mapping.json

# Use site configuration with known tag mapping
python src/scada_mapper.py --file data.csv --site site_config.json

# Run auto-detection tests on sample files
python src/scada_mapper.py --test
```

---

## Usage examples

### Python API — basic mapping

```python
from pathlib import Path
from scada_mapper import map_scada_file, show_mapping, read_file

# Full pipeline
mapping = map_scada_file(Path("data.csv"))
show_mapping(mapping)

# Output:
# ============================================================
# SCADA Mapping Results
# ============================================================
#   digester_temp_c            <- 'Digester Temp (°C)'
#   digester_ph                <- 'pH'
#   vfa_mg_l                   <- 'VFA (mg/L)'
#   ch4_pct                    <- 'CH4 (%)'
#   biogas_flow_nm3h           <- 'Biogas Flow (Nm3/h)'
# ============================================================
```

### Python API — with vendor hint and site config

```python
import json
from pathlib import Path
from scada_mapper import map_scada_file

# Force Siemens vendor
mapping = map_scada_file(
    Path("siemens_export.csv"),
    vendor_hint="siemens"
)
# -> {"digester_temp_c": "TIC_101", "digester_ph": "PH_101", ...}

# Use site_config (skips auto-detection entirely)
site_config = json.loads(Path("site_config.json").read_text())
mapping = map_scada_file(
    Path("data.csv"),
    site_config=site_config
)
```

### Python API — low-level auto_map

```python
import pandas as pd
from scada_mapper import auto_map_dataframe, detect_scada_vendor

df = pd.read_csv("plant_data.csv")
vendor = detect_scada_vendor(df)         # auto-detect
mapping = auto_map_dataframe(df, vendor) # auto-map
```

### Siemens SCADA export

Given a file with columns like `TIC_101`, `PH_101`, `FT_101`, `AT_101`:

```python
vendor = detect_scada_vendor(df)  # "siemens"
mapping = auto_map_dataframe(df, vendor="siemens")
# -> {
#     "digester_temp_c": "TIC_101",
#     "digester_ph": "PH_101",
#     "biogas_flow_nm3h": "FT_101",
#     "ch4_pct": "AT_101"
# }
```

### Rockwell / PlantPAX export

Given columns like `TEMPERATURE_PV`, `PH_PV`, `FLOW_PV`:

```python
vendor = detect_scada_vendor(df)  # "rockwell"
mapping = auto_map_dataframe(df, vendor="rockwell")
# -> {
#     "digester_temp_c": "TEMPERATURE_PV",
#     "digester_ph": "PH_PV",
#     "biogas_flow_nm3h": "FLOW_PV"
# }
```

### Mitsubishi MELSEC export

Given columns like `D100`, `D200`:

```python
vendor = detect_scada_vendor(df)  # "mitsubishi"
mapping = auto_map_dataframe(df, vendor="mitsubishi")
# -> {
#     "digester_temp_c": "D100",
#     "digester_ph": "D200"
# }
```

### Generic / unknown format

For free-form column names like `Digester Temp (°C)`, `pH`, `CH4 (%)`:

```python
vendor = detect_scada_vendor(df)  # "generic"
mapping = auto_map_dataframe(df, vendor="generic")
# -> {
#     "digester_temp_c": "Digester Temp (°C)",
#     "digester_ph": "pH",
#     "ch4_pct": "CH4 (%)"
# }
```

### Encoding fallback example

If a CSV is saved as `latin-1` (common for older SCADA exports), `read_csv`
transparently falls back through `utf-8` → `latin-1` → `cp1252` →
`iso-8859-1`:

```python
from scada_mapper import read_csv
df = read_csv(Path("legacy_export.csv"))  # succeeds even if not UTF-8
```

---

## Column name normalisation rules

`clean_column_name()` applies these transformations to every column name and
pattern before matching:

| Raw name | Cleaned | Notes |
|----------|---------|-------|
| `TIC_101` | `tic101` | Underscores removed |
| `pH (Digester)` | `phdigester` | Spaces and parens removed |
| `CH4 (%)` | `ch4` | `(%)` stripped — note: `%` survives since it passes `re.sub(r'[^a-z0-9]', '', ...)` after lowercasing? Actually `%` is non-alphanumeric so it IS removed → `ch4` |
| `Digester Temp (°C)` | `digestertempc` | Everything non-alphanumeric stripped |
| `Biogas Flow (Nm3/h)` | `biogasflownm3h` | Slashes and parens removed |
| `VFA (mg/L)` | `vfamgl` | Parentheses and slash removed |

Patterns from `PATTERN_MAP` are also cleaned before matching, so writing
`r"digester.*temp"` in the source effectively matches against `digester.*temp`
in cleaned space.
