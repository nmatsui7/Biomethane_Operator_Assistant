# Biomethane Operations MCP Server v5 — API Reference

**Version:** 5.0  
**Module:** `src/bio_methane_operations_mcp_server_v5.py`  
**Transport:** stdio (default) or HTTP (via `--http` + uvicorn)  

By default the MCP server is launched as a **stdio subprocess** — `biomethane_chat.py` spawns it and communicates over stdin/stdout JSON-RPC. No network ports are opened for MCP unless `--http` is passed. This is the recommended mode: a single process tree, no external services to manage.

**Dependencies:** `mcp`, `numpy`, `pandas`, `uvicorn` (HTTP only), `python-dotenv`

---

## Overview

This server exposes 23 MCP tools organised into five logical layers:

| Layer | Area | Tools |
|-------|------|-------|
| **A** | Rule-based alerts & thresholds | `get_plant_state`, `update_plant_state`, `check_alerts`, `get_vfa_alkalinity_ratio`, `get_alert_history` |
| **B** | Feedstock blending & stoichiometry | `blend_feedstocks`, `list_feedstocks`, `get_operational_reference`, `get_kpi_summary` |
| **C** | Physics-based calibration | `buswell_bmp`, `buswell_bmp_by_class`, `calculate_energy_conversion_factor`, `cn_ratio_from_composition`, `olr_from_recipe`, `biodegradability_coefficient` |
| **D** | AD4 physical simulation | `ad4_simulate`, `ad4_critical_dilution_rate`, `ad4_perturbation_test` |
| **E** | EnKF state estimation | `enkf_initialise`, `enkf_update`, `enkf_status` |
| **F** | SCADA/data ingest | `ingest_scada_file`, `get_state_buffer_status` |

### Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    MCP Client (LLM / Host)                       │
└──────────────────────┬───────────────────────────────────────────┘
                       │  MCP protocol (stdio or HTTP SSE)
┌──────────────────────▼───────────────────────────────────────────┐
│                   FastMCP("biomethane-ops")                       │
│                                                                  │
│  ┌─────────────┐  ┌───────────────┐  ┌──────────────────────┐   │
│  │ StateBuffer  │  │ biomethane.db │  │  AD4Simulator        │   │
│  │ plant_state  │  │ (legacy KV)   │  │  (Bernard 2001 ODE)  │   │
│  │ .sqlite      │  │               │  │                      │   │
│  │ CUSUM-filter │  │ fallback only │  │  ┌─────────────────┐ │   │
│  │ PRIMARY      │  │               │  │  │ AD4EnKFServer   │ │   │
│  └─────────────┘  └───────────────┘  │  │ (Ensemble Kalman │ │   │
│                                      │  │  Filter)         │ │   │
│                                      │  └─────────────────┘ │   │
│                                      └──────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

**Storage priority:** StateBuffer (CUSUM-filtered SQLite) → biomethane.db (legacy) → in-memory defaults.

**Key design points:**

- All sensor writes pass through StateBuffer's CUSUM anomaly detector before persistence.
- `update_plant_state()` mirrors accepted values to both StateBuffer and legacy DB.
- AD4 and EnKF are optional — if their modules are absent, tools return `{"error": "not available"}`.
- EnKF auto-initialises at startup from `site_config.json` → `estimated_geometry`.
- A background daemon thread runs a daily assessment loop (EnKF update + conditional perturbation test) at a configurable UTC hour.

---

## CLI Usage

```bash
python src/bio_methane_operations_mcp_server_v5.py [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--http` | off (stdio) | Run HTTP transport via SSE |
| `--host` | `127.0.0.1` | HTTP bind address |
| `--port` | `3000` | HTTP port |
| `--site PATH` | `project_root/site_config.json` | Path to site configuration JSON |
| `--lab-scale` | off | Use lab-scale temperature thresholds (20–30 °C) |

**Examples:**

```bash
# stdio (default — for MCP host integration)
python src/bio_methane_operations_mcp_server_v5.py

# HTTP transport
python src/bio_methane_operations_mcp_server_v5.py --http --port 3000

# With site config + lab-scale thresholds
python src/bio_methane_operations_mcp_server_v5.py --site my_site.json --lab-scale

# HTTP with custom host/port
python src/bio_methane_operations_mcp_server_v5.py --http --host 0.0.0.0 --port 8080
```

### FastMCP Initialisation

```python
mcp = FastMCP("biomethane-ops")
```

All tools are registered via the `@mcp.tool()` decorator on module-level functions.

---

## Constants & Configuration

### `DEFAULT_ALERT_THRESHOLDS`

Two profiles: `lab_scale` and `industrial`. Active profile determined by the `_is_lab_scale` flag (set via `--lab-scale` CLI flag or `site_config.json` `"lab_scale": true`).

| Parameter | lab_scale | industrial |
|-----------|-----------|------------|
| `digester_temp_c` | 20–30 °C | 35–40 °C |
| `vfa_mmol_l` | medium=30, high=80 | medium=8, high=15 |
| `nh4_mg_l` | medium=300, high=800 | medium=300, high=800 |
| `h2s_ppm` | medium=200, high=500 | medium=200, high=500 |
| `digester_ph` | 6.8–7.8 | 6.8–7.8 |
| `biomethane_purity_pct` | min=95 | min=95 |
| `o2_ppm` | max=500 | max=500 |

**Note:** NH₄, H₂S, pH, purity, and O₂ thresholds are identical across profiles. The defining differences are temperature range and VFA thresholds (lab uses Benyahia model values; industrial uses conservative values).

### `PLANT_STATE_DEFAULTS`

15 key-value pairs used to seed the legacy DB on first run and as in-memory fallback:

| Key | Default | Description |
|-----|---------|-------------|
| `digester_temp_c` | 37.2 | Digester temperature (°C) |
| `digester_ph` | 7.1 | pH of digester contents |
| `vfa_mmol_l` | 8.5 | Volatile fatty acids (mmol/L) |
| `alkalinity_mg_caco3_l` | 2800.0 | Alkalinity as CaCO₃ (mg/L) |
| `nh4_mg_l` | 420.0 | Ammonium-N (mg/L) |
| `biogas_flow_nm3h` | 142.0 | Raw biogas flow (Nm³/h) |
| `ch4_pct` | 62.3 | Methane content of raw biogas (%) |
| `co2_pct` | 36.1 | Carbon dioxide content (%) |
| `h2s_ppm` | 380.0 | Hydrogen sulphide (ppm) |
| `o2_ppm` | 180.0 | Oxygen (ppm) |
| `biomethane_purity_pct` | 97.4 | Upgraded biomethane CH₄ purity (%) |
| `grid_injection_nm3h` | 98.0 | Biomethane sent to grid (Nm³/h) |
| `organic_load_kg_vs_d` | 2.8 | Organic loading rate (kg VS/d) |
| `hydraulic_retention_days` | 22.0 | Hydraulic retention time (days) |
| `digestate_ts_pct` | 3.2 | Digestate total solids (%) |

### `_FEEDSTOCKS`

Eight predefined feedstocks used by `blend_feedstocks()`, `olr_from_recipe()`, and related tools:

| Feedstock | BMP (NL CH₄/kg VS) | C/N | DM (%) | VS/DM |
|-----------|-------------------|-----|--------|-------|
| Cattle slurry | 200 | 11 | 8 | 0.80 |
| Pig manure | 310 | 8 | 6 | 0.78 |
| Maize silage | 340 | 25 | 33 | 0.94 |
| Food waste | 480 | 16 | 25 | 0.88 |
| Grass silage | 290 | 18 | 30 | 0.90 |
| Sewage sludge | 220 | 9 | 4 | 0.75 |
| Chicken manure | 350 | 7 | 25 | 0.76 |
| Fat/grease trap | 900 | 30 | 80 | 0.97 |

### `_SUBSTRATE_FORMULAS`

Molecular formulas for Buswell BMP calculations:

| Key | Formula | C | H | O | N | S | MW (g/mol) |
|-----|---------|---|---|---|---|---|------------|
| `carbohydrate_cellulose` | C₆H₁₀O₅ | 6 | 10 | 5 | 0 | 0 | 162.14 |
| `carbohydrate_glucose` | C₆H₁₂O₆ | 6 | 12 | 6 | 0 | 0 | 180.16 |
| `protein_generic` | C₅H₇O₂N | 5 | 7 | 2 | 1 | 0 | 113.12 |
| `lipid_tripalmitin` | C₅₁H₉₈O₆ | 51 | 98 | 6 | 0 | 0 | 807.32 |
| `lipid_triolein` | C₅₇H₁₀₄O₆ | 57 | 104 | 6 | 0 | 0 | 885.43 |

### `LHV_KWH_PER_NM3`

Built-in constant: **10.55** kWh/Nm³ at 97.4% CH₄ purity. Derivation approach available via `calculate_energy_conversion_factor()`.

### `DEFAULT_DAILY_ASSESSMENT`

```python
{
    "souring_probability_watch":  0.10,   # Bernard (2001) early-warning
    "souring_probability_action": 0.30,   # Benyahia (2012) S2 accumulation onset
    "s2_rising_days":             2,      # consecutive S2 increases before flag
    "run_hour_utc":               6,      # 06:00 UTC daily assessment window
}
```

---

## Temperature Correction (AD4 Tools)

All three AD4 simulation tools (`ad4_simulate`, `ad4_critical_dilution_rate`, `ad4_perturbation_test`) apply Arrhenius temperature correction to the methanogen maximum growth rate μ₂ₘₐₓ:

```
μ₂ₘₐₓ(T) = μ₂ₘₐₓ(35°C) × θ^(T - 35)

where θ = 1.035 (Arrhenius constant)
```

If `digester_temp_c` is not explicitly passed, the tools read the current value from `plant_state`. The response always includes:
- `mu2_max_at_35c` — the reference value
- `mu2_max_effective` — the temperature-corrected value
- `temperature_correction_applied` — boolean flag
- A textual `temperature_note` explaining the correction

**Practical impact:** A cooler digester (e.g. 30 °C) has a lower D_crit, shrinking the safe operating envelope. Winter operation reduces washout headroom.

---

## Plant State Monitoring (Tools 1–5)

### Tool 1: `get_plant_state`

```
get_plant_state() -> dict
```

Returns the current plant operating state. Read priority: StateBuffer → legacy DB → in-memory defaults.

**Response example:**

```json
{
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
  "digestate_ts_pct": 3.2
}
```

---

### Tool 2: `update_plant_state`

```
update_plant_state(updates: dict) -> dict
```

Update plant state values. Writes through StateBuffer (CUSUM-filtered) when available, then mirrors to legacy DB.

| Parameter | Type | Description |
|-----------|------|-------------|
| `updates` | `dict` | Key-value pairs to update (must be numeric; keys must exist in `PLANT_STATE_DEFAULTS` or StateBuffer schema) |

**Response:**

```json
{
  "updated": ["digester_temp_c", "vfa_mmol_l"],
  "rejected": [],
  "accepted_count": 2,
  "rejected_count": 0,
  "state": { ... },
  "via_state_buffer": true
}
```

---

### Tool 3: `check_alerts`

```
check_alerts() -> list
```

Check all parameters against active thresholds and return alert records. Thresholds selected by `_is_lab_scale` flag, optionally overridden by `site_config.json` → `alert_thresholds`.

**Checks performed:** temperature, pH, VFA, NH₄, H₂S, biomethane purity, O₂.

**Response:**

```json
[
  {
    "parameter": "vfa_mmol_l",
    "severity": "MEDIUM",
    "message": "VFA 12.5 mmol/L - acidification risk (threshold: 8)"
  },
  {
    "parameter": "digester_temp_c",
    "severity": "HIGH",
    "message": "Temperature 41.2C outside 35-40°C (industrial) range"
  }
]
```

Severity levels: `HIGH` (immediate action), `MEDIUM` (monitor). VFA and NH₄ have two-tier thresholds (medium/high).

---

### Tool 4: `get_vfa_alkalinity_ratio`

```
get_vfa_alkalinity_ratio() -> dict
```

Calculate the FOS/TAC ratio (VFA / alkalinity × 50) and assess acidification risk.

| Ratio Range | Status | Advice |
|-------------|--------|--------|
| < 0.1 | LOW | Stable but may be underloaded |
| 0.1–0.3 | OPTIMAL | Process is in healthy range |
| 0.3–0.5 | HIGH | Monitor closely for acidification risk |
| > 0.5 | CRITICAL | Take immediate action |

**Response:**

```json
{
  "fos_tac_ratio": 0.152,
  "vfa_mmol_l": 8.5,
  "alkalinity_mg_caco3_l": 2800.0,
  "status": "OPTIMAL",
  "advice": "Process is in healthy range"
}
```

**Derivation:** `FOS/TAC = VFA(mmol/L) × 50 / alkalinity(mg CaCO₃/L)`. The factor 50 converts alkalinity from mg/L CaCO₃ to meq/L (1 meq ≡ 50 mg CaCO₃), giving a dimensionless ratio.

---

### Tool 5: `get_alert_history`

```
get_alert_history(limit: int = 10) -> dict
```

Return recent alert records from the in-memory deque (max 1000 entries).

| Parameter | Default | Description |
|-----------|---------|-------------|
| `limit` | `10` | Maximum alerts to return (≤0 returns all) |

**Response:**

```json
{
  "total_alerts_in_log": 47,
  "returned": 10,
  "records": [
    {"parameter": "vfa_mmol_l", "severity": "MEDIUM", "message": "..."}
  ]
}
```

---

## Feedstock Chemistry (Tools 6–15)

### Tool 6: `blend_feedstocks`

```
blend_feedstocks(
    recipe: list[dict],
    target_olr: float = 2.5
) -> dict
```

Calculate a multi-feedstock blend and its expected output.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `recipe` | `list[dict]` | — | List of `{"name": "...", "wet_tonnes": N}` |
| `target_olr` | `float` | `2.5` | Target OLR (kg VS/m³/d) — advisory only |

**Recipe item format:**

```json
{"name": "Cattle slurry", "wet_tonnes": 12.0}
```

**Response:**

```json
{
  "streams": [
    {"name": "Cattle slurry", "wet_tonnes": 12.0, "vs_kg": 768.0, "bmp": 200},
    {"name": "Maize silage", "wet_tonnes": 5.0, "vs_kg": 1551.0, "bmp": 340}
  ],
  "total_vs_kg": 2319.0,
  "estimated_ch4_yield_nl_kg_vs": 285.3,
  "blend_cn_ratio": 20.4,
  "ch4_fraction_used": 0.623,
  "inhibition_warnings": [],
  "estimated_daily_output": {
    "biomethane_nm3": 661.5,
    "biomethane_mwh": 6.979
  },
  "status": "OK"
}
```

Status is `"OK"` if 20 ≤ C/N ≤ 30, otherwise `"ADJUST_CN"`. Inhibition warnings fire for food waste (H₂S risk), poultry/pig manure (NH₃ risk), and C/N out of range.

---

### Tool 7: `get_operational_reference`

```
get_operational_reference(topic: str) -> dict
```

Look up operational guidance text.

| Parameter | Type | Description |
|-----------|------|-------------|
| `topic` | `str` | Topic string (matched against keys and aliases) |

**Available topics:** `fos_tac`, `temperature`, `olr`, `cn_ratio`. Aliases include `fos tac`, `acidification`, `temp`, `loading rate`, `carbon nitrogen`, etc.

**Response:**

```json
{
  "topic": "fos_tac",
  "guidance": "FOS/TAC ratio measures buffering capacity vs volatile fatty acids. Target 0.3-0.4 for stable mesophilic digestion. >0.6 indicates risk of acidification."
}
```

---

### Tool 8: `get_kpi_summary`

```
get_kpi_summary(period: str = "daily") -> dict
```

Calculate production KPIs for the given period.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `period` | `str` | `"daily"` | One of `"daily"`, `"weekly"`, `"monthly"` |

**Response:**

```json
{
  "period": "daily",
  "date": "2026-05-31T12:00:00",
  "grid_injection_nm3": 2352.0,
  "energy_kwh": 24141.6,
  "avg_purity_pct": 97.4,
  "uptime_pct": 98.5
}
```

Uses `grid_injection_nm3h` from plant state, multiplies by hours and a random uptime factor (92–99 %).

---

### Tool 9: `list_feedstocks`

```
list_feedstocks() -> dict
```

Return the full feedstock table.

**Response:**

```json
{
  "feedstocks": {
    "Cattle slurry": {"bmp": 200, "cn": 11, "dm": 8, "vs_dm": 0.8},
    ...
  }
}
```

---

### Tool 10: `buswell_bmp`

```
buswell_bmp(
    c: float,
    h: float,
    o: float,
    n: float = 0.0,
    s: float = 0.0
) -> dict
```

Calculate theoretical BMP from elemental composition using the Buswell equation (Buswell & Mueller 1952).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `c` | `float` | — | Carbon atoms in molecular formula |
| `h` | `float` | — | Hydrogen atoms |
| `o` | `float` | — | Oxygen atoms |
| `n` | `float` | `0.0` | Nitrogen atoms |
| `s` | `float` | `0.0` | Sulfur atoms |

**Equations:**

```
CH₄  = C/2 + H/8 - O/4 - 3N/8 - S/4
CO₂  = C/2 - H/8 + O/4 + 3N/8 + S/4
H₂O  = C   - H/4 - O/2 + 3N/4 + S/2
```

**Response (glucose C₆H₁₂O₆ example):**

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

Molar volume at NTP: 24.04 L/mol.

---

### Tool 11: `buswell_bmp_by_class`

```
buswell_bmp_by_class(substrate_class: str) -> dict
```

Calculate BMP for a named substrate class (see `_SUBSTRATE_FORMULAS` table above).

| Parameter | Type | Description |
|-----------|------|-------------|
| `substrate_class` | `str` | One of: `carbohydrate_cellulose`, `carbohydrate_glucose`, `protein_generic`, `lipid_tripalmitin`, `lipid_triolein` |

**Response (lipid_tripalmitin example):**

```json
{
  "substrate_formula": "C51H98O6N0S0",
  "ch4_mol_per_mol_substrate": 36.75,
  "ch4_nl_per_mol_substrate": 883.47,
  "ch4_fraction_pct": 70.2,
  "co2_fraction_pct": 29.8,
  "substrate_class": "lipid_tripalmitin",
  "molecular_weight": 807.32,
  "bmp_nl_per_g_vs": 1.1,
  "bmp_nl_per_kg_vs": 1094.0
}
```

Returns `{"error": "Unknown class '...'", "available": [...]}` on invalid input.

---

### Tool 12: `calculate_energy_conversion_factor`

```
calculate_energy_conversion_factor(ch4_fraction: float = 0.974) -> dict
```

Derive the kWh/Nm³ conversion factor from CH₄ fraction. Full derivation chain from STP through NTP correction.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `ch4_fraction` | `float` | `0.974` | CH₄ mole fraction (default = 97.4 %) |

**Constants:**

- LHV CH₄ (STP, 15 °C, 1 atm) = 35.88 MJ/Nm³ (Perry & Green 2008, Table 2-150)
- STP→NTP correction: 273.15 / 288.15
- MJ→kWh: 1/3.6

**Response:**

```json
{
  "ch4_fraction_input": 0.974,
  "LHV_pure_CH4_MJ_per_Nm3_STP": 35.88,
  "STP_to_NTP_correction": 0.948,
  "LHV_pure_CH4_MJ_per_Nm3_NTP": 37.848,
  "LHV_biomethane_MJ_per_Nm3": 36.864,
  "LHV_biomethane_kWh_per_Nm3": 10.24,
  "script_current_value_kWh_per_Nm3": 10.55,
  "difference_pct": -2.94,
  "recommendation": "For 97.4% CH4, use 10.24 kWh/Nm3."
}
```

---

### Tool 13: `cn_ratio_from_composition`

```
cn_ratio_from_composition(
    carbon_pct_of_vs: float,
    nitrogen_pct_of_vs: float
) -> dict
```

Calculate C/N ratio from elemental analysis (Drosg 2013, IEA Bioenergy Task 37).

| Parameter | Type | Description |
|-----------|------|-------------|
| `carbon_pct_of_vs` | `float` | % carbon by mass of volatile solids (e.g. 44.0) |
| `nitrogen_pct_of_vs` | `float` | % nitrogen by mass of VS (e.g. 1.8) |

**Response:**

```json
{
  "carbon_pct_vs": 44.0,
  "nitrogen_pct_vs": 1.8,
  "cn_ratio": 24.4,
  "status": "OPTIMAL",
  "advice": "C/N ratio in ideal range (20-30). Good stability expected."
}
```

| C/N Range | Status | Advice |
|-----------|--------|--------|
| 20–30 | OPTIMAL | Ideal range |
| < 20 | LOW — ammonia inhibition risk | Add carbon-rich co-substrate |
| 30–35 | HIGH — slow degradation | Add nitrogen-rich co-substrate |
| > 35 | VERY HIGH | Pre-treatment required |

---

### Tool 14: `olr_from_recipe`

```
olr_from_recipe(
    recipe: list[dict],
    digester_volume_m3: float
) -> dict
```

Calculate OLR from a feedstock recipe (Weiland 2010 reference, optimal 2–4 kg VS/m³/d).

| Parameter | Type | Description |
|-----------|------|-------------|
| `recipe` | `list[dict]` | List of `{"name": "...", "wet_tonnes": N}` |
| `digester_volume_m3` | `float` | Working volume of digester (m³) |

**Response:**

```json
{
  "recipe_streams": [
    {"name": "Cattle slurry", "vs_kg_per_day": 768.0}
  ],
  "total_vs_kg_per_day": 2319.0,
  "digester_volume_m3": 2000.0,
  "olr_kg_vs_m3_day": 1.159,
  "status": "UNDERLOADED",
  "advice": "OLR low. Increase feedstock throughput."
}
```

| OLR (kg VS/m³/d) | Status |
|-------------------|--------|
| < 1.5 | UNDERLOADED |
| 1.5–3.5 | OPTIMAL |
| 3.5–4.5 | HIGH |
| > 4.5 | OVERLOADED |

---

### Tool 15: `biodegradability_coefficient`

```
biodegradability_coefficient(
    substrate_class: str,
    empirical_bmp_nl_per_kg_vs: float
) -> dict
```

Calculate biodegradability η = empirical / theoretical (Angelidaki et al. 2009).

| Parameter | Type | Description |
|-----------|------|-------------|
| `substrate_class` | `str` | One of the five substrate classes |
| `empirical_bmp_nl_per_kg_vs` | `float` | Measured BMP from lab or literature |

**Response:**

```json
{
  "substrate_class": "protein_generic",
  "theoretical_bmp_nl_per_kg_vs": 786.0,
  "empirical_bmp_nl_per_kg_vs": 550.0,
  "biodegradability_coefficient_eta": 0.7,
  "flag": "MODERATE — partially recalcitrant"
}
```

| η Range | Flag |
|---------|------|
| > 1.0 | IMPOSSIBLE — empirical > theoretical |
| ≥ 0.80 | GOOD — highly degradable |
| ≥ 0.50 | MODERATE — partially recalcitrant |
| < 0.50 | LOW — recalcitrant substrate |

---

## Physical Simulation — AD4 (Tools 16–18)

The AD4 tools implement the 4-state AM2 anaerobic digestion model (Bernard 2001, Benyahia 2012):

| State | Symbol | Description |
|-------|--------|-------------|
| S₁ | Substrate | Readily biodegradable COD (g/L) |
| S₂ | VFA | Volatile fatty acids (mmol/L) |
| X₁ | Acidogens | Acidogenic biomass (g/L) |
| X₂ | Methanogens | Methanogenic biomass (g/L) |

All three tools require `ad4_simulator.py` on the Python path. They return `{"error": "AD4 simulator not available"}` if absent.

---

### Tool 16: `ad4_simulate`

```
ad4_simulate(
    dilution_rate: float,
    influent_cod_g_per_l: float,
    days: float = 100.0,
    digester_temp_c: Optional[float] = None
) -> dict
```

Run the CSTR digester ODE to steady state.

| Parameter | Range | Default | Description |
|-----------|-------|---------|-------------|
| `dilution_rate` | (0, 2.0] | — | D in d⁻¹ (HRT = 1/D). Typical: 0.04–0.07 |
| `influent_cod_g_per_l` | (0, 200] | — | S1_in — influent COD (g/L). Typical: 15–50 |
| `days` | [10, 1000] | 100.0 | Simulation horizon. Use ≥100 for steady state |
| `digester_temp_c` | — | from plant_state | Digester temp for Arrhenius correction |

**Response:**

```json
{
  "steady_state": {
    "S1_g_per_L": 1.234,
    "S2_mmol_per_L": 12.5,
    "X1_g_per_L": 0.987,
    "X2_g_per_L": 1.876
  },
  "methane_mL_per_L_per_d": 456.2,
  "COD_removal_pct": 95.1,
  "washout_detected": false,
  "souring_detected": false,
  "is_healthy": true,
  "interpretation": {
    "S2_status": "HEALTHY",
    "X2_status": "ROBUST",
    "methane_status": "GOOD",
    "temperature_correction_applied": false,
    "digester_temp_c": 35.0,
    "mu2_max_effective": 1.2,
    "mu2_max_at_35c": 1.2,
    "kinetic_params": "Benyahia defaults with Arrhenius T-correction (T=35.0°C, mu2_max=1.2000 d⁻¹)"
  }
}
```

**S₂ status thresholds:** HEALTHY < 30, WATCH < 80, WARNING < 150, CRITICAL ≥ 150.  
**X₂ status thresholds:** ROBUST > 2.0, NORMAL > 1.0, LOW > 0.1, NEAR_WASHOUT > 0.05, WASHOUT ≤ 0.05.  
**Methane status:** GOOD > 300, MODERATE > 150, LOW > 50, VERY_LOW ≤ 50 mL/L/d.

---

### Tool 17: `ad4_critical_dilution_rate`

```
ad4_critical_dilution_rate(
    influent_cod_g_per_l: float = 25.0,
    digester_temp_c: Optional[float] = None
) -> dict
```

Find the washout threshold (D_crit) — the maximum dilution rate before methanogen washout. Uses both analytical (Haldane peak) and numerical (ODE bisection) approaches.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `influent_cod_g_per_l` | `float` | `25.0` | Influent COD concentration |
| `digester_temp_c` | `float` or None | from plant_state | Temperature for Arrhenius correction |

**Response:**

```json
{
  "influent_cod_g_per_l": 25.0,
  "digester_temp_c": 35.0,
  "mu2_max_at_35c": 1.2,
  "mu2_max_effective": 1.2,
  "D_crit_at_35c_per_d": 0.185,
  "D_crit_at_temp_analytical": 0.185,
  "D_crit_numerical_per_d": 0.182,
  "HRT_crit_days": 5.49,
  "current_plant_HRT_days": 22.0,
  "current_plant_D_per_d": 0.04545,
  "safety_margin_pct": 75.0,
  "D_crit_reduction_vs_35c_pct": 0.0,
  "status": "SAFE",
  "recommended_max_D_per_d": 0.0728,
  "recommended_min_HRT_days": 13.7,
  "temperature_note": "..."
}
```

**Safety margin status:**
- `SAFE` — margin > 40 %
- `CAUTION` — margin > 20 %
- `WARNING — operating close to washout threshold` — margin ≤ 20 %

The tool reports both the analytical D_crit (fast, from Haldane peak formula) and the numerical D_crit (more accurate, from ODE bisection).

---

### Tool 18: `ad4_perturbation_test`

```
ad4_perturbation_test(
    overload_cod_g_per_l: float,
    overload_days: float = 10.0,
    recovery_days: float = 30.0,
    baseline_cod_g_per_l: float = 0.0,
    digester_temp_c: Optional[float] = None
) -> dict
```

Simulate an organic overload spike and test digester recovery. Three-phase simulation: baseline steady state → overload → recovery at normal feeding.

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `overload_cod_g_per_l` | `float` | (0, 200] | — | Elevated influent COD during spike |
| `overload_days` | `float` | [1, 60] | 10.0 | Duration of overload event |
| `recovery_days` | `float` | [5, 200] | 30.0 | Post-overload recovery simulation |
| `baseline_cod_g_per_l` | `float` | — | 0.0 (uses 25.0) | Normal influent COD |
| `digester_temp_c` | `float` or None | — | from plant_state | Temperature for Arrhenius correction |

**Response:**

```json
{
  "scenario": "overload_spike",
  "plant_HRT_days": 22.0,
  "dilution_rate_per_d": 0.04545,
  "digester_temp_c": 35.0,
  "mu2_max_effective": 1.2,
  "mu2_max_at_35c": 1.2,
  "baseline_cod_g_per_l": 25.0,
  "overload_cod_g_per_l": 45.0,
  "overload_duration_days": 10.0,
  "recovery_duration_days": 30.0,
  "baseline_steady_state": {
    "S1_g_per_L": 1.234,
    "S2_mmol_per_L": 12.5,
    "X1_g_per_L": 0.987,
    "X2_g_per_L": 1.876
  },
  "peak_stress_during_overload": {
    "S2_peak_mmol_per_L": 85.3,
    "X2_min_g_per_L": 1.234,
    "souring_during_overload": false
  },
  "post_recovery_state": {
    "S1_g_per_L": 1.240,
    "S2_mmol_per_L": 13.1,
    "X1_g_per_L": 0.991,
    "X2_g_per_L": 1.870
  },
  "washout_detected": false,
  "souring_detected": false,
  "recovered_healthy": true,
  "interpretation": "RECOVERED — digester returned to healthy state after overload.",
  "temperature_note": "..."
}
```

---

## EnKF State Estimation (Tools 19–21)

The Ensemble Kalman Filter estimates hidden states (S₂ = VFA, X₂ = methanogens) from observable Q_CH₄ and temperature, using the AD4 ODE as the process model. Requires `ad4_enkf.py` on the Python path.

---

### Tool 19: `enkf_initialise`

```
enkf_initialise(
    digester_volume_m3: float = 2000.0,
    hrt_days: float = 22.0,
    s1_in_g_per_l: float = 25.0,
    n_ensemble: int = 100,
    t_ref_celsius: float = 35.0
) -> dict
```

Initialise (or re-initialise) the Ensemble Kalman Filter.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `digester_volume_m3` | `float` | `2000.0` | Total digester volume (m³) |
| `hrt_days` | `float` | `22.0` | Current hydraulic retention time (days) |
| `s1_in_g_per_l` | `float` | `25.0` | Estimated influent COD (g/L) |
| `n_ensemble` | `int` | `100` | Ensemble size (200 is more accurate but ~4× slower) |
| `t_ref_celsius` | `float` | `35.0` | Reference temperature for AD4 kinetics |

**Response:**

```json
{
  "status": "initialised",
  "digester_volume_m3": 2000.0,
  "hrt_days": 22.0,
  "n_ensemble": 100,
  "t_ref_celsius": 35.0
}
```

Auto-initialised at server startup from `site_config.json` → `estimated_geometry`.

---

### Tool 20: `enkf_update`

```
enkf_update(
    fos_mg_per_l: Optional[float] = None,
    new_hrt_days: Optional[float] = None,
    new_s1_in_g_per_l: Optional[float] = None
) -> dict
```

Advance the EnKF by one day. Reads `biogas_flow_nm3h`, `ch4_pct`, and `digester_temp_c` automatically from plant_state.

| Parameter | Type | Description |
|-----------|------|-------------|
| `fos_mg_per_l` | `float` or None | FOS (volatile acids) in mg/L — optional. Converts to S₂ proxy via S₂ ≈ FOS/60. Dramatically tightens estimate if provided |
| `new_hrt_days` | `float` or None | Updated HRT if changed today |
| `new_s1_in_g_per_l` | `float` or None | Updated influent COD if changed |

**Response:**

```json
{
  "S2_mean": 15.3,
  "S2_std": 4.2,
  "X2_mean": 1.87,
  "X2_std": 0.31,
  "souring_probability": 0.05,
  "washout_probability": 0.01,
  "risk_level": "LOW",
  "guidance": "...",
  "plant_state_used": {
    "biogas_flow_nm3h": 142.0,
    "ch4_pct": 62.3,
    "digester_temp_c": 37.2,
    "fos_provided": false
  }
}
```

Returns `{"error": "EnKF not initialised. Call enkf_initialise() first."}` if the filter has not been set up.

---

### Tool 21: `enkf_status`

```
enkf_status() -> dict
```

Return current EnKF filter status, latest estimate, and 7-day S₂ trend. Does **not** advance the filter.

**Response:**

```json
{
  "initialised": true,
  "days_tracked": 45,
  "current_risk_level": "LOW",
  "s2_trend": "stable",
  "latest_estimate": {
    "day": 45,
    "S2_mean": 15.3,
    "S2_std": 4.2,
    "X2_mean": 1.87,
    "X2_std": 0.31,
    "souring_probability": 0.05,
    "washout_probability": 0.01,
    "risk_level": "LOW",
    "S2_interval_95": [7.1, 23.5]
  },
  "operator_summary": "Day 45: Estimated VFA S2=15.3 mmol/L (95% CI: 7–24), Methanogens X2=1.87 g/L, Souring risk=5%, Risk: LOW."
}
```

---

## SCADA Ingest (Tools 22–23)

### Tool 22: `ingest_scada_file`

```
ingest_scada_file(
    file_path: str,
    vendor: Optional[str] = None
) -> dict
```

Ingest a SCADA export file (CSV or Excel) into the StateBuffer. Runs the full pipeline: auto-detect vendor → fuzzy-map column names → CUSUM-filtered insert.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file_path` | `str` | Absolute or project-relative path to CSV/Excel file |
| `vendor` | `str` or None | Optional hint: `"generic"`, `"siemens"`, `"rockwell"`, `"mitsubishi"` |

**Response:**

```json
{
  "file": "/data/scada_export.csv",
  "format": "csv",
  "vendor": "siemens",
  "rows_accepted": 95,
  "rows_skipped": 3,
  "mapping": {"digester_temp_c": "TEMP_DIG", "ph": "PH_PROBE_1", ...},
  "cusum_status": {...}
}
```

Requires `scada_mapper.py`, `StateBuffer.py`, and `pandas`.

---

### Tool 23: `get_state_buffer_status`

```
get_state_buffer_status() -> dict
```

Return StateBuffer health, latest clean readings, and CUSUM accumulator status. Useful for diagnosing sensor drift or anomaly detection behaviour.

**Response (available):**

```json
{
  "available": true,
  "db_path": "/data/plant_state.sqlite",
  "rows_in_buffer": 2841,
  "latest_reading": {"digester_temp_c": 37.2, ...},
  "cusum_status": {
    "digester_temp_c": {"s_pos": 0.5, "s_neg": 0.0, "H": 2.0, ...},
    ...
  },
  "sensors_near_threshold": ["vfa_mmol_l"]
}
```

**Response (unavailable):**

```json
{
  "available": false,
  "reason": "StateBuffer not initialised (StateBuffer.py not found or site_config.json missing)",
  "fallback": "Using legacy biomethane.db"
}
```

---

## Alert Thresholds Reference

### Threshold Resolution Order

1. `_is_lab_scale` flag selects active profile (`lab_scale` or `industrial`)
2. `site_config.json` → `alert_thresholds` → profile key → per-key overrides (Option A: explicit config overrides defaults for any specified keys; unspecified keys fall through to defaults)
3. `DEFAULT_ALERT_THRESHOLDS` as base fallback

### Profile Comparison

| Parameter | lab_scale | industrial | Rationale |
|-----------|-----------|------------|-----------|
| Temperature | 20–30 °C | 35–40 °C | Lab digesters operate at ambient/psychrophilic range |
| VFA (medium/high) | 30 / 80 mmol/L | 8 / 15 mmol/L | Lab uses Benyahia model; industrial uses conservative thresholds |
| NH₄ (medium/high) | 300 / 800 mg/L | 300 / 800 mg/L | Same literature base |
| H₂S (medium/high) | 200 / 500 ppm | 200 / 500 ppm | Same literature base |
| pH (low/high) | 6.8 / 7.8 | 6.8 / 7.8 | Same literature base |
| Purity (min) | 95 % | 95 % | Grid injection spec |
| O₂ (max) | 500 ppm | 500 ppm | Air ingress detection |

### Daily Assessment Thresholds

Configured via `site_config.json` → `daily_assessment` or defaults:

| Threshold | Default | Description |
|-----------|---------|-------------|
| `souring_probability_watch` | 0.10 | EnKF souring probability at which advisory is logged |
| `souring_probability_action` | 0.30 | Triggers `ad4_perturbation_test` to quantify headroom |
| `s2_rising_days` | 2 | Consecutive daily S₂ increases before trend is flagged |
| `run_hour_utc` | 6 | UTC hour when the background assessment thread executes |

---

## Server Initialisation Flow

On startup, the `__main__` block executes in this order:

1. **Parse CLI args** (`--http`, `--host`, `--port`, `--site`, `--lab-scale`)
2. **Set `_is_lab_scale`** from `site_config.lab_scale`, CLI flag overrides
3. **Reload config** if `--site` was provided
4. **Auto-initialise EnKF** from `site_config.estimated_geometry` (if `ad4_enkf.py` available)
5. **Start daily assessment thread** (if both EnKF and AD4 available):
   - Daemon thread checks every 30 minutes
   - Runs once per calendar day at `run_hour_utc` UTC
   - Calls `enkf_update()` → checks souring probability
   - If ≥ action threshold (0.30): calls `ad4_perturbation_test()` with current S1_in
   - If ≥ watch threshold (0.10): logs advisory
   - Otherwise: logs "HEALTHY"
6. **Start MCP transport** — stdio or HTTP (uvicorn)

```python
mcp = FastMCP("biomethane-ops")
# ... all 23 @mcp.tool() decorated functions ...

if __name__ == "__main__":
    # CLI parsing, config loading, EnKF init, assessment thread, mcp.run()
```

---

## Usage Examples

### Basic state monitoring

```python
# Get current plant state
state = get_plant_state()
print(state["digester_temp_c"])  # 37.2

# Update a parameter  
update_plant_state({"digester_temp_c": 38.5})

# Check for alerts
alerts = check_alerts()
for a in alerts:
    print(f"{a['severity']}: {a['message']}")
```

### Feedstock blending

```python
recipe = [
    {"name": "Cattle slurry", "wet_tonnes": 15.0},
    {"name": "Maize silage",  "wet_tonnes": 8.0},
    {"name": "Food waste",    "wet_tonnes": 3.0},
]
result = blend_feedstocks(recipe, target_olr=2.5)
print(f"C/N: {result['blend_cn_ratio']}, CH₄: {result['estimated_ch4_yield_nl_kg_vs']} NL/kg VS")
```

### Buswell BMP calculation

```python
# Glucose: C₆H₁₂O₆
bmp = buswell_bmp(c=6, h=12, o=6)
print(f"CH₄ = {bmp['ch4_mol_per_mol_substrate']} mol/mol")  # 3.0

# Predefined class
result = buswell_bmp_by_class("lipid_tripalmitin")
print(f"BMP = {result['bmp_nl_per_kg_vs']} NL/kg VS")  # ~1094
```

### AD4 simulation

```python
# Simulate steady state at D=0.05 d⁻¹, S1_in=25 g/L
ss = ad4_simulate(dilution_rate=0.05, influent_cod_g_per_l=25.0, days=100)
print(f"VFA: {ss['steady_state']['S2_mmol_per_L']} mmol/L")

# Critical dilution rate
dcrit = ad4_critical_dilution_rate(influent_cod_g_per_l=25.0)
print(f"D_crit = {dcrit['D_crit_numerical_per_d']} d⁻¹, margin = {dcrit['safety_margin_pct']}%")

# Perturbation test
pert = ad4_perturbation_test(overload_cod_g_per_l=45.0)
print(pert['interpretation'])
```

### EnKF state estimation

```python
# Initialise
enkf_initialise(digester_volume_m3=2500, hrt_days=20, n_ensemble=100)

# Daily update (reads biogas_flow, ch4_pct, temp from plant_state)
result = enkf_update()
print(f"S₂ = {result['S2_mean']} ± {result['S2_std']} mmol/L")
print(f"Souring probability: {result['souring_probability']:.1%}")

# Check status without advancing
status = enkf_status()
print(status['operator_summary'])
```

### SCADA file ingest

```python
result = ingest_scada_file("data/scada_export.csv", vendor="siemens")
print(f"Accepted {result['rows_accepted']} rows, skipped {result['rows_skipped']}")
print(f"Mapping: {result['mapping']}")
```
