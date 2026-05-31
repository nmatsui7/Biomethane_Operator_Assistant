# `biomethane_calibration` — Calibration Module

## Overview

`src/biomethane_calibration.py` provides calibration and validation functions
for anaerobic digestion process modelling. It is organised into three groups:

> **Calibration is manual.** Parameters are edited directly in this file —
> Buswell molecular formulas, C/N ratio tables, k6 fitting bounds — and
> applied by re-running `bio_cli.py calibrate ...`. There is no AI-driven or
> automated parameter tuning; every calibration is an explicit,
> operator-initiated action.

| Group | Topic | Data Required |
|-------|-------|---------------|
| **1a–1f** | Physics / chemistry calculations | None (deterministic) |
| **2a–2d** | SCADA statistical fitting | Plant historian CSV |
| **3** | AD4 simulator calibration | Plant production CSV |

```python
from src.biomethane_calibration import (
    buswell_bmp, buswell_bmp_by_class,
    calculate_energy_conversion_factor,
    cn_ratio_from_composition,
    olr_from_recipe,
    biodegradability_coefficient,
    fit_production_distribution, fit_uptime_range,
    fit_steady_state_plant_state,
    validate_bmp_against_scada,
    fit_am2_k6_only, fit_am2_from_dataset, fit_am2_from_csv,
    ParameterSource, CalibrationConfidence,
)
```

---

## Constants

| Name | Value | Unit | Source |
|------|-------|------|--------|
| `NTP_L_PER_MOL` | `24.04` | L/mol | VDI 4630 (20 °C, 1 atm) |
| `LHV_CH4_MJ_PER_NM3_STP` | `35.88` | MJ/Nm³ | Perry & Green (2008), Table 2-150 |

---

## `_SUBSTRATE_FORMULAS`

Dictionary of approximate elemental formulae for common substrate classes.
Used internally by `buswell_bmp_by_class()` and `biodegradability_coefficient()`.

| Key | C | H | O | N | S | MW (g/mol) | Source |
|-----|---|---|---|---|---|------------|--------|
| `carbohydrate_cellulose` | 6 | 10 | 5 | 0 | 0 | 162.14 | Drosg (2013) |
| `carbohydrate_glucose` | 6 | 12 | 6 | 0 | 0 | 180.16 | Drosg (2013) |
| `protein_generic` | 5 | 7 | 2 | 1 | 0 | 113.12 | Angelidaki et al. (2009) |
| `lipid_tripalmitin` | 51 | 98 | 6 | 0 | 0 | 807.32 | Drosg (2013) |
| `lipid_triolein` | 57 | 104 | 6 | 0 | 0 | 885.43 | Drosg (2013) |
| `volatile_fatty_acid_acetic` | 2 | 4 | 2 | 0 | 0 | 60.05 | Stoichiometric |

---

## Group 1 — Physics / Chemistry Based

### `buswell_bmp()`

Calculates theoretical Biomethane Potential (BMP) and biogas composition from
the elemental formula C<sub>c</sub>H<sub>h</sub>O<sub>o</sub>N<sub>n</sub>S<sub>s</sub>
using the Buswell & Mueller (1952) equation.

```python
def buswell_bmp(
    c: float,
    h: float,
    o: float,
    n: float = 0.0,
    s: float = 0.0,
) -> dict
```

**Parameters**

| Param | Description |
|-------|-------------|
| `c` | Molar stoichiometric coefficient of carbon |
| `h` | Molar stoichiometric coefficient of hydrogen |
| `o` | Molar stoichiometric coefficient of oxygen |
| `n` | Molar stoichiometric coefficient of nitrogen (default `0.0`) |
| `s` | Molar stoichiometric coefficient of sulfur (default `0.0`) |

**Returns**

| Key | Type | Description |
|-----|------|-------------|
| `substrate_formula` | `str` | Formatted formula string, e.g. `"C6H12O6N0S0"` |
| `ch4_mol_per_mol_substrate` | `float` | Moles CH₄ produced per mole substrate (4 d.p.) |
| `co2_mol_per_mol_substrate` | `float` | Moles CO₂ produced (4 d.p.) |
| `h2o_consumed_mol` | `float` | Moles H₂O consumed in digestion (4 d.p.) |
| `ch4_nl_per_mol_substrate` | `float` | NTP volume CH₄ per mole substrate (L/mol, 2 d.p.) |
| `ch4_fraction_pct` | `float` | CH₄ mole fraction in raw biogas (%, 1 d.p.) |
| `co2_fraction_pct` | `float` | CO₂ mole fraction in raw biogas (%, 1 d.p.) |
| `note` | `str` | Conversion guidance for NL CH₄ / g VS |

**Reference**

Buswell & Mueller (1952). *Mechanism of methane fermentation.*
Ind. Eng. Chem., 44(3), 550–552.

Equation:

```
C_c H_h O_o N_n S_s + (c - h/4 - o/2 + 3n/4 + s/2) H₂O
    → (c/2 + h/8 - o/4 - 3n/8 - s/4) CH₄
    +  (c/2 - h/8 + o/4 + 3n/8 + s/4) CO₂
    +  n NH₃
    +  s H₂S
```

**Examples**

```python
# Glucose C6H12O6 (MW = 180.16 g/mol)
r = buswell_bmp(c=6, h=12, o=6)
r["ch4_fraction_pct"]   # 50.0

# Cellulose C6H10O5 (MW = 162.14 g/mol)
r = buswell_bmp(c=6, h=10, o=5)

# Protein C5H7O2N (MW = 113.12 g/mol)
r = buswell_bmp(c=5, h=7, o=2, n=1)
```

---

### `buswell_bmp_by_class()`

Convenience wrapper — calls `buswell_bmp()` with the elemental formula from
`_SUBSTRATE_FORMULAS` for a named substrate class. Returns BMP in
NL CH₄ / g VS and NL CH₄ / kg VS.

```python
def buswell_bmp_by_class(substrate_class: str) -> dict
```

**Parameters**

| Param | Description |
|-------|-------------|
| `substrate_class` | Key from `_SUBSTRATE_FORMULAS`: `"carbohydrate_cellulose"`, `"carbohydrate_glucose"`, `"protein_generic"`, `"lipid_tripalmitin"`, `"lipid_triolein"`, `"volatile_fatty_acid_acetic"` |

**Returns**

All keys from `buswell_bmp()`, plus:

| Key | Type | Description |
|-----|------|-------------|
| `substrate_class` | `str` | The class name passed in |
| `molecular_weight` | `float` | Substrate molecular weight (g/mol) |
| `bmp_nl_per_g_vs` | `float` | Theoretical BMP per gram VS (1 d.p.) |
| `bmp_nl_per_kg_vs` | `float` | Theoretical BMP per kilogram VS (0 d.p.) |
| `note` | `str` | Reminder that these are theoretical maxima |

If the class is unknown, returns `{"error": ..., "available": [...]}`.

**Example**

```python
r = buswell_bmp_by_class("lipid_tripalmitin")
r["bmp_nl_per_kg_vs"]   # theoretical maximum
```

---

### `calculate_energy_conversion_factor()`

Derives the kWh/Nm³ conversion factor for biomethane from the lower heating
value (LHV) of methane, accounting for actual CH₄ purity.

```python
def calculate_energy_conversion_factor(
    ch4_fraction: float = 0.974,
) -> dict
```

**Parameters**

| Param | Default | Description |
|-------|---------|-------------|
| `ch4_fraction` | `0.974` | CH₄ mole fraction in upgraded biomethane |

**Returns**

| Key | Type | Description |
|-----|------|-------------|
| `ch4_fraction_input` | `float` | CH₄ fraction used (4 d.p.) |
| `LHV_pure_CH4_MJ_per_Nm3_STP` | `float` | LHV at STP: 35.88 MJ/Nm³ |
| `STP_to_NTP_correction` | `float` | Volume correction 273.15/288.15 (4 d.p.) |
| `LHV_pure_CH4_MJ_per_Nm3_NTP` | `float` | LHV at NTP (3 d.p.) |
| `LHV_biomethane_MJ_per_Nm3` | `float` | Scaled by CH₄ fraction (3 d.p.) |
| `LHV_biomethane_kWh_per_Nm3` | `float` | Conversion in kWh (3 d.p.) |
| `script_current_value_kWh_per_Nm3` | `int` | Hardcoded value `10.55` |
| `difference_pct` | `float` | Relative difference from script value (2 d.p.) |
| `recommendation` | `str` | Whether to update the script constant |
| `reference` | `str` | Perry & Green (2008) citation |

**Reference**

Perry & Green (2008). *Perry's Chemical Engineers' Handbook,* 8th ed.,
Table 2-150. LHV of pure CH₄ = 35.88 MJ/Nm³ at 0 °C, 1 atm (STP).

**Example**

```python
r = calculate_energy_conversion_factor(0.974)
print(r["LHV_biomethane_kWh_per_Nm3"])   # e.g. 10.114
```

---

### `cn_ratio_from_composition()`

Calculates the C/N ratio directly from elemental analysis results (% C and % N
as fraction of volatile solids). Returns an operational advisory based on the
ratio.

```python
def cn_ratio_from_composition(
    carbon_pct_of_vs: float,
    nitrogen_pct_of_vs: float,
) -> dict
```

**Parameters**

| Param | Description |
|-------|-------------|
| `carbon_pct_of_vs` | % carbon by mass of volatile solids (e.g. `42.5`) |
| `nitrogen_pct_of_vs` | % nitrogen by mass of volatile solids (e.g. `3.2`) |

**Returns**

| Key | Type | Description |
|-----|------|-------------|
| `carbon_pct_vs` | `float` | Input carbon % (2 d.p.) |
| `nitrogen_pct_vs` | `float` | Input nitrogen % (2 d.p.) |
| `cn_ratio` | `float` | C/N mass ratio (1 d.p.) |
| `status` | `str` | One of `OPTIMAL`, `LOW — ammonia inhibition risk`, `HIGH — slow degradation`, `VERY HIGH — significant degradation limitation` |
| `advice` | `str` | Operational recommendation |
| `reference` | `str` | Drosg (2013) IEA Bioenergy Task 37, Section 3.2 |

**Operational thresholds**

| C/N Range | Status |
|-----------|--------|
| 20–30 | OPTIMAL — ideal stability |
| < 20 | LOW — add carbon-rich co-substrate |
| 30–35 | HIGH — slow degradation, add N-rich feedstock |
| > 35 | VERY HIGH — pre-treatment likely needed |

**Example**

```python
r = cn_ratio_from_composition(carbon_pct_of_vs=44.0, nitrogen_pct_of_vs=1.8)
r["cn_ratio"]   # 24.4
r["status"]     # "OPTIMAL"
```

---

### `olr_from_recipe()`

Calculates the Organic Loading Rate (OLR) in kg VS/m³/day from a feedstock
recipe and digester volume. The mass balance matches `blend_feedstocks()`.

```
OLR = Σ(wet_kg_i × DM_i × VS/DM_i) / digester_volume_m³
```

```python
def olr_from_recipe(
    recipe: list[dict],
    digester_volume_m3: float,
    feedstock_table: Optional[dict] = None,
) -> dict
```

**Parameters**

| Param | Description |
|-------|-------------|
| `recipe` | List of dicts, each with `name` (str) and `wet_tonnes` (float) |
| `digester_volume_m3` | Active digester working volume (m³) |
| `feedstock_table` | Optional dict matching the `_FEEDSTOCKS` format. If `None`, uses built-in defaults: Cattle slurry, Pig manure, Maize silage, Food waste, Grass silage, Sewage sludge, Chicken manure, Fat/grease trap. |

**Returns**

| Key | Type | Description |
|-----|------|-------------|
| `recipe_streams` | `list[dict]` | Per-feedstock VS contributions with `name` and `vs_kg_per_day` |
| `total_vs_kg_per_day` | `float` | Sum of VS across all recipe items (1 d.p.) |
| `digester_volume_m3` | `float` | Volume used in calculation |
| `olr_kg_vs_m3_day` | `float` | Organic loading rate (3 d.p.) |
| `status` | `str` | One of `UNDERLOADED`, `OPTIMAL`, `HIGH`, `OVERLOADED — acidification risk` |
| `advice` | `str` | Operational recommendation |
| `reference` | `str` | Weiland (2010) citation |
| `errors` | `list` | (returned only) List of unknown feedstock names |

**OLR thresholds**

| Range (kg VS/m³/day) | Status |
|----------------------|--------|
| < 1.5 | UNDERLOADED |
| 1.5–3.5 | OPTIMAL (Weiland 2010) |
| 3.5–4.5 | HIGH — monitor VFA / FOS/TAC |
| > 4.5 | OVERLOADED — acidification risk |

**Reference**

Weiland (2010). *Biogas production: current state and perspectives.*
Appl. Microbiol. Biotechnol., 85(4), 849–860. Optimal OLR: 2–4 kg VS/m³/day.

**Example**

```python
r = olr_from_recipe(
    recipe=[
        {"name": "Cattle slurry", "wet_tonnes": 30},
        {"name": "Maize silage",  "wet_tonnes": 15},
        {"name": "Food waste",    "wet_tonnes":  5},
    ],
    digester_volume_m3=2000,
)
print(r["olr_kg_vs_m3_day"], r["status"])
```

---

### `biodegradability_coefficient()`

Calculates the biodegradability coefficient η = empirical / theoretical from
a substrate class and a measured BMP value. A useful sanity check on empirical
BMP values.

```python
def biodegradability_coefficient(
    substrate_class: str,
    empirical_bmp_nl_per_kg_vs: float,
) -> dict
```

**Parameters**

| Param | Description |
|-------|-------------|
| `substrate_class` | Key from `_SUBSTRATE_FORMULAS` |
| `empirical_bmp_nl_per_kg_vs` | Measured BMP in NL CH₄ / kg VS |

**Returns**

| Key | Type | Description |
|-----|------|-------------|
| `substrate_class` | `str` | Substrate class used |
| `theoretical_bmp_nl_per_kg_vs` | `float` | Buswell theoretical maximum |
| `empirical_bmp_nl_per_kg_vs` | `float` | Input empirical value |
| `biodegradability_coefficient_eta` | `float` | Ratio η (3 d.p.) |
| `flag` | `str` | One of: `IMPOSSIBLE`, `GOOD`, `MODERATE`, `LOW` |
| `reference` | `str` | Angelidaki et al. (2009) and Buswell & Mueller (1952) |

**η interpretation**

| η | Meaning |
|---|---------|
| > 1.0 | Impossible — data error |
| 0.80–0.95 | Good — highly degradable (food waste, fat) |
| 0.50–0.80 | Moderate — lignocellulosic |
| < 0.50 | Low — recalcitrant, consider pre-treatment |

**Example**

```python
r = biodegradability_coefficient(
    substrate_class="carbohydrate_cellulose",
    empirical_bmp_nl_per_kg_vs=340,
)
r["biodegradability_coefficient_eta"]   # e.g. 0.752
```

---

## Group 2 — SCADA Statistical Fitting

All functions in this group return **placeholder results** when no CSV is
supplied. With real historian data they fit distribution parameters that can
replace the hardcoded simulation defaults.

### `fit_production_distribution()`

Fits Gaussian distribution parameters (mean, std) to historical grid injection
flow and biomethane purity from SCADA data.

```python
def fit_production_distribution(
    csv_path: Optional[str] = None,
) -> dict
```

**Parameters**

| Param | Description |
|-------|-------------|
| `csv_path` | Path to SCADA export CSV. If `None`, returns placeholders. |

**Expected CSV columns** (configurable via `COLUMN_MAP`):

| Column | Description |
|--------|-------------|
| `timestamp` | ISO datetime or Unix epoch |
| `grid_injection_nm3h` | Hourly avg grid injection flow (Nm³/h) |
| `biomethane_purity_pct` | Hourly avg CH₄ purity after upgrading |

**Returns (no-data mode)**

| Key | Type | Description |
|-----|------|-------------|
| `status` | `str` | `"NO_DATA"` |
| `message` | `str` | Instructions |
| `placeholder_values` | `dict` | Current script defaults: `mean_flow_nm3h: 98.0`, `std_flow_nm3h: 6.0`, `mean_purity_pct: 97.5`, `std_purity_pct: 0.3` |
| `how_to_use` | `str` | Code snippet |

**Returns (fitted mode)**

| Key | Type | Description |
|-----|------|-------------|
| `status` | `str` | `"FITTED"` |
| `n_days_total` | `int` | Number of daily records |
| `n_days_after_outlier_removal` | `int` | Count after 3‑sigma filtering |
| `warning` | `str` or `None` | Warning if history < 30 days |
| `fitted_parameters` | `dict` | `mean_flow_nm3h`, `std_flow_nm3h`, `mean_purity_pct`, `std_purity_pct` |
| `flagged_outliers` | `dict` | Lists of outlier day indices |
| `how_to_use` | `str` | Code snippet for replacing simulation defaults |

**Method**

- Resamples hourly data to daily averages.
- Removes outliers beyond 3σ (configurable via `OUTLIER_SIGMA`).
- Computes sample mean and standard deviation on cleaned data.
- Minimum recommended history: 30 days (configurable via `MIN_DAYS`).

---

### `fit_uptime_range()`

Fits the uptime percentile range (p5, p95) to replace the hardcoded
`random.uniform(0.92, 0.99)` in `_simulate_daily_production()`.

```python
def fit_uptime_range(
    csv_path: Optional[str] = None,
) -> dict
```

**Parameters**

| Param | Description |
|-------|-------------|
| `csv_path` | Path to SCADA export CSV. If `None`, returns placeholders. |

**Expected CSV columns** (one of):

| Column | Description |
|--------|-------------|
| `uptime_fraction` | Fractional uptime (0.0–1.0) |
| `uptime_pct` | Uptime as percentage (0–100) |
| `hours_online` + implicit `24` | Hours producing per day |

**Returns (no-data mode)**

| Key | Type | Description |
|-----|------|-------------|
| `status` | `str` | `"NO_DATA"` |
| `placeholder_values` | `dict` | `uptime_p5: 0.92`, `uptime_p95: 0.99` |
| `how_to_use` | `str` | Code snippet |

**Returns (fitted mode)**

| Key | Type | Description |
|-----|------|-------------|
| `status` | `str` | `"FITTED"` |
| `n_days` | `int` | Number of valid records |
| `fitted_parameters` | `dict` | `uptime_p5`, `uptime_p95`, `uptime_mean`, `uptime_median` |
| `how_to_use` | `str` | Code snippet |

**Method**

- Reads uptime from one of several supported column formats.
- Clamps values to [0, 1].
- Computes empirical percentiles (default p5/p95 via `P_LOW`/`P_HIGH`).
- Uses percentiles (not normality assumption) because uptime is left-skewed.

---

### `fit_steady_state_plant_state()`

Fits the steady-state operating point from historian data, returning median
values for each monitored variable. Replaces the hardcoded `_plant_state` dict.

```python
def fit_steady_state_plant_state(
    csv_path: Optional[str] = None,
) -> dict
```

**Parameters**

| Param | Description |
|-------|-------------|
| `csv_path` | Path to SCADA export CSV. If `None`, returns placeholders. |

**Fittable columns** (configurable via `COLUMN_MAP`):

| SCADA tag | Plant state key |
|-----------|-----------------|
| `digester_temp_c` | `digester_temp_c` |
| `digester_ph` | `digester_ph` |
| `vfa_mmol_l` | `vfa_mmol_l` |
| `alkalinity_mg_caco3_l` | `alkalinity_mg_caco3_l` |
| `nh4_mg_l` | `nh4_mg_l` |
| `biogas_flow_nm3h` | `biogas_flow_nm3h` |
| `ch4_pct` | `ch4_pct` |
| `co2_pct` | `co2_pct` |
| `h2s_ppm` | `h2s_ppm` |
| `o2_ppm` | `o2_ppm` |
| `biomethane_purity_pct` | `biomethane_purity_pct` |
| `grid_injection_nm3h` | `grid_injection_nm3h` |
| `organic_load_kg_vs_d` | `organic_load_kg_vs_d` |
| `hydraulic_retention_days` | `hydraulic_retention_days` |
| `digestate_ts_pct` | `digestate_ts_pct` |

**Returns (no-data mode)**

| Key | Type | Description |
|-----|------|-------------|
| `status` | `str` | `"NO_DATA"` |
| `message` | `str` | Documentation |
| `current_defaults` | `dict` | Current hardcoded `_plant_state` values |

**Returns (fitted mode)**

| Key | Type | Description |
|-----|------|-------------|
| `status` | `str` | `"FITTED"` |
| `n_rows_read` | `int` | Total rows in CSV |
| `columns_fitted` | `list[str]` | State keys fitted from data |
| `columns_missing` | `list[str]` | State keys not found in CSV |
| `fitted_plant_state` | `dict` | Median values for each fitted key (3 d.p.) |
| `how_to_use` | `str` | Instructions |

**Method**

- Matches CSV column names to plant state keys via `COLUMN_MAP`.
- Uses `pd.to_numeric` with `errors="coerce"` and drops NaN.
- Takes **median** (robust to upsets), not mean.

---

### `validate_bmp_against_scada()`

Validates a feedstock BMP value against three sources:
(a) Buswell theoretical maximum,
(b) a lab-measured BMP assay,
(c) a back-calculated yield from SCADA production data.

```python
def validate_bmp_against_scada(
    feedstock_name: str,
    substrate_class: str,
    measured_bmp_nl_per_kg_vs: Optional[float] = None,
    csv_path: Optional[str] = None,
) -> dict
```

**Parameters**

| Param | Description |
|-------|-------------|
| `feedstock_name` | Name as it appears in `_FEEDSTOCKS` (e.g. `"Maize silage"`) |
| `substrate_class` | Corresponding key in `_SUBSTRATE_FORMULAS` |
| `measured_bmp_nl_per_kg_vs` | Optional lab BMP assay result (NL CH₄ / kg VS) |
| `csv_path` | Optional SCADA CSV with columns `feedstock_vs_kg_d`, `biogas_nm3_d`, `ch4_pct` |

**Returns**

| Key | Type | Description |
|-----|------|-------------|
| `feedstock_name` | `str` | Input feedstock name |
| `substrate_class` | `str` | Input substrate class |
| `script_bmp` | `int` or `str` | Current value in `_FEEDSTOCKS` |
| `theoretical_bmp_buswell` | `float` | (if class valid) Buswell maximum |
| `script_vs_theoretical_eta` | `float` | Ratio script / theoretical (3 d.p.) |
| `measured_bmp_lab` | `float` | (if provided) Lab assay value |
| `scada_back_calculated_bmp` | `float` | (if CSV supplied) Median yield from SCADA (1 d.p.) |
| `scada_status` | `str` | Status of SCADA back-calculation |
| `recommendation` | `str` | Suggested BMP replacement |

**SCADA back-calculation**

When CSV is supplied with columns `feedstock_vs_kg_d`, `biogas_nm3_d`, and `ch4_pct`:

```
yield = (biogas_nm3_d × ch4_pct) / feedstock_vs_kg_d
```

The median of per-day yields is returned as `scada_back_calculated_bmp`.

**Example**

```python
r = validate_bmp_against_scada(
    feedstock_name="Maize silage",
    substrate_class="carbohydrate_cellulose",
)
```

---

## Group 3 — AD4 Simulator Calibration

These functions calibrate the AM2 model parameters to observed production data.
The key insight is that **k6 can be solved analytically** (no iterative
optimisation), making this suitable for datasets with 10,000+ rows.

### `ParameterSource` (Enum)

```python
class ParameterSource(Enum):
    FITTED_TO_DATA        = "fitted_to_data"        # Calibrated to Q_CH4
    FROM_DATASET          = "from_dataset"           # Direct from CSV columns
    LITERATURE_DEFAULT    = "literature_default"     # Benyahia / published values
    BUSWELL_CONSTRAINED   = "buswell_constrained"    # Bounded by Buswell chemistry
```

### `CalibrationConfidence`

Tracks which parameters have data support versus literature defaults.

```python
class CalibrationConfidence:
    def __init__(self)
    def add(self, name: str, value: Any, source: ParameterSource,
            bounds: tuple = None, notes: str = "")
    def to_dict(self) -> dict
    def summary(self) -> str
```

**`add()` parameters**

| Param | Description |
|-------|-------------|
| `name` | Parameter name (e.g. `"k6"`, `"mu2_max"`) |
| `value` | Parameter value |
| `source` | `ParameterSource` enum member |
| `bounds` | Optional `(min, max)` tuple |
| `notes` | Free-text annotation |

**`to_dict()`**

Returns the internal `parameters` dict keyed by name, each with `value`,
`source`, `bounds`, and `notes`.

**`summary()`**

Returns a formatted multi-line string with one line per parameter, annotated
with `[FITTED]`, `[FROM DATA]`, `[BUSWELL BOUNDED]`, or `[LITERATURE DEFAULT]`.

**Example**

```python
conf = CalibrationConfidence()
conf.add("k6", 612.3, ParameterSource.FITTED_TO_DATA, bounds=(100, 2500),
         notes="Changed from default 453")
conf.add("Ki2", 256.0, ParameterSource.LITERATURE_DEFAULT,
         notes="UNVALIDATED - Benyahia default")
print(conf.summary())
```

---

### `fit_am2_k6_only()`

Validates that the AD4 model can match observed Q_CH₄, or finds the analytical
k6 adjustment needed. This is ~20× faster than iterative optimisation because
k6 does not appear in the ODE system — only in the methane output equation.

```python
def fit_am2_k6_only(
    D_measured: float,
    S1_in_measured: float,
    Q_CH4_measured: float,
    digester_volume_m3: float = 2000.0,
    params: AD4Params = None,
) -> dict
```

**Parameters**

| Param | Default | Description |
|-------|---------|-------------|
| `D_measured` | — | Dilution rate from HRT (d⁻¹) |
| `S1_in_measured` | — | Influent COD from feedstock (g/L) |
| `Q_CH4_measured` | — | Measured methane flow (Nm³/d) |
| `digester_volume_m3` | `2000.0` | Digester volume for unit conversion |
| `params` | `None` | `AD4Params` instance (uses Benyahia defaults if `None`) |

**Method**

1. Convert Q_CH₄ from Nm³/d → mL/L/d: `Q_CH4 × 1000 / volume`.
2. Run the ODE once (200 days) with default params to get steady-state S₂ and X₂
   (k6 does not appear in the ODEs, so this gives correct internal states
   regardless of the true k6).
3. Compute methanogen activity: `μ₂(S₂) × X₂`.
4. Solve analytically: `k6 = Q_CH₄ / (μ₂(S₂) × X₂)`.
5. Clip to physical bounds [100, 2500].

**Returns**

| Key | Type | Description |
|-----|------|-------------|
| `digester_volume_m3` | `float` | Volume used |
| `Q_CH4_input_Nm3_per_d` | `float` | Input methane flow |
| `Q_CH4_converted_mL_per_L_per_d` | `float` | Converted to internal units (2 d.p.) |
| `Q_CH4_default_Nm3_per_d` | `float` | Default model output with k6=453 (1 d.p.) |
| `Q_CH4_simulated_Nm3_per_d` | `float` | Re-computed with fitted k6 (1 d.p.) |
| `model_vs_data_ratio` | `float` | Data / default-model ratio (1 d.p.) |
| `k6_default` | `float` | Default k6 value (453.0) |
| `k6_fitted` | `float` | Analytically fitted k6 (1 d.p.) |
| `k6_change_pct` | `float` | Relative change from default (1 d.p.) |
| `error_pct` | `float` | Validation error (2 d.p.) |
| `status` | `str` | One of: `INCOMPATIBLE`, `POOR`, `HIGH`, `OK` |
| `diagnosis` | `str` | (conditionally present) Detailed interpretation |
| `calibration_confidence` | `str` | Honest assessment string |
| `confidence_report` | `dict` | `CalibrationConfidence.to_dict()` |
| `error` | `str` | (on failure) `"washout"` or `"AD4 simulator not available"` |

**Status classification**

| Condition | Status |
|-----------|--------|
| model_ratio > 10 | `INCOMPATIBLE — data 10×+ higher than model can produce` |
| k6_fitted > 1500 | `POOR — requires extreme k6, model mismatch likely` |
| k6_fitted > 800 | `HIGH — k6 above typical range` |
| Otherwise | `OK — within operational range` |

**Example**

```python
r = fit_am2_k6_only(
    D_measured=0.05,
    S1_in_measured=25.0,
    Q_CH4_measured=98.0,
    digester_volume_m3=2000,
)
print(r["k6_fitted"], r["status"])
```

---

### `fit_am2_from_dataset()`

Runs honest calibration on a CSV dataset, calling `fit_am2_k6_only()` for
each row.

```python
def fit_am2_from_dataset(csv_path: str) -> dict
```

**Parameters**

| Param | Description |
|-------|-------------|
| `csv_path` | Path to CSV with daily operational data |

**Expected CSV columns**

| Column | Description |
|--------|-------------|
| `HRT_days` or `dilution_rate_d1` | Hydraulic retention time or dilution rate |
| `influent_COD_g_L` or `vs_reduction_pct` | Influent organic load |
| `methane_mL_per_L_per_d` | (optional) Methane in internal units |
| `biogas_m3_d` + `ch4_pct` | (optional) Calculates methane from biogas |

**Returns**

| Key | Type | Description |
|-----|------|-------------|
| `n_rows_calibrated` | `int` | Number of successfully calibrated rows |
| `results` | `list[dict]` | Per-row fitted k6, D, error_pct |
| `confidence` | `dict` | `CalibrationConfidence.to_dict()` |

---

### `fit_am2_from_csv()`

Calibrates AD4 to production data from any CSV format with auto-detection of
columns. Designed to handle common biogas dataset formats (Indian Biogas
Dataset, Mendeley *ri_flex.csv*, plant SCADA exports).

```python
def fit_am2_from_csv(
    csv_path: str,
    digester_volume_m3: float = 2000.0,
) -> dict
```

**Parameters**

| Param | Default | Description |
|-------|---------|-------------|
| `csv_path` | — | Path to CSV file |
| `digester_volume_m3` | `2000.0` | Digester working volume |

**Column auto-detection**

| Detected | Patterns searched |
|----------|-------------------|
| HRT | `hrt`, `retention`, `days` |
| Biogas flow | `biogas_production`, `biogas_flow`, `biogas_m3` |
| CH₄ fraction | `methane`, `ch4`, `ch4_pct` |
| Feedstock | `pig manure`, `chicken litter`, `cassava`, `bagasse`, `kitchen food`, `energy grass`, `banana`, `alcohol waste`, `municipal`, `fish waste` |
| Water | `water (l)`, `water_l`, `water` |

**Missing column fallbacks**

| Missing Column | Fallback |
|---------------|----------|
| CH₄ % | Assumes 60% (typical mesophilic manure) |
| HRT | Assumes 18 days (typical farm-scale) |
| Feedstock columns | Assumes S1_in = 25.0 g COD/L |

**CH₄ auto-detect**

If a CH₄ column is found, the function checks whether values are fractions
(median < 1.0) or percentages, and auto-scales to 0–100 before multiplying
with biogas flow.

**S1_in estimation from feedstock columns**

```
COD ~ 30 g/L for raw manure/food waste mix
S1_in = total_feedstock × 30.0 / (total_feedstock + water_kg + 1)
Clipped to [5, 50] g COD/L
```

**Returns**

| Key | Type | Description |
|-----|------|-------------|
| `source_file` | `str` | Path to input CSV |
| `digester_volume_m3` | `float` | Volume used |
| `n_rows_total` | `int` | Total rows in CSV |
| `n_rows_calibrated` | `int` | Valid calibration count |
| `n_rows_skipped` | `int` | Rows excluded (negative/missing values, washout) |
| `k6_recommended` | `float` | **Median k6** — the value to pass to `generate_model_card.py` (1 d.p.) |
| `k6_median` | `float` | Same as recommended (1 d.p.) |
| `k6_mean` | `float` | Mean k6 across valid rows (1 d.p.) |
| `k6_std` | `float` | Standard deviation of k6 (1 d.p.) |
| `k6_cv_pct` | `float` | Coefficient of variation % (1 d.p.) |
| `k6_p10` | `float` | 10th percentile (1 d.p.) |
| `k6_p90` | `float` | 90th percentile (1 d.p.) |
| `k6_min` / `k6_max` | `float` | Range extents (1 d.p.) |
| `feedstock_consistency` | `str` | Interpretation of CV: `GOOD` (< 10%), `MODERATE` (10–25%), `HIGH VARIATION` (> 25%) |
| `columns_detected` | `dict` | Auto-detected column names: `hrt`, `biogas`, `ch4`, `water`, `feed` |
| `results_sample` | `list[dict]` | First 5 per-row results |
| `skip_sample` | `list[dict]` | First 5 skipped-row details |
| `calibration_note` | `str` | Guidance on using `k6_recommended` |

**Performance note**

k6 is solved analytically (one ODE run per row), not iteratively. Suitable
for datasets with 10,000+ rows.

**Example**

```python
r = fit_am2_from_csv("scada_export.csv", digester_volume_m3=2000)
print(f"Recommended k6: {r['k6_recommended']} ± {r['k6_std']}")
print(f"Consistency: {r['feedstock_consistency']}")
```

---

## References

| # | Source |
|---|--------|
| [1] | Buswell & Mueller (1952). *Mechanism of methane fermentation.* Ind. Eng. Chem., 44(3), 550–552. |
| [2] | VDI 4630:2016. *Fermentation of organic materials.* Verein Deutscher Ingenieure, Düsseldorf. |
| [3] | Lossie & Puetz (2008). *Targeted control of biogas plants with FOS/TAC.* Hach-Lange GmbH. |
| [4] | Amon et al. (2007). *Methane production through anaerobic digestion of various energy crops.* Bioresource Technology, 98(17), 3204–3212. |
| [5] | Angelidaki et al. (2009). *Defining the biomethane potential (BMP) of solid organic wastes.* Water Sci. Tech., 59(5), 927–934. |
| [6] | Weiland (2010). *Biogas production: current state and perspectives.* Appl. Microbiol. Biotechnol., 85(4), 849–860. |
| [7] | Perry & Green (2008). *Perry's Chemical Engineers' Handbook,* 8th ed., Table 2-150. McGraw-Hill. |
| [8] | Drosg (2013). *Process Monitoring in Biogas Plants.* IEA Bioenergy Task 37. |
| [9] | Bernard et al. (2001). *Dynamical model development and parameter identification for an anaerobic wastewater treatment process.* Biotechnol. Bioeng., 75(4), 424–438. |
| [10] | Benyahia et al. (2012). *Modeling of anaerobic digestion.* Chem. Eng. J., 197, 469–479. |
