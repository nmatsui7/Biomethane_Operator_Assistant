# `AD4Simulator` — 4-State AM2 Anaerobic Digestion Model

## Overview

`ad4_simulator.py` implements the **AM2 anaerobic digestion model** (4-state
simplification) described by Bernard et al. (2001). It models a
continuous-stirred tank reactor (CSTR) digester with two microbial populations:

| Symbol | State        | Unit       | Description                                |
|--------|-------------|------------|--------------------------------------------|
| S1     | Substrate   | g COD/L    | Organic feed (chemical oxygen demand)      |
| S2     | VFA         | mmol/L     | Volatile fatty acids (intermediate)        |
| X1     | Acidogens   | g VSS/L    | Acidogenic bacteria (S1 → S2)              |
| X2     | Methanogens | g VSS/L    | Methanogenic archaea (S2 → CH₄)            |

**Acidogen kinetics** follow Monod (no inhibition).  
**Methanogen kinetics** follow Haldane (inhibition at high VFA).

Stoichiometric coefficients are adapted from Benyahia et al. (2012) and
representative of **farm-scale mesophilic manure digesters (~35 °C)**.

```python
from ad4_simulator import AD4Simulator, AD4Params, AD4State
```

---

## AD4Params

Dataclass holding all kinetic and stoichiometric parameters.

```python
@dataclass
class AD4Params(mu1_max=1.20, Ks1=7.10, k1=42.14, k2=116.50,
                mu2_max=0.74, Ks2=9.28, Ki2=256.0, k3=268.0,
                alpha=1.0, k6=453.0)
```

### Fields

| Field      | Default  | Unit            | Description                                      |
|-----------|----------|-----------------|--------------------------------------------------|
| `mu1_max` | `1.20`   | d⁻¹             | Max specific growth rate of acidogens            |
| `Ks1`     | `7.10`   | g COD/L         | Half-saturation constant for S1 (Monod)          |
| `k1`      | `42.14`  | g COD/g COD     | S1 consumption yield coefficient                 |
| `k2`      | `116.50` | mmol VFA/g COD  | S2 production yield coefficient                  |
| `mu2_max` | `0.74`   | d⁻¹             | Max specific growth rate of methanogens          |
| `Ks2`     | `9.28`   | mmol/L          | Half-saturation constant for S2 (Haldane)        |
| `Ki2`     | `256.0`  | mmol/L          | Inhibition constant for S2 (Haldane)             |
| `k3`      | `268.0`  | mmol VFA/g VSS  | S2 consumption yield coefficient                 |
| `alpha`   | `1.0`    | —               | Biomass washout factor (1.0 = CSTR, <1 with settler) |
| `k6`      | `453.0`  | mL CH₄/mmol S2  | Methane production coefficient                   |

### Methods

#### `mu1(S1: float) -> float`

Monod growth rate for acidogens.

```
μ₁(S₁) = μ₁_max · S₁ / (K_s1 + S₁)
```

#### `mu2(S2: float) -> float`

Haldane growth rate for methanogens (inhibited at high VFA).

```
μ₂(S₂) = μ₂_max · S₂ / (K_s2 + S₂ + S₂² / K_i2)
```

#### `mu2_max_achievable() -> float`

Peak of the Haldane curve (occurs at `S₂ = √(K_s2 · K_i2)`).

```
S₂_opt = sqrt(Ks2 * Ki2)
return mu2(S₂_opt)
```

#### `critical_dilution_rate() -> float`

Approximate washout threshold for methanogens. Above this `D` the digester
will fail.

```
return mu2_max_achievable()
```

#### `mu2_max_at_temp(T_celsius: float, T_ref: float = 35.0, theta: float = 1.035) -> float`

Temperature-corrected maximum methanogen growth rate using the modified
Arrhenius equation (Rittmann & McCarty 2001).

```
μ₂_max(T) = μ₂_max · θ^(T − T_ref)
```

| Argument    | Default | Description                                        |
|------------|---------|----------------------------------------------------|
| `T_celsius` | —       | Actual digester temperature (°C)                   |
| `T_ref`     | `35.0`  | Reference temperature at which μ₂_max was calibrated |
| `theta`     | `1.035` | Temperature sensitivity (1.02–1.08 typical)        |

**Examples:**

- At 32 °C: `0.74 × 1.035^(32-35) = 0.74 × 0.901 = 0.667 d⁻¹`
- At 38 °C: `0.74 × 1.035^(38-35) = 0.74 × 1.109 = 0.821 d⁻¹`

#### `mu2_at_temp(S2: float, T_celsius: float, T_ref: float = 35.0, theta: float = 1.035) -> float`

Haldane growth rate with Arrhenius temperature correction. Combines
`mu2_max_at_temp()` with the Haldane inhibition term.

#### `critical_dilution_rate_at_temp(T_celsius: float, T_ref: float = 35.0, theta: float = 1.035) -> float`

Temperature-corrected washout threshold. Scales `D_crit` with the Haldane
peak using the corrected `μ₂_max`.

---

## AD4State

Dataclass representing a snapshot of the digester state vector.

```python
@dataclass
class AD4State(S1=3.0, S2=0.5, X1=0.5, X2=1.5)
```

| Field | Default | Unit       | Description                              |
|-------|---------|------------|------------------------------------------|
| `S1`  | `3.0`   | g COD/L    | Organic substrate concentration          |
| `S2`  | `0.5`   | mmol/L     | Volatile fatty acids (healthy = low)     |
| `X1`  | `0.5`   | g VSS/L    | Acidogenic bacteria concentration        |
| `X2`  | `1.5`   | g VSS/L    | Methanogenic archaea concentration       |

### Methods

#### `to_array() -> np.ndarray`

Returns `[S1, S2, X1, X2]` as a NumPy array.

#### `from_array(cls, y: np.ndarray) -> AD4State`

Class method. Constructs an `AD4State` from a 4-element array `[S1, S2, X1, X2]`.

#### `is_healthy(vfa_threshold: float = 150.0) -> bool`

Rough health check: returns `True` when VFA is below threshold and both
biomass concentrations are above `0.01`.

```python
state.is_healthy()          # True for default values
state.is_healthy(threshold=200.0)  # custom VFA threshold
```

---

## AD4Result

Dataclass returned by `AD4Simulator.run()` and `run_perturbation()`.

```python
@dataclass
class AD4Result(t, S1, S2, X1, X2, params, D, S1_in, success, message)
```

| Field     | Type         | Description                             |
|-----------|-------------|-----------------------------------------|
| `t`       | `np.ndarray` | Time vector (days)                      |
| `S1`      | `np.ndarray` | Substrate trajectory (g COD/L)          |
| `S2`      | `np.ndarray` | VFA trajectory (mmol/L)                 |
| `X1`      | `np.ndarray` | Acidogen trajectory (g VSS/L)           |
| `X2`      | `np.ndarray` | Methanogen trajectory (g VSS/L)         |
| `params`  | `AD4Params`  | Parameter set used for the simulation   |
| `D`       | `float`      | Dilution rate (d⁻¹)                     |
| `S1_in`   | `float`      | Influent substrate (g COD/L)            |
| `success` | `bool`       | ODE solver convergence status           |
| `message` | `str`        | Solver status message                   |

### Methods

#### `methane_flow() -> np.ndarray`

Approximate volumetric CH₄ production rate (mL/L/d). Based on S₂ consumption
by X₂:

```
CH₄(t) = k₆ · μ₂(S₂(t)) · X₂(t)
```

#### `vfa_risk(threshold: float = 150.0) -> np.ndarray`

Boolean array, `True` where `S₂` exceeds the souring threshold.

#### `washout_detected(x2_floor: float = 0.05) -> bool`

`True` if the final methanogen population `X₂[-1]` has collapsed below the
floor.

#### `souring_detected(threshold: float = 150.0) -> bool`

`True` if VFA exceeded the threshold at **any** point during the simulation.

#### `steady_state() -> AD4State`

Returns the final state of the simulation as an `AD4State`, approximating the
steady-state equilibrium.

#### `summary() -> dict`

Human-readable summary dictionary, suitable for API responses.

| Key                    | Type   | Description                           |
|------------------------|--------|---------------------------------------|
| `duration_days`        | `float`| Total simulation time                 |
| `dilution_rate_per_d`  | `float`| Dilution rate used                    |
| `HRT_days`             | `float`| Hydraulic retention time (1/D)        |
| `influent_COD_g_per_L` | `float`| Influent substrate                    |
| `steady_state`         | `dict` | Final S1, S2, X1, X2                 |
| `methane_mL_per_L_per_d` | `float` | Final CH₄ production rate          |
| `washout`              | `bool` | Washout detected?                     |
| `souring`              | `bool` | Souring event detected?               |
| `healthy`              | `bool` | Final state passes health check?      |
| `solver_ok`            | `bool` | ODE solver converged?                 |

---

## AD4Simulator

Core simulator class. Integrates the ODE system using `scipy.integrate.solve_ivp`
(RK45).

```python
class AD4Simulator
```

### `__init__(params: Optional[AD4Params] = None)`

| Argument | Default       | Description                              |
|----------|---------------|------------------------------------------|
| `params` | `AD4Params()` | Kinetic parameters (farm-scale mesophilic defaults if omitted) |

### `run(days=60.0, D=0.5, S1_in=25.0, initial=None, n_points=500, rtol=1e-6, atol=1e-8) -> AD4Result`

Simulate the digester over a time horizon.

| Argument    | Default       | Description                              |
|-------------|---------------|------------------------------------------|
| `days`      | `60.0`        | Simulation duration (days)               |
| `D`         | `0.5`         | Dilution rate (d⁻¹); HRT = 1/D           |
| `S1_in`     | `25.0`        | Influent organic substrate (g COD/L)     |
| `initial`   | `AD4State()`  | Starting state                           |
| `n_points`  | `500`         | Number of output time points             |
| `rtol`      | `1e-6`        | Relative tolerance for ODE solver        |
| `atol`      | `1e-8`        | Absolute tolerance for ODE solver        |

**Raises:** `ValueError` if `D ≤ 0` or `S1_in < 0`.

```python
sim = AD4Simulator()
result = sim.run(days=60, D=0.5, S1_in=25.0)
print(result.summary())
```

### `run_perturbation(initial: AD4State, schedule: list[dict], n_points_per_segment: int = 200) -> AD4Result`

Multi-segment simulation where `D` or `S1_in` changes over time. Each segment
is simulated separately and concatenated; the final state of one segment feeds
as initial condition to the next.

| Argument                | Default | Description                              |
|-------------------------|---------|------------------------------------------|
| `initial`               | —       | Starting state                           |
| `schedule`              | —       | List of segment dicts (see below)        |
| `n_points_per_segment`  | `200`   | Output resolution per segment            |

**Schedule format — each dict:**

| Key     | Type    | Description                          |
|---------|---------|--------------------------------------|
| `days`  | `float` | Duration of this segment             |
| `D`     | `float` | Dilution rate for this segment       |
| `S1_in` | `float` | Influent COD for this segment        |

```python
schedule = [
    {'days': 30, 'D': 0.5,  'S1_in': 25.0},   # normal operation
    {'days': 10, 'D': 0.5,  'S1_in': 60.0},   # organic overload spike
    {'days': 30, 'D': 0.5,  'S1_in': 25.0},   # recovery
]
result = sim.run_perturbation(AD4State(), schedule)
```

### `find_steady_state(D: float, S1_in: float, initial: Optional[AD4State] = None) -> AD4State`

Numerically approximate the steady-state equilibrium via a long (500-day)
simulation. More robust than algebraic solvers across washout / souring
regimes.

| Argument  | Default       | Description                              |
|-----------|---------------|------------------------------------------|
| `D`       | —             | Dilution rate (d⁻¹)                      |
| `S1_in`   | —             | Influent substrate (g COD/L)             |
| `initial` | `AD4State()`  | Starting state                           |

```python
ss = sim.find_steady_state(D=0.5, S1_in=25.0)
print(f"Steady-state S2 = {ss.S2:.2f} mmol/L")
```

### `scan_dilution_rates(D_range=(0.1, 1.2), n_steps=24, S1_in=25.0, sim_days=200.0) -> list[dict]`

Sweep dilution rates and return steady-state summaries. Useful for generating
operating envelope data.

| Argument   | Default        | Description                           |
|------------|----------------|---------------------------------------|
| `D_range`  | `(0.1, 1.2)`   | (min, max) dilution rate              |
| `n_steps`  | `24`           | Number of steps in the sweep          |
| `S1_in`    | `25.0`         | Influent substrate (g COD/L)          |
| `sim_days` | `200.0`        | Simulation time per D value           |

```python
results = sim.scan_dilution_rates()
for r in results:
    print(f"D={r['D']:.2f}  washout={r['washout']}  CH₄={r['methane_mL_per_L_per_d']}")
```

### `critical_D(S1_in=25.0) -> float`

Binary search for the washout dilution rate. Above this `D`, methanogens
cannot sustain themselves (`X₂` falls below `0.05` after 300 days).

| Argument | Default | Description                              |
|----------|---------|------------------------------------------|
| `S1_in`  | `25.0`  | Influent substrate (g COD/L)             |

```python
D_crit = sim.critical_D(S1_in=25.0)
print(f"Critical dilution rate: {D_crit} d⁻¹  (HRT = {1/D_crit:.1f} d)")
```

---

## ODE Model Equations

The CSTR mass balances, implemented in `AD4Simulator._odes()`:

```
dS₁/dt = D · (S₁_in − S₁) − k₁ · μ₁(S₁) · X₁
dS₂/dt = D · (0 − S₂)     + k₂ · μ₁(S₁) · X₁ − k₃ · μ₂(S₂) · X₂
dX₁/dt = (μ₁(S₁) − α · D) · X₁
dX₂/dt = (μ₂(S₂) − α · D) · X₂
```

With growth kinetics:

```
μ₁(S₁) = μ₁_max · S₁ / (K_s1 + S₁)                              (Monod)
μ₂(S₂) = μ₂_max · S₂ / (K_s2 + S₂ + S₂² / K_i2)                (Haldane)
```

All states are clamped to ≥ 0 to suppress numerical noise near zero.

---

## Temperature Correction

The model includes an optional Arrhenius-type temperature correction for
methanogen kinetics, standard for mesophilic digesters (Rittmann & McCarty
2001).

**Correction factor:**

```
θ^(T − T_ref)
```

Applied in three methods:

- `mu2_max_at_temp(T_celsius, T_ref=35.0, theta=1.035)` — scales `μ₂_max`
- `mu2_at_temp(S2, T_celsius, T_ref=35.0, theta=1.035)` — full Haldane with
  corrected `μ₂_max`
- `critical_dilution_rate_at_temp(T_celsius, T_ref=35.0, theta=1.035)` —
  washout threshold at operating temperature

The reference temperature `T_ref = 35 °C` matches the calibration of the
default parameters (Benyahia et al. 2012). The sensitivity `θ = 1.035` is
typical for mesophilic methanogens; values of 1.02 (robust) to 1.08 (sensitive)
may be used.

**Operational note:** In winter at lower digester temperatures, `D_crit`
decreases — the safe operating envelope shrinks.
