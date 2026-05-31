# Biomethane Operations CLI — API Reference

**Module:** `src/bio_cli.py`  
**Entry point:** `python src/bio_cli.py <command> [options]`  
**Python API:** `from bio_cli import ...` (all functions directly importable)  
**Dependencies:** `tabulate`, `pandas`+`numpy` (optional, for CSV-based calibration)

---

## Usage Examples

```bash
# Get current plant state
python src/bio_cli.py get-state

# Update a sensor reading
python src/bio_cli.py update --temp 38.5 --ph 7.2

# Check alerts (industrial profile)
python src/bio_cli.py check-alerts

# Check alerts with lab-scale thresholds
python src/bio_cli.py --lab-scale check-alerts

# Blend two feedstocks
python src/bio_cli.py blend "Maize silage:5" "Cattle slurry:10"

# Look up operational reference
python src/bio_cli.py reference fos_tac

# Buswell BMP calculation
python src/bio_cli.py calibrate buswell --c 6 --h 12 --o 6

# Simulate AD4 steady state
python src/bio_cli.py simulate run --D 0.045 --S1 25 --days 100

# JSON output
python src/bio_cli.py --format json get-state

# CSV output for simulation
python src/bio_cli.py simulate run --D 0.045 --S1 25 --format csv --output results.csv
```

---

## Global Flags

Applied **before** the subcommand on the command line.

| Flag | Default | Description |
|------|---------|-------------|
| `--format` | `table` | Output format: `json`, `table` (most commands), `text`, `csv` (simulate) |
| `--lab-scale` | off | Activate lab-scale alert thresholds (20–30 °C temp, Benyahia VFA 30/80 mmol/L) |
| `--site PATH` | `PROJECT_ROOT/site_config.json` | Path to site configuration JSON (overrides auto-detected path) |

The `--format` flag can also be passed **per-subcommand**; subcommand-level `--format` takes precedence over the global flag.

**Resolution order for `--lab-scale`:**

1. `site_config.json` → `lab_scale` key
2. CLI `--lab-scale` flag (overrides site_config)

---

## Output Formats

| Format | Available For | Behaviour |
|--------|---------------|-----------|
| `json` | All commands | Pretty-printed JSON dict |
| `table` | Most commands (default) | `tabulate` output — state key/value, alert/calibration records, blend streams |
| `text` | `simulate` subcommands | Human-readable labelled output with interpretation |
| `csv` | `simulate run`, `simulate perturb` | Time-series CSV written to `--output PATH` |

When `--format table` encounters a structure it doesn't recognise, it falls back to JSON.

---

## Plant State Defaults

`PLANT_STATE_DEFAULTS` in `src/bio_cli.py:151`.

| Parameter | Default | Unit |
|-----------|---------|------|
| `digester_temp_c` | 37.2 | °C |
| `digester_ph` | 7.1 | — |
| `vfa_mmol_l` | 8.5 | mmol/L |
| `alkalinity_mg_caco3_l` | 2800.0 | mg CaCO₃/L |
| `nh4_mg_l` | 420.0 | mg/L |
| `biogas_flow_nm3h` | 142.0 | Nm³/h |
| `ch4_pct` | 62.3 | % |
| `co2_pct` | 36.1 | % |
| `h2s_ppm` | 380.0 | ppm |
| `o2_ppm` | 180.0 | ppm |
| `biomethane_purity_pct` | 97.4 | % |
| `grid_injection_nm3h` | 98.0 | Nm³/h |
| `organic_load_kg_vs_d` | 2.8 | kg VS/d |
| `hydraulic_retention_days` | 22.0 | days |
| `digestate_ts_pct` | 3.2 | % |

---

## Feedstock Table

`FEEDSTOCKS` in `src/bio_cli.py:169`.

| Feedstock | BMP (NL/kg VS) | C/N | DM (%) | VS/DM |
|-----------|:--------------:|:---:|:------:|:-----:|
| Cattle slurry | 200 | 11 | 8 | 0.80 |
| Pig manure | 310 | 8 | 6 | 0.78 |
| Maize silage | 340 | 25 | 33 | 0.94 |
| Food waste | 480 | 16 | 25 | 0.88 |
| Grass silage | 290 | 18 | 30 | 0.90 |
| Sewage sludge | 220 | 9 | 4 | 0.75 |
| Chicken manure | 350 | 7 | 25 | 0.76 |
| Fat/grease trap | 900 | 30 | 80 | 0.97 |

---

## Alert Thresholds

`DEFAULT_ALERT_THRESHOLDS` in `src/bio_cli.py:62`.

| Parameter | Lab-scale | Industrial |
|-----------|-----------|------------|
| **digester_temp_c** | Low: 20, High: 30 °C | Low: 35, High: 40 °C |
| **vfa_mmol_l** | Medium: 30, High: 80 | Medium: 8, High: 15 |
| **nh4_mg_l** | Medium: 300, High: 800 | Medium: 300, High: 800 |
| **h2s_ppm** | Medium: 200, High: 500 | Medium: 200, High: 500 |
| **digester_ph** | Low: 6.8, High: 7.8 | Low: 6.8, High: 7.8 |
| **biomethane_purity_pct** | Min: 95 | Min: 95 |
| **o2_ppm** | Max: 500 | Max: 500 |

Thresholds can be overridden per-key in `site_config.json` → `alert_thresholds.<profile>` (Option A behaviour). Values not present in config fall back to the defaults above.

---

## Substrate Classes (Buswell)

`_SUBSTRATE_FORMULAS` in `src/bio_cli.py:199`.

| Class | Formula | MW | C | H | O | N | S |
|-------|---------|:--:|:-:|:-:|:-:|:-:|:-:|
| `carbohydrate_cellulose` | C₆H₁₀O₅ | 162.14 | 6 | 10 | 5 | 0 | 0 |
| `carbohydrate_glucose` | C₆H₁₂O₆ | 180.16 | 6 | 12 | 6 | 0 | 0 |
| `protein_generic` | C₅H₇O₂N | 113.12 | 5 | 7 | 2 | 1 | 0 |
| `lipid_tripalmitin` | C₅₁H₉₈O₆ | 807.32 | 51 | 98 | 6 | 0 | 0 |
| `lipid_triolein` | C₅₇H₁₀₄O₆ | 885.43 | 57 | 104 | 6 | 0 | 0 |
| `volatile_fatty_acid_acetic` | C₂H₄O₂ | 60.05 | 2 | 4 | 2 | 0 | 0 |

---

## Commands

### `get-state`

Read the latest clean plant state from the StateBuffer (`plant_state.sqlite`).

```
python src/bio_cli.py get-state [--format json|table]
```

**Python:** `get_state() → dict`

Returns all 16 plant state parameters merged with `PLANT_STATE_DEFAULTS`. Falls back to defaults if StateBuffer is unavailable or empty.

---

### `update`

Write sensor readings into the StateBuffer. Values pass through CUSUM anomaly detection (spikes rejected silently).

```
python src/bio_cli.py update [--temp FLOAT] [--ph FLOAT] [--vfa FLOAT]
                             [--nh4 FLOAT] [--h2s FLOAT] [--purity FLOAT]
                             [--flow FLOAT] [--grid FLOAT]
                             [--format json|table]
```

**Python:** `update_state(updates: dict) → dict`

**Arguments:**

| Flag | Maps to | Unit |
|------|---------|------|
| `--temp` | `digester_temp_c` | °C |
| `--ph` | `digester_ph` | — |
| `--vfa` | `vfa_mmol_l` | mmol/L |
| `--nh4` | `nh4_mg_l` | mg/L |
| `--h2s` | `h2s_ppm` | ppm |
| `--purity` | `biomethane_purity_pct` | % |
| `--flow` | `biogas_flow_nm3h` | Nm³/h |
| `--grid` | `grid_injection_nm3h` | Nm³/h |

**Response shape:**
```json
{
  "updated": ["digester_temp_c"],
  "rejected": [],
  "accepted_count": 1,
  "rejected_count": 0,
  "note": "Values passed through CUSUM — spikes are silently discarded by StateBuffer.",
  "state": { ... }
}
```

Rejected keys include unknown parameters and non-numeric values.

---

### `check-alerts`

Check all state parameters against the active threshold profile and return triggered alerts. Alerts are automatically logged to the CLI database (`alert_log` table).

```
python src/bio_cli.py check-alerts [--format json|table]
```

**Python:** `check_alerts() → list[dict]`

**Threshold checks performed (in order):**

1. **digester_temp_c** — HIGH if outside low/high range
2. **digester_ph** — HIGH if outside 6.8–7.8
3. **vfa_mmol_l** — MEDIUM if above medium threshold, HIGH if above high threshold (acidification risk)
4. **nh4_mg_l** — MEDIUM if above medium threshold, HIGH if above high threshold (inhibition risk)
5. **h2s_ppm** — MEDIUM if above medium threshold, HIGH if above high threshold (toxicity/elevated)
6. **biomethane_purity_pct** — HIGH if below min spec
7. **o2_ppm** — HIGH if above max (air ingress)

Returns `[]` when no thresholds are breached.

---

### `alert-history`

Retrieve recent alert log entries from the CLI database.

```
python src/bio_cli.py alert-history [--limit INT] [--format json|table]
```

**Python:** `get_alert_history(limit: int = 10) → dict`

| Flag | Default | Description |
|------|---------|-------------|
| `--limit` | 10 | Max records to return |

**Response shape:**
```json
{
  "total": 3,
  "records": [
    { "id": 1, "timestamp": "2026-05-31T12:00:00", "parameter": "digester_temp_c", "severity": "HIGH", "message": "..." }
  ]
}
```

---

### `blend`

Calculate blended feedstock properties from a recipe.

```
python src/bio_cli.py blend <recipe>... [--olr FLOAT] [--format json|table]
```

**Python:** `blend_feedstocks(recipe: list[dict], target_olr: float = 2.5) → dict`

| Argument | Description |
|----------|-------------|
| `recipe` | One or more `"Feedstock Name:wet_tonnes"` pairs (positional) |
| `--olr` | Target organic loading rate (default: 2.5 kg VS/m³/d) |

**Example:**
```bash
python src/bio_cli.py blend "Maize silage:5" "Cattle slurry:10"
```

**Response shape:**
```json
{
  "streams": [
    { "name": "Maize silage", "wet_tonnes": 5.0, "vs_kg": 1551.0, "bmp": 340 },
    { "name": "Cattle slurry", "wet_tonnes": 10.0, "vs_kg": 640.0, "bmp": 200 }
  ],
  "total_vs_kg": 2191.0,
  "estimated_ch4_yield_nl_kg_vs": 260.4,
  "blend_cn_ratio": 19.2,
  "status": "ADJUST_CN"
}
```

**`status` field:**
- `OK` — C/N ratio between 20–30
- `ADJUST_CN` — C/N outside ideal range (co-substrate adjustment recommended)

---

### `reference`

Look up operational guidance by topic.

```
python src/bio_cli.py reference <topic> [--format json|table]
```

**Python:** `get_reference(topic: str) → dict`

| Topic | Aliases | Description |
|-------|---------|-------------|
| `fos_tac` | `fos tac`, `acidification`, `vfa/alkalinity` | FOS/TAC ratio buffering capacity |
| `temperature` | `temp`, `digester temp`, `operating temperature` | Mesophilic/thermophilic ranges |
| `olr` | `loading rate`, `vs load` | Organic loading rate guidelines |
| `cn_ratio` | `carbon nitrogen`, `c/n` | C/N ratio targets and risks |

**Response:**
```json
{ "topic": "fos_tac", "guidance": "FOS/TAC ratio measures buffering capacity..." }
```

Returns `"No reference found for this topic"` for unknown topics.

---

### `kpi`

Generate a KPI summary for a given period.

```
python src/bio_cli.py kpi [--period daily|weekly|monthly] [--format json|table]
```

**Python:** `get_kpi_summary(period: str = "daily") → dict`

| Flag | Default | Description |
|------|---------|-------------|
| `--period` | `daily` | Aggregation period |

**Response shape:**
```json
{
  "period": "daily",
  "date": "2026-05-31T12:00:00",
  "grid_injection_nm3": 2352.0,
  "energy_kwh": 24192.0,
  "avg_purity_pct": 97.4,
  "uptime_pct": 95.2
}
```

**Calculation notes:**
- Production = `grid_injection_nm3h * hours * uptime`
- Hours: daily=24, weekly=168, monthly=720
- Energy = `production_nm3 * 10.55 * (purity / 100)`
- Uptime is sampled uniformly from 92–99 % (or fitted values from calibration)

---

### `list-feedstocks`

List all available feedstocks with their BMP, C/N, DM, and VS/DM values.

```
python src/bio_cli.py list-feedstocks [--format json|table]
```

**Python:** `list_feedstocks() → dict`

---

### `calibrate` — Subcommands

All calibration subcommands support `--save` (persist to `calibration_log` table) and `--notes TEXT`.

> **Calibration is manual.** Parameters are edited in `biomethane_calibration.py`
> (Buswell formulas, C/N tables, k6 bounds) and applied by re-running the
> appropriate `calibrate` subcommand. There is no AI-driven or automated tuning;
> every calibration is an explicit operator action.

---

#### `calibrate buswell`

Buswell BMP calculation from elemental composition.

```
python src/bio_cli.py calibrate buswell --c FLOAT --h FLOAT --o FLOAT
                                        [--n FLOAT] [--s FLOAT]
                                        [--save] [--notes TEXT]
```

**Python:** `buswell_bmp(c, h, o, n=0.0, s=0.0) → dict`

| Flag | Default | Description |
|------|---------|-------------|
| `--c` | 6 | Carbon atoms |
| `--h` | 12 | Hydrogen atoms |
| `--o` | 6 | Oxygen atoms |
| `--n` | 0 | Nitrogen atoms |
| `--s` | 0 | Sulfur atoms |

**Reactions (Buswell 1952):**

```
C_c H_h O_o N_n S_s + (c - h/4 + o/2 + 3n/4 + s/2)H2O →
       (c/2 - h/8 + o/4 + 3n/8 + s/4)CO2 +
       (c/2 + h/8 - o/4 - 3n/8 - s/4)CH4 +
       nNH3 + sH2S
```

**Response:**
```json
{
  "substrate_formula": "C6H12O6N0S0",
  "ch4_mol_per_mol_substrate": 3.0,
  "co2_mol_per_mol_substrate": 3.0,
  "h2o_consumed_mol": 0.0,
  "ch4_nl_per_mol_substrate": 72.12,
  "ch4_fraction_pct": 50.0,
  "co2_fraction_pct": 50.0
}
```

---

#### `calibrate buswell-class`

Buswell BMP calculated from a named substrate class.

```
python src/bio_cli.py calibrate buswell-class <substrate_class>
                                              [--save] [--notes TEXT]
```

**Python:** `buswell_bmp_by_class(substrate_class: str) → dict`

| Positional | Description |
|------------|-------------|
| `substrate_class` | One of the six substrate classes (see table above) |

**Response:** Same as `buswell` plus:
```json
{
  "substrate_class": "carbohydrate_glucose",
  "molecular_weight": 180.16,
  "bmp_nl_per_g_vs": 0.4,
  "bmp_nl_per_kg_vs": 400.0
}
```

Returns `{"error": ...}` with `available` list for unknown classes.

---

#### `calibrate energy-factor`

Calculate the energy conversion factor (kWh/Nm³) from biomethane CH₄ fraction.

```
python src/bio_cli.py calibrate energy-factor [--purity FLOAT]
                                              [--save] [--notes TEXT]
```

**Python:** `calculate_energy_conversion_factor(ch4_fraction: float = 0.974) → dict`

| Flag | Default | Description |
|------|---------|-------------|
| `--purity` | 0.974 | CH₄ fraction (decimal, e.g. 0.974) |

**Constants used:**
- LHV CH₄ at STP: 35.88 MJ/Nm³
- STP → NTP correction: 273.15 / 288.15 ≈ 0.9479
- MJ → kWh: 1/3.6 ≈ 0.2778
- Script value (reference): 10.55 kWh/Nm³

**Response:**
```json
{
  "ch4_fraction_input": 0.974,
  "LHV_pure_CH4_MJ_per_Nm3_STP": 35.88,
  "STP_to_NTP_correction": 0.9479,
  "LHV_pure_CH4_MJ_per_Nm3_NTP": 37.846,
  "LHV_biomethane_MJ_per_Nm3": 36.858,
  "LHV_biomethane_kWh_per_Nm3": 10.238,
  "script_current_value_kWh_per_Nm3": 10.55,
  "difference_pct": -2.95
}
```

---

#### `calibrate cn`

Calculate C/N ratio from elemental composition of volatile solids.

```
python src/bio_cli.py calibrate cn --carbon FLOAT --nitrogen FLOAT
                                   [--save] [--notes TEXT]
```

**Python:** `cn_ratio_from_composition(carbon_pct_of_vs, nitrogen_pct_of_vs) → dict`

| Flag | Required | Description |
|------|----------|-------------|
| `--carbon` | Yes | Carbon percentage of VS |
| `--nitrogen` | Yes | Nitrogen percentage of VS |

**Status classification:**
- `OPTIMAL` — C/N 20–30
- `LOW — ammonia inhibition risk` — C/N < 20
- `HIGH — slow degradation` — C/N 30–35
- `VERY HIGH — significant degradation limitation` — C/N > 35

**Response:**
```json
{
  "carbon_pct_vs": 45.0,
  "nitrogen_pct_vs": 2.5,
  "cn_ratio": 18.0,
  "status": "LOW — ammonia inhibition risk",
  "advice": "C/N = 18.0. Add carbon-rich co-substrate."
}
```

---

#### `calibrate olr`

Calculate organic loading rate from a feedstock recipe and digester volume.

```
python src/bio_cli.py calibrate olr <recipe>... --volume FLOAT
                                      [--save] [--notes TEXT]
```

**Python:** `olr_from_recipe_calc(recipe: list[dict], digester_volume_m3: float) → dict`

| Argument | Description |
|----------|-------------|
| `recipe` | One or more `"Feedstock:tonnes"` pairs (positional) |
| `--volume` | Digester volume in m³ (required) |

**Status classification:**
- `UNDERLOADED` — OLR < 1.5
- `OPTIMAL` — OLR 1.5–3.5
- `HIGH` — OLR 3.5–4.5
- `OVERLOADED — acidification risk` — OLR > 4.5

---

#### `calibrate biodegradability`

Calculate the biodegradability coefficient η = BMP_empirical / BMP_theoretical (Buswell).

```
python src/bio_cli.py calibrate biodegradability <substrate_class>
                                                 --empirical FLOAT
                                                 [--save] [--notes TEXT]
```

**Python:** `biodegradability_coefficient(substrate_class, empirical_bmp_nl_per_kg_vs) → dict`

| Argument | Description |
|----------|-------------|
| `substrate_class` | Positional — one of the six substrate classes |
| `--empirical` | Lab-measured BMP in NL/kg VS |

**η classification:**
- `> 1.0` → IMPOSSIBLE (empirical exceeds theoretical maximum)
- `≥ 0.80` → GOOD — highly degradable
- `≥ 0.50` → MODERATE — partially recalcitrant
- `< 0.50` → LOW — recalcitrant

---

#### `calibrate fit-production`

Fit a Gaussian production distribution from SCADA historian CSV. Requires `pandas` + `numpy`.

```
python src/bio_cli.py calibrate fit-production [csv] [--save] [--notes TEXT]
```

**Python:** `fit_production_distribution(csv_path: Optional[str] = None) → dict`

**CSV columns expected:** `flow` & `purity` **or** `grid_injection_nm3h` & `biomethane_purity_pct`.

**Response (fitted):**
```json
{
  "status": "FITTED",
  "n_rows": 8760,
  "fitted_parameters": {
    "mean_flow_nm3h": 97.5,
    "std_flow_nm3h": 5.8,
    "mean_purity_pct": 97.452,
    "std_purity_pct": 0.287
  }
}
```

Without CSV/pandas returns placeholder defaults and usage hint.

---

#### `calibrate fit-uptime`

Fit uptime distribution (5th/95th percentiles) from SCADA CSV.

```
python src/bio_cli.py calibrate fit-uptime [csv] [--save] [--notes TEXT]
```

**Python:** `fit_uptime_range(csv_path: Optional[str] = None) → dict`

**CSV column expected:** `uptime_fraction`, `uptime_pct`, or `uptime` (0–1 range).

---

#### `calibrate fit-state`

Fit steady-state plant state (median of each parameter) from SCADA historian CSV.

```
python src/bio_cli.py calibrate fit-state [csv] [--save] [--notes TEXT]
```

**Python:** `fit_steady_state_plant_state(csv_path: Optional[str] = None) → dict`

**CSV columns mapped:**
- `digester_temp_c`, `digester_ph`, `vfa_mmol_l`, `alkalinity_mg_caco3_l`, `nh4_mg_l`
- `biogas_flow_nm3h`, `ch4_pct`, `co2_pct`, `h2s_ppm`, `o2_ppm`
- `biomethane_purity_pct`, `grid_injection_nm3h`, `organic_load_kg_vs_d`
- `hydraulic_retention_days`, `digestate_ts_pct`

Returns fitted medians for any columns present in the CSV, falling back to `PLANT_STATE_DEFAULTS` for missing columns.

---

#### `calibrate validate-bmp`

Cross-validate a feedstock's BMP against Buswell theory and/or SCADA back-calculation.

```
python src/bio_cli.py calibrate validate-bmp <feedstock> <substrate_class>
                                             [--lab FLOAT] [csv]
                                             [--save] [--notes TEXT]
```

**Python:** `validate_bmp_against_scada(feedstock_name, substrate_class, measured_bmp=None, csv_path=None) → dict`

| Argument | Description |
|----------|-------------|
| `feedstock` | Feedstock name (looked up in FEEDSTOCKS table) |
| `substrate_class` | Buswell substrate class |
| `--lab` | Optional lab BMP assay value (NL/kg VS) |
| `csv` | Optional SCADA CSV path |

**CSV columns required for SCADA back-calculation:** `feedstock_vs_kg_d`, `biogas_nm3_d`, `ch4_pct`.

**Response:**
```json
{
  "feedstock_name": "Maize silage",
  "substrate_class": "carbohydrate_glucose",
  "script_bmp": 340,
  "theoretical_bmp_buswell": 400.0,
  "script_vs_theoretical_eta": 0.85,
  "measured_bmp_lab": 350.0,
  "scada_back_calculated_bmp": 332.5,
  "scada_status": "FITTED"
}
```

---

### `calibration-history`

List saved calibration records from the CLI database.

```
python src/bio_cli.py calibration-history [--limit INT] [--format json|table]
```

**Python:** `cmd_calibration_history(args)` — reads `calibration_log` table.

| Flag | Default | Description |
|------|---------|-------------|
| `--limit` | 10 | Max records |

---

### `simulate` — AD4 4-State Simulation

All `simulate` subcommands require `ad4_simulator.py` and `bio_methane_operations_mcp_server_v5.py` to be available in `src/`.

---

#### `simulate run`

Run the AD4 4-state (Bernard 2001) ODE model to steady state.

```
python src/bio_cli.py simulate run --D FLOAT --S1 FLOAT
                                   [--days FLOAT]
                                   [--format json|text|csv]
                                   [--output PATH]
```

**Python:** `ad4_simulate(dilution_rate, influent_cod_g_per_l, days)` (imported from MCP server)

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--D` | Yes | — | Dilution rate in d⁻¹ |
| `--S1` | Yes | — | Influent COD in g/L |
| `--days` | No | 100 | Simulation duration (days) |
| `--format` | No | `text` | Output format |
| `--output` | No | None | CSV output file path |

**Text output fields:**
- D, HRT, S1_in
- Steady state: S1 (g/L), S2 (mmol/L), X1 (g/L), X2 (g/L)
- Methane production (mL/L/d)
- Solver status, health flag

**CSV columns:** `day`, `S1_g_L`, `S2_mmol_L`, `X1_g_L`, `X2_g_L`, `Q_CH4_mL_L_d`

---

#### `simulate critical-d`

Find the washout threshold (critical dilution rate) for a given influent concentration.

```
python src/bio_cli.py simulate critical-d [--S1 FLOAT]
                                          [--format json|text|csv]
```

**Python:** `ad4_critical_dilution_rate(influent_cod_g_per_l)` (imported from MCP server)

| Flag | Default | Description |
|------|---------|-------------|
| `--S1` | 25 | Influent COD (g/L) |

**Text output fields:**
- D_crit numerical & theoretical (d⁻¹)
- HRT_crit (days)
- Current plant HRT/D, safety margin (%)
- Recommended max D / min HRT

CSV output not supported (falls back to text).

---

#### `simulate perturb`

Run an overload/recovery perturbation test.

```
python src/bio_cli.py simulate perturb --overload FLOAT
                                       [--days FLOAT] [--recovery FLOAT]
                                       [--baseline FLOAT]
                                       [--format json|text|csv]
                                       [--output PATH]
```

**Python:** `ad4_perturbation_test(overload_cod_g_per_l, overload_days, recovery_days, baseline_cod_g_per_l)` (imported from MCP server)

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--overload` | Yes | — | COD during spike (g/L) |
| `--days` | No | 10 | Overload duration (days) |
| `--recovery` | No | 30 | Recovery period (days) |
| `--baseline` | No | 25 | Baseline COD (g/L) |
| `--format` | No | `text` | Output format |
| `--output` | No | None | CSV output file path |

**Text output fields:**
- Scenario description
- Baseline steady state (S1, S2, X2)
- Peak stress (S2 peak, X2 min)
- Post-recovery state
- Recovered/washout/souring flags + interpretation

**CSV columns:** Same as `simulate run` — time series across baseline → overload → recovery.

---

#### `simulate fit` — Disabled

```
python src/bio_cli.py simulate fit ...
```

**Not available in CLI.** Directs users to use the MCP server or the chat interface instead.

```text
simulate fit is not available in this version.
Use the MCP server or calibrate through the chat interface instead.
```

---

## Database Schema

**CLI database** (`data/biometa_cli.db`) — two tables:

### `alert_log`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `timestamp` | TEXT | ISO 8601 |
| `parameter` | TEXT | Sensor parameter name |
| `severity` | TEXT | `MEDIUM` or `HIGH` |
| `message` | TEXT | Human-readable alert |

### `calibration_log`

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `timestamp` | TEXT | ISO 8601 |
| `cal_type` | TEXT | Calibration type (e.g. `buswell`) |
| `inputs` | TEXT | JSON-serialised inputs |
| `results` | TEXT | JSON-serialised results |
| `notes` | TEXT | Free-text notes |

**StateBuffer** (`data/plant_state.sqlite`) — managed by `StateBuffer` class (CUSUM-filtered live data store). Not written directly by the CLI; `update` → `StateBuffer.insert_live_data()`.

---

## Site Configuration

`site_config.json` (auto-detected at `PROJECT_ROOT/site_config.json` or `src/site_config.json`):

```json
{
  "lab_scale": false,
  "alert_thresholds": {
    "lab_scale": {
      "digester_temp_c": { "low": 20, "high": 30 }
    },
    "industrial": {
      "vfa_mmol_l": { "medium": 10, "high": 20 }
    }
  }
}
```

Keys present in `alert_thresholds.<profile>` override the corresponding `DEFAULT_ALERT_THRESHOLDS` entries (Option A behaviour). Missing keys fall through to defaults.
