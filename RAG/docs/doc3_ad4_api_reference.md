# AD4 Simulator — MCP Tool API Reference Sheet
## Plain-text reference for LLM context: what every parameter and output means

---

## SECTION 1: Classes Overview

The ad4_simulator.py module contains four classes:

1. **AD4Params** — holds all kinetic and stoichiometric parameters
2. **AD4State** — represents a snapshot of the four state variables at one point in time
3. **AD4Result** — contains the full time-series output of a simulation run
4. **AD4Simulator** — the main simulator object; call `.run()` to simulate

---

## SECTION 2: AD4Params — All Parameters Explained

AD4Params stores the kinetic constants and yield coefficients. Default values are for a farm-scale manure digester at 35°C (mesophilic).

### Acidogen parameters:
- **mu1_max = 1.20 d⁻¹** — Maximum possible growth rate of acidogens. Upper bound on how fast X1 can grow regardless of substrate availability.
- **Ks1 = 7.10 g COD/L** — Half-saturation constant. When S1 = 7.10 g/L, acidogens grow at exactly half their maximum rate. Lower Ks1 = better substrate affinity.
- **k1 = 42.14 (g COD / g COD)** — Substrate consumption yield. For every unit of acidogen growth, 42.14 units of COD (S1) are consumed. High because most substrate energy goes to VFA, not cell growth.
- **k2 = 116.50 (mmol VFA / g COD)** — VFA production yield. For every unit of acidogen growth, 116.50 mmol of VFA (S2) are produced.

### Methanogen parameters:
- **mu2_max = 0.74 d⁻¹** — Maximum possible growth rate of methanogens. Note: actual achievable maximum is ~0.37 d⁻¹ due to Haldane inhibition.
- **Ks2 = 9.28 mmol/L** — Half-saturation constant for VFA. When S2 = 9.28 mmol/L, methanogens grow at half their (uninhibited) maximum rate.
- **Ki2 = 256.0 mmol/L** — Haldane inhibition constant. Higher Ki2 = methanogens more tolerant of high VFA. Lower Ki2 = more sensitive population, souring threshold is lower.
- **k3 = 268.0 (mmol VFA / g VSS)** — VFA consumption yield. For every unit of methanogen growth, 268 mmol of VFA (S2) are consumed.

### Shared parameters:
- **alpha = 1.0** — Biomass washout factor. alpha=1.0 means cells wash out at the same rate as liquid (plain CSTR). alpha=0.5 means cells are retained twice as long as liquid (e.g., with a settler). Setting alpha < 1.0 raises the effective D_crit.
- **k6 = 453.0 (mL CH4 / mmol S2)** — Methane production coefficient. Converts VFA consumption rate by methanogens into volumetric methane flow.

### How to customize parameters:
```python
# More sensitive methanogens (lower Ki2):
p = AD4Params(Ki2=150.0)

# Faster acidogens:
p = AD4Params(mu1_max=1.5, Ks1=5.0)

# Biomass retention (settler):
p = AD4Params(alpha=0.6)

sim = AD4Simulator(params=p)
```

---

## SECTION 3: AD4State — State Variable Snapshot

AD4State holds one time-point of the four state variables.

- **S1** — Organic substrate concentration (g COD/L). Default initial: 3.0 g/L.
- **S2** — Volatile fatty acid concentration (mmol/L). Default initial: 0.5 mmol/L. A healthy digester has S2 < 30 mmol/L.
- **X1** — Acidogenic bacteria concentration (g VSS/L). Default initial: 0.5 g/L.
- **X2** — Methanogenic archaea concentration (g VSS/L). Default initial: 1.5 g/L.

**Health check rule:** `AD4State.is_healthy()` returns True when S2 < 150 mmol/L AND X1 > 0.01 g/L AND X2 > 0.01 g/L.

**Usage:** Pass a custom initial state to avoid spin-up time when running sequential simulations:
```python
state = AD4State(S1=5.0, S2=20.0, X1=0.8, X2=2.0)
result = sim.run(days=60, D=0.05, S1_in=30.0, initial=state)
```

---

## SECTION 4: AD4Simulator Methods

### Method: run()
Simulate the digester over a fixed time horizon at constant D and S1_in.

**Input parameters:**
- `days` (float, default=60.0) — How many days to simulate. Use 200+ days to ensure steady state is reached.
- `D` (float) — Dilution rate in d⁻¹. Must be positive. D = 1/HRT. Example: D=0.05 for HRT=20 days.
- `S1_in` (float) — Influent COD concentration in g/L. Typical range: 15–60 g/L.
- `initial` (AD4State, optional) — Starting state. If None, uses default healthy initial conditions.
- `n_points` (int, default=500) — Number of time points in output. More points = finer resolution.
- `rtol`, `atol` — ODE solver tolerances. Defaults (1e-6, 1e-8) are suitable for most cases.

**Returns:** AD4Result object. Call `.summary()` for a dict suitable for MCP tool response.

**Example:**
```python
sim = AD4Simulator()
result = sim.run(days=100, D=0.05, S1_in=30.0)
print(result.summary())
```

---

### Method: run_perturbation()
Simulate a sequence of operating conditions (multi-segment run). Useful for testing digester response to disturbances.

**Input:**
- `initial` (AD4State) — Starting state.
- `schedule` (list of dicts) — Each dict must have keys: `days`, `D`, `S1_in`.

**Example schedule — overload spike test:**
```python
schedule = [
    {'days': 30, 'D': 0.05, 'S1_in': 25.0},   # baseline
    {'days': 10, 'D': 0.05, 'S1_in': 60.0},   # overload
    {'days': 30, 'D': 0.05, 'S1_in': 25.0},   # recovery
]
result = sim.run_perturbation(AD4State(), schedule)
```

**Returns:** AD4Result spanning all segments concatenated.

---

### Method: find_steady_state()
Run a very long simulation (500 days) to find the equilibrium state for given D and S1_in.

**When to use:** Before running perturbation tests — start from the actual steady state rather than default initial conditions.

```python
ss = sim.find_steady_state(D=0.05, S1_in=30.0)
result = sim.run_perturbation(ss, schedule)
```

---

### Method: scan_dilution_rates()
Sweep a range of D values and return steady-state summaries for each. Generates the operating envelope.

**Input:**
- `D_range` (tuple, default=(0.1, 1.2)) — Min and max D values to scan.
- `n_steps` (int, default=24) — Number of D values.
- `S1_in` (float, default=25.0) — Influent COD held constant.
- `sim_days` (float, default=200.0) — Simulation time per D value.

**Returns:** List of summary dicts, one per D value. Useful for identifying D_crit experimentally.

---

### Method: critical_D()
Estimate the washout dilution rate by bisection search.

**Input:** `S1_in` (float) — Influent COD.
**Returns:** Critical D value (d⁻¹) above which methanogens cannot sustain themselves.

**Note:** The result is purely kinetic — it represents the theoretical maximum based on Haldane peak mu2. Practical safe operating limit is D < 0.4 * critical_D.

---

## SECTION 5: AD4Result — Output Fields

The `.summary()` method returns a dict with these fields:

| Field | Type | Description |
|-------|------|-------------|
| `duration_days` | float | Simulation duration |
| `dilution_rate_per_d` | float | D value used |
| `HRT_days` | float | 1/D — hydraulic retention time |
| `influent_COD_g_per_L` | float | S1_in used |
| `steady_state.S1_g_per_L` | float | Residual substrate at end |
| `steady_state.S2_mmol_per_L` | float | VFA at end — primary health indicator |
| `steady_state.X1_g_per_L` | float | Acidogen biomass at end |
| `steady_state.X2_g_per_L` | float | Methanogen biomass at end |
| `methane_mL_per_L_per_d` | float | Volumetric methane production at end |
| `washout` | bool | True if X2 fell below 0.05 g/L |
| `souring` | bool | True if S2 exceeded 150 mmol/L at any point |
| `healthy` | bool | True if S2 < 150, X1 > 0.01, X2 > 0.01 |
| `solver_ok` | bool | True if ODE integration succeeded |

### How to interpret results:

**Healthy normal operation:**
- healthy = True, washout = False, souring = False
- S2 < 30 mmol/L, X2 > 1.0 g/L, methane_mL_per_L_per_d > 200

**Early warning — approaching souring:**
- healthy = True (still), but S2 rising toward 80–100 mmol/L
- Action: reduce D or S1_in

**Souring in progress:**
- souring = True, healthy = False
- S2 > 150 mmol/L, methane declining
- Action: emergency reduction of feeding rate

**Washout:**
- washout = True, healthy = False
- X2 < 0.05 g/L, methane near zero
- Recovery requires: reduce D dramatically, possibly inoculate fresh methanogen culture

---

## SECTION 6: MCP Tool Inputs — Quick Reference

When calling AD4 tools from the MCP server, use these typical ranges:

| Parameter | Typical range | Unit | Notes |
|-----------|--------------|------|-------|
| dilution_rate | 0.03–0.10 | d⁻¹ | For HRT 10–33 days |
| influent_cod_g_per_l | 15–50 | g COD/L | Manure-based |
| days | 60–200 | days | 100+ recommended for steady state |
| overload_cod_g_per_l | 50–100 | g COD/L | For perturbation tests |
| overload_days | 5–15 | days | Duration of spike event |

---

## SECTION 7: MCP Tools — biomethane_operations_mcp_server_v5.py

The AD4 simulator is integrated into the MCP server as three callable tools:

### Tool: ad4_simulate()

Runs the 4-state AM2 simulation at steady operating conditions.

**Input (MCP tool call):**
```python
ad4_simulate(
    dilution_rate=0.05,      # D in d^-1
    influent_cod_g_per_l=25.0,  # S1_in in g/L
    days=100.0               # simulation horizon
)
```

**Output (interpreter labels added):**
- `steady_state` — S1, S2, X1, X2 at end
- `interpretation.S2_status` — "HEALTHY" / "WATCH" / "WARNING" / "CRITICAL"
- `interpretation.X2_status` — "ROBUST" / "NORMAL" / "LOW" / "NEAR_WASHOUT" / "WASHOUT"
- `interpretation.methane_status` — "GOOD" / "MODERATE" / "LOW" / "VERY_LOW"
- `interpretation.kinetic_params` — notes that params are Benyahia defaults

---

### Tool: ad4_critical_dilution_rate()

Finds the washout dilution rate threshold by bisection. Compares to current plant HRT.

**Input:**
```python
ad4_critical_dilution_rate(influent_cod_g_per_l=25.0)
```

**Output:**
- `D_crit_numerical_per_d` — actual washout threshold
- `safety_margin_pct` — current operating distance from washout
- `status` — "SAFE" / "CAUTION" / "WARNING"
- `recommended_max_D_per_d` — 40% of D_crit for safe operation

---

### Tool: ad4_perturbation_test()

Simulates a substrate overload spike and recovery.

**Input:**
```python
ad4_perturbation_test(
    overload_cod_g_per_l=80.0,  # COD during spike
    overload_days=10.0,        # spike duration
    recovery_days=30.0,        # recovery simulation
    baseline_cod_g_per_l=25.0   # normal COD
)
```

**Output:**
- `baseline_steady_state` — pre-spike conditions
- `peak_stress_during_overload` — S2_peak, X2_min during spike
- `post_recovery_state` — final state after recovery
- `washout_detected` — True if X2 < 0.05 at any point
- `souring_detected` — True if S2 > 150 at any point
- `recovered_healthy` — True if final state is healthy

---

## SECTION 8: CLI Commands — bio_cli.py

The AD4 tools are also accessible via CLI without invoking the LLM:

```bash
# Run steady-state simulation
python src/bio_cli.py simulate run --D 0.05 --S1 25 --days 100

# Find washout threshold
python src/bio_cli.py simulate critical-d --S1 25

# Test overload perturbation
python src/bio_cli.py simulate perturb --overload 80 --days 10 --recovery 30

# Output formats (text/json/csv)
python src/bio_cli.py simulate run --D 0.05 --S1 25 --format json
python src/bio_cli.py simulate run --D 0.05 --S1 25 --format csv --output sim.csv
```

---

## SECTION 9: Integration Notes

### Two Usage Paths

| Path | When to use |
|------|-------------|
| **CLI only** (bio_cli.py) | Quick manual checks, scripting, no AI needed |
| **MCP + LLM** (Gemma 4) | Operational reasoning, "what-if" scenarios, natural language interpretation |

### Calibration Status

The kinetic parameters (Ki2=256, mu2_max=0.74) are **Benyahia literature defaults**, not calibrated to any specific plant. Only k6 can be fitted to observed methane production in `biomethane_calibration.py`:

```python
from src.biomethane_calibration import fit_am2_k6_only
result = fit_am2_k6_only(D_measured=0.05, S1_in=25.0, Q_CH4_measured=50)
# Returns k6_fitted and CalibrationConfidence report
```

For operational decisions, use relative metrics (safety margin %, recovery status) rather than absolute predictions.

---

*Module: ad4_simulator.py*
*Parameters: Benyahia et al. (2012). Reference kinetics: Bernard et al. (2001).*
