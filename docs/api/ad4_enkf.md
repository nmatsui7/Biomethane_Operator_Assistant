# AD4 Ensemble Kalman Filter — API Reference

**Module**: `src/ad4_enkf.py`

---

## Overview

The Ensemble Kalman Filter (EnKF) is a Monte Carlo variant of the classic Kalman
Filter designed for **nonlinear state estimation**. Instead of propagating a
single state vector and its covariance matrix, the EnKF maintains an *ensemble*
of `N` state vectors whose sample mean and covariance approximate the true
posterior distribution.

### Why EnKF for anaerobic digestion?

Farm-scale digesters have a fundamental observability gap: **S2 (VFAs)** and
**X2 (methanogen biomass)** are the key stability indicators but are never
measured continuously. Only **Q_CH4** (methane flow) and **T** (temperature)
are available daily. The EnKF bridges this gap by:

1. **Forecast** — each ensemble member is propagated one day forward through
   the AD4 ODE (Haldane kinetics with temperature-corrected μ₂,max).
2. **Predict** — a predicted Q_CH4 is computed from each member via
   `H(x) = k₆ · μ₂(S₂) · X₂`.
3. **Update** — ensemble covariances are used to compute a Kalman gain that
   pulls all members toward the actual measurement.
4. **Report** — posterior means, standard deviations, and derived risk
   probabilities (souring, washout) are computed from the ensemble.

### Observability summary

| Variable | Symbol | Daily? | Source |
|----------|--------|--------|--------|
| Methane flow | Q_CH₄ | ✓ | biogas flow × CH₄% × volume conversion |
| Temperature | T | ✓ | digester temp sensor |
| VFA proxy | S₂ proxy | ✗ (weekly) | FOS/TAC → S₂ ≈ FOS / 60 |
| Substrate | S₁ | ✗ (hidden) | estimated, wide uncertainty |
| VFAs | S₂ | ✗ (hidden) | **key estimate**; FOS/TAC tightens this |
| Acidogens | X₁ | ✗ (hidden) | estimated, very wide uncertainty |
| Methanogens | X₂ | ✗ (hidden) | **key stability indicator** |

---

## EnKFConfig

Dataclass holding all tunable filter parameters. Located at `ad4_enkf.py:115`.

```python
@dataclass
class EnKFConfig:
    n_ensemble:          int   = 100
    T_ref_celsius:       float = 35.0
    theta_arrhenius:     float = 1.035
    process_noise_S1:    float = 1.0
    process_noise_S2:    float = 2.0
    process_noise_X1:    float = 0.05
    process_noise_X2:    float = 0.05
    obs_noise_Q_CH4:     float = 20.0
    obs_noise_S2_proxy:  float = 15.0
    init_std_S1:         float = 5.0
    init_std_S2:         float = 15.0
    init_std_X1:         float = 0.3
    init_std_X2:         float = 0.5
    S2_souring_threshold:  float = 80.0
    X2_washout_threshold:  float = 0.10
```

### Field reference

| Field | Default | Unit | Purpose |
|---|---|---|---|
| `n_ensemble` | `100` | — | Ensemble size. 50 = fast, 200 = more accurate. |
| `T_ref_celsius` | `35.0` | °C | Reference temperature at which AD4Params were calibrated. |
| `theta_arrhenius` | `1.035` | — | Temperature sensitivity coefficient (mesophilic range 1.02–1.08). |
| `process_noise_S1` | `1.0` | g COD/L/d | Process noise std dev for S₁ — feedstock variation. |
| `process_noise_S2` | `2.0` | mmol/L/d | Process noise std dev for S₂ — VFA dynamics uncertainty. |
| `process_noise_X1` | `0.05` | g VSS/L/d | Process noise std dev for X₁ — slow biomass change. |
| `process_noise_X2` | `0.05` | g VSS/L/d | Process noise std dev for X₂ — slow biomass change. |
| `obs_noise_Q_CH4` | `20.0` | mL/L/d | Observation noise std dev for Q_CH₄ (~10% at typical flow). |
| `obs_noise_S2_proxy` | `15.0` | mmol/L | Observation noise std dev for S₂ proxy (FOS/TAC conversion error). |
| `init_std_S1` | `5.0` | g COD/L | Initial ensemble spread for S₁. |
| `init_std_S2` | `15.0` | mmol/L | Initial ensemble spread for S₂. |
| `init_std_X1` | `0.3` | g VSS/L | Initial ensemble spread for X₁. |
| `init_std_X2` | `0.5` | g VSS/L | Initial ensemble spread for X₂. |
| `S2_souring_threshold` | `80.0` | mmol/L | S₂ level above which souring is considered likely. |
| `X2_washout_threshold` | `0.10` | g VSS/L | X₂ level below which washout is considered likely. |

### Tuning guidance

- **Process noise**: set larger when model error is significant (farm digesters
  have unmodelled mixing, dead zones, feedstock variation).
- **Observation noise Q_CH₄**: farm biogas meters are typically ±5–10%
  accurate. At Q_CH₄ ≈ 175 mL/L/d, std dev ≈ 15–20 mL/L/d.
- **Observation noise S₂ proxy**: the conversion `S₂ ≈ FOS/60` is approximate;
  set generously (10–20 mmol/L).
- **θ** (`theta_arrhenius`): 1.035 is standard for mesophilic methanogens
  (Rittmann & McCarty). Range 1.02–1.08 depending on population.

---

## Observation

Dataclass holding one time step of sensor measurements. Located at
`ad4_enkf.py:174`.

```python
@dataclass
class Observation:
    Q_CH4_mL_per_L:  float
    T_celsius:       float = 35.0
    S2_proxy_mmol_l: Optional[float] = None
```

### Fields

| Field | Required | Unit | Description |
|---|---|---|---|
| `Q_CH4_mL_per_L` | **yes** | mL/L/d | Methane production per litre of reactor volume. |
| `T_celsius` | no (default 35.0) | °C | Digester temperature (used for Arrhenius correction). |
| `S2_proxy_mmol_l` | no (default None) | mmol/L | S₂ proxy derived from FOS/TAC measurement, typically available weekly. |

### `Observation.from_scada` (classmethod)

```python
@classmethod
def from_scada(
    cls,
    biogas_flow_nm3h: float,
    ch4_pct: float,
    digester_volume_m3: float,
    digester_temp_c: float = 35.0,
    fos_mg_per_l: Optional[float] = None,
) -> "Observation":
```

Converts raw SCADA plant-state values into an `Observation` ready for the EnKF.

#### Conversion equations

**Methane flow (mL/L/d):**

```text
Q_nm3_per_day = biogas_flow_nm3h × 24 × (ch4_pct / 100)
Q_mL_per_L    = Q_nm3_per_day × 1e6 / (digester_volume_m3 × 1000)
```

**S₂ proxy (FOS/TAC → mmol/L):**

```text
S2_proxy = fos_mg_per_l / 60.0    # acetic acid MW = 60 g/mol
```

#### Parameters

| Parameter | Unit | Description |
|---|---|---|
| `biogas_flow_nm3h` | Nm³/h | Biogas flow from plant-state SCADA. |
| `ch4_pct` | % | Methane percentage (e.g. `62.0` for 62%). |
| `digester_volume_m3` | m³ | Fixed plant parameter — digester working volume. |
| `digester_temp_c` | °C | Digester temperature from SCADA (default 35.0). |
| `fos_mg_per_l` | mg/L | FOS (volatile acids) measurement if available (typically weekly). |

#### Example

```python
obs = Observation.from_scada(
    biogas_flow_nm3h=320.0,
    ch4_pct=62.0,
    digester_volume_m3=2000.0,
    digester_temp_c=36.5,
    fos_mg_per_l=600.0,       # weekly FOS/TAC result
)
# obs.Q_CH4_mL_per_L ≈ 297.6
# obs.S2_proxy_mmol_l ≈ 10.0
```

---

## EnKFEstimate

Dataclass representing the posterior state estimate at one time step. Located at
`ad4_enkf.py:226`.

```python
@dataclass
class EnKFEstimate:
    day:                int
    S1_mean:            float   # g COD/L
    S2_mean:            float   # mmol/L  — KEY indicator
    X1_mean:            float   # g VSS/L
    X2_mean:            float   # g VSS/L — KEY stability indicator
    Q_CH4_predicted:    float   # mL/L/d — model prediction before update
    S1_std:             float
    S2_std:             float
    X1_std:             float
    X2_std:             float
    souring_probability:  float # P(S₂ > config.S2_souring_threshold)
    washout_probability:  float # P(X₂ < config.X2_washout_threshold)
    innovation_Q_CH4:   float   # observation minus prediction
    T_celsius:          float
    mu2_max_effective:  float   # temperature-corrected μ₂,max
```

All `_mean` and `_std` fields are computed from the ensemble distribution.
Probabilities are Monte Carlo estimates (fraction of ensemble members exceeding
the threshold).

### Methods

#### `S2_interval_95() -> tuple`

Returns the 95% confidence interval for S₂ as `(lower, upper)`:

```python
def S2_interval_95(self) -> tuple:
    return (
        max(0.0, self.S2_mean - 1.96 * self.S2_std),
        self.S2_mean + 1.96 * self.S2_std,
    )
```

#### `X2_interval_95() -> tuple`

Returns the 95% confidence interval for X₂ as `(lower, upper)`.

#### `risk_level() -> str`

Returns an integrated risk assessment string based on the thresholds below.

#### `to_dict() -> dict`

Serialises the estimate to a dictionary suitable for JSON responses.

```python
{
    "day":                  42,
    "S2_mean_mmol_per_L":   45.3,
    "S2_std_mmol_per_L":    12.1,
    "S2_95pct_interval":    [21.6, 69.0],
    "X2_mean_g_per_L":      1.2345,
    "X2_std_g_per_L":       0.1234,
    "X2_95pct_interval":    [0.9926, 1.4764],
    "S1_mean_g_per_L":      4.567,
    "X1_mean_g_per_L":      0.789,
    "Q_CH4_predicted":      172.3,
    "souring_probability":  0.02,
    "washout_probability":  0.0005,
    "risk_level":           "HEALTHY",
    "mu2_max_effective":    0.7600,
    "T_celsius":            36.5,
    "innovation_Q_CH4":     2.7,
    "calibration_note":     "S2 and X2 are ESTIMATED, not measured. ...",
}
```

### Risk level thresholds

| Level | Condition | Meaning |
|---|---|---|
| `CRITICAL` | `washout_probability > 0.20` | High washout risk — X₂ critically low. Reduce feeding immediately. |
| `WARNING` | `souring_probability > 0.30` **or** `S2_mean > 80.0` | S₂ likely in the danger zone. Consider reducing OLR. |
| `WATCH` | `souring_probability > 0.10` | S₂ elevated. Monitor closely, no immediate action. |
| `HEALTHY` | all below thresholds | Digester appears stable. |

The CRITICAL threshold is **asymmetric**: it is driven by X₂ washout (the more
dangerous failure mode), not S₂ souring.

---

## AD4EnKF

Core EnKF implementation. Located at `ad4_enkf.py:324`.

### Algorithm (one time step)

```
1. FORECAST:   propagate each ensemble member one day via AD4 ODE
               with temperature-corrected μ₂,max, then add process noise.

2. PREDICT:    compute H(x) = k₆ · μ₂(S₂) · X₂ for each member.

3. UPDATE:     compute ensemble covariances → Kalman gain → update
               all members (Burgers et al. perturbed observation approach).

4. CLIP:       enforce physical bounds (all states ≥ 0).

5. REPORT:     posterior means, stds, risk probabilities.
```

**State vector**: `x = [S₁, S₂, X₁, X₂]` stored as an `(n_ensemble × 4)` matrix.

### `__init__`

```python
def __init__(
    self,
    params:        Optional[AD4Params]  = None,
    config:        Optional[EnKFConfig] = None,
    initial_state: Optional[AD4State]   = None,
    D:             float = 1.0 / 22.0,
    S1_in:         float = 25.0,
):
```

| Parameter | Default | Description |
|---|---|---|
| `params` | `AD4Params()` (Benyahia defaults) | Kinetic parameters for the AD4 ODE. |
| `config` | `EnKFConfig()` | Tuning parameters (ensemble size, noise, thresholds). |
| `initial_state` | `AD4State()` | Best guess at starting digester state. |
| `D` | `1/22 ≈ 0.0455` d⁻¹ | Dilution rate = 1 / HRT. Set from plant hydraulic retention time. |
| `S1_in` | `25.0` g COD/L | Influent substrate concentration. Set from feedstock records. |

The ensemble is initialised by drawing `n_ensemble` samples from a Gaussian
centred on `initial_state` with standard deviations from `EnKFConfig.init_std_*`.

### Kalman update equations

The `_kalman_update` method (line 442) implements the **perturbed observation**
EnKF (Burgers et al. 1998):

```
Given:
  X        : (N, 4) prior ensemble matrix
  H(X)     : (N,)   predicted observations for each member
  y        : scalar actual observation
  σ_obs    : scalar observation noise std dev

1. Perturb observation:  ỹᵢ = y + 𝒩(0, σ_obs²)   for each member i
2. Compute means:        x̄ = mean(X),  h̄ = mean(H(X))
3. Anomalies:            X' = X - x̄,   H' = H(X) - h̄
4. Cross-covariance:     Pₓₕ = X'ᵀ H' / (N - 1)    (4,)
5. Obs. variance:        Pₕₕ = H'ᵀ H' / (N - 1)    scalar
6. Observation error:    R = σ_obs²
7. Kalman gain:          K = Pₓₕ / (Pₕₕ + R)        (4,)
8. Update:               X₊ = max(0, X + (ỹ - H(X)) · K)
```

Process noise is added **before** the update step (additive Gaussian, scaled by
`EnKFConfig.process_noise_*`).

### `update`

```python
def update(self, obs: Observation) -> EnKFEstimate:
```

Advances the filter by one day. Call once per day with the latest observations.

**Returns**: `EnKFEstimate` with posterior means, standard deviations, and risk
probabilities.

#### Update logic

| Step | Always? | Description |
|---|---|---|
| Forecast | ✓ | Propagate all members via AD4 ODE with temperature-corrected μ₂,max. |
| Process noise | ✓ | Add Gaussian noise scaled by `process_noise_*`. |
| Q_CH₄ update | ✓ | Always performed. `_kalman_update` with `obs_noise_Q_CH4`. |
| S₂ proxy update | ✗ | Only if `obs.S2_proxy_mmol_l is not None`. `_kalman_update` with `obs_noise_S2_proxy`. |

### `current_estimate`

```python
def current_estimate(self) -> Optional[EnKFEstimate]:
```

Returns the most recent `EnKFEstimate`, or `None` if no updates have been
performed yet.

### `history`

```python
def history(self) -> List[EnKFEstimate]:
```

Returns the full list of all posterior estimates (one per `update` call), in
chronological order.

### `update_D`

```python
def update_D(self, new_D: float):
```

Updates the dilution rate. Raises `ValueError` if `new_D ≤ 0`. Call when HRT
changes (e.g. feedstock recipe change alters retention time).

### `update_S1_in`

```python
def update_S1_in(self, new_S1_in: float):
```

Updates the influent COD. Raises `ValueError` if `new_S1_in < 0`. Call when
feedstock composition changes.

### `souring_trend`

```python
def souring_trend(self, window_days: int = 7) -> Optional[str]:
```

Detects rising S₂ trend over the last N days using a linear fit
(`numpy.polyfit` with degree 1).

| Returns | Slope condition |
|---|---|
| `"RISING"` | slope > 1.0 mmol/L/d |
| `"FALLING"` | slope < -1.0 mmol/L/d |
| `"STABLE"` | otherwise |
| `None` | insufficient history (< `window_days` estimates) |

### Private methods

#### `_mu2_max_at_T`

```python
def _mu2_max_at_T(self, T: float) -> float:
```

Arrhenius-corrected μ₂,max:

```text
μ₂,max(T) = μ₂,max(T_ref) × θ^(T − T_ref)
```

#### `_odes`

```python
def _odes(self, t: float, y: np.ndarray,
          D: float, S1_in: float, mu2_max_eff: float) -> list:
```

AD4 ODE right-hand side. Preserves the full Haldane nonlinearity:

```text
μ₁ = μ₁,max · S₁ / (Kₛ₁ + S₁)
μ₂ = μ₂,max · S₂ / (Kₛ₂ + S₂ + S₂²/Kᵢ₂)

dS₁/dt = D · (S₁,in − S₁) − k₁ · μ₁ · X₁
dS₂/dt = D · (0 − S₂)     + k₂ · μ₁ · X₁ − k₃ · μ₂ · X₂
dX₁/dt = (μ₁ − α·D) · X₁
dX₂/dt = (μ₂ − α·D) · X₂
```

#### `_propagate_one`

```python
def _propagate_one(self, y0: np.ndarray, mu2_max_eff: float) -> np.ndarray:
```

Integrates one ensemble member forward one day using `scipy.integrate.solve_ivp`
(RK45, rtol=1e-4, atol=1e-6). Clips all states to ≥ 0.

#### `_predict_Q_CH4`

```python
def _predict_Q_CH4(self, ensemble: np.ndarray, mu2_max_eff: float) -> np.ndarray:
```

Observation operator H(x) for methane:

```text
Q_CH₄ [mL/L/d] = k₆ · μ₂(S₂) · X₂
```

Returns array of shape `(n_ensemble,)`.

#### `_predict_S2`

```python
def _predict_S2(self, ensemble: np.ndarray) -> np.ndarray:
```

Observation operator for direct S₂ observation (FOS/TAC proxy). Simply returns
`ensemble[:, 1]`.

---

## AD4EnKFServer

MCP-ready wrapper that returns dicts suitable for tool responses. Located at
`ad4_enkf.py:616`.

### `__init__`

```python
def __init__(
    self,
    digester_volume_m3: float = 2000.0,
    params:  Optional[AD4Params]  = None,
    config:  Optional[EnKFConfig] = None,
):
```

### `initialise`

```python
def initialise(
    self,
    hrt_days: float,
    S1_in: float = 25.0,
    initial_state: Optional[AD4State] = None,
) -> dict:
```

Call once at startup. Creates the internal `AD4EnKF` instance.

**Returns**:

```python
{
    "status":        "INITIALISED",
    "HRT_days":      22.0,
    "D_per_d":       0.04545,
    "S1_in_g_per_L": 25.0,
    "n_ensemble":    100,
    "T_ref_celsius": 35.0,
    "message":       "EnKF initialised. Call enkf_update() daily ...",
}
```

### `step`

```python
def step(
    self,
    biogas_flow_nm3h: float,
    ch4_pct: float,
    digester_temp_c: float = 35.0,
    fos_mg_per_l: Optional[float] = None,
    new_hrt_days: Optional[float] = None,
    new_S1_in: Optional[float] = None,
) -> dict:
```

Advances the filter by one day. Call once per day with SCADA values.

| Parameter | Unit | Description |
|---|---|---|
| `biogas_flow_nm3h` | Nm³/h | From plant-state. |
| `ch4_pct` | % | From plant-state. |
| `digester_temp_c` | °C | From plant-state (default 35.0). |
| `fos_mg_per_l` | mg/L | Optional FOS measurement (weekly). |
| `new_hrt_days` | days | Update HRT if it changed. |
| `new_S1_in` | g COD/L | Update influent COD if feedstock changed. |

**Returns**: `EnKFEstimate.to_dict()` augmented with:

| Key | Source |
|---|---|
| `S2_trend_7d` | `AD4EnKF.souring_trend(window_days=7)` |
| `guidance` | `AD4EnKFServer._guidance(estimate, trend)` |

### `summary`

```python
def summary(self) -> dict:
```

Returns filter health and status:

```python
{
    "status":        "RUNNING",
    "days_tracked":  60,
    "current_risk":  "HEALTHY",
    "S2_trend_7d":   "STABLE",
    "n_ensemble":    100,
}
```

If not yet initialised: `{"status": "NOT_INITIALISED"}`.

### `_guidance`

```python
def _guidance(self, est: EnKFEstimate, trend: Optional[str]) -> str:
```

Generates plain-language operational guidance. Concatenates parts with ` | `:

| Condition | Guidance content |
|---|---|
| CRITICAL | URGENT: washout probability, reduce feeding immediately. |
| WARNING | WARNING: S₂ estimate with 95% CI, consider reducing OLR. |
| WATCH | No immediate action, monitor trend. |
| HEALTHY | Digester appears healthy with S₂ and X₂ estimates. |
| RISING trend | Early warning of possible overloading. |
| FALLING trend | Digester recovering. |
| T < 33°C | Reduced μ₂,max via Arrhenius, D_crit lower than usual. |
| T > 40°C | Check for thermophilic transition risk. |

---

## Usage examples

### Basic daily update loop

```python
from ad4_enkf import AD4EnKF, EnKFConfig, Observation

config = EnKFConfig(n_ensemble=100, T_ref_celsius=35.0)
enkf = AD4EnKF(config=config)

obs = Observation(Q_CH4_mL_per_L=175.0, T_celsius=36.5)
state = enkf.update(obs)

print(state.S2_mean, state.S2_std)         # VFA estimate with uncertainty
print(state.souring_probability)            # P(S₂ > 80 mmol/L)
print(state.washout_probability)            # P(X₂ < 0.1 g/L)
```

### Using `from_scada` with SCADA values

```python
from ad4_enkf import Observation

obs = Observation.from_scada(
    biogas_flow_nm3h=320.0,
    ch4_pct=62.0,
    digester_volume_m3=2000.0,
    digester_temp_c=36.5,
    fos_mg_per_l=600.0,
)
```

### MCP server pattern

```python
from ad4_enkf import AD4EnKFServer

server = AD4EnKFServer(digester_volume_m3=2000.0)
server.initialise(hrt_days=22.0, S1_in=25.0)

# Called daily with plant_state values
result = server.step(
    biogas_flow_nm3h=320.0,
    ch4_pct=62.0,
    digester_temp_c=36.5,
    fos_mg_per_l=600.0,
)
print(result["risk_level"])
print(result["guidance"])
```

### Accessing confidence intervals

```python
est = enkf.current_estimate()
s2_lo, s2_hi = est.S2_interval_95()
x2_lo, x2_hi = est.X2_interval_95()
print(f"S₂ 95% CI: [{s2_lo:.1f}, {s2_hi:.1f}] mmol/L")
print(f"X₂ 95% CI: [{x2_lo:.3f}, {x2_hi:.3f}] g VSS/L")
```

---

## References

- Evensen, G. (1994). Sequential data assimilation with a nonlinear
  quasi-geostrophic model using Monte Carlo methods to forecast error
  statistics. *J. Geophys. Res.*, 99(C5), 10143–10162.
- Burgers, G., van Leeuwen, P. J., & Evensen, G. (1998). Analysis scheme in
  the ensemble Kalman filter. *Mon. Weather Rev.*, 126(6), 1719–1724.
- Bernard et al. (2001). *Biotechnology and Bioengineering*, 75(4), 424–438.
- Dochain, D. (2008). *State and Parameter Estimation in Chemical and
  Biochemical Processes*. CRC Press.
- Rittmann, B. E. & McCarty, P. L. (2001). *Environmental Biotechnology:
  Principles and Applications*. McGraw-Hill.
