"""
ad4_enkf.py
===========
Ensemble Kalman Filter (EnKF) for the AD4 anaerobic digestion model.

PURPOSE
-------
Estimates hidden digester states (S2, X2) from observable measurements
(Q_CH4, temperature) using the AD4 ODE as the physical process model.

This solves the core farm-scale problem: you can measure methane flow
and temperature continuously, but never S2 (VFA) or X2 (methanogen biomass).
The EnKF treats S2 and X2 as uncertain quantities whose probability
distributions are updated every time a new measurement arrives.

DESIGN PRINCIPLES
-----------------
1. The AD4 ODE is the process model — Haldane nonlinearity is preserved.
2. Uncertainty is explicit — every estimate comes with a confidence interval.
3. Graceful degradation — if only Q_CH4 is available (no FOS/TAC), the
   filter still runs but with wider S2 uncertainty.
4. Temperature correction — mu2_max is adjusted daily via Arrhenius.
5. Honest about limits — souring risk is reported as a probability,
   not a binary flag.

OBSERVABILITY
-------------
Observable (daily):
    Q_CH4 [mL/L/d]  — biogas_flow_nm3h × CH4% × volume conversion
    T [°C]           — digester_temp_c (used for Arrhenius correction)

Observable (weekly, optional):
    S2_proxy [mmol/L] — from FOS/TAC: S2 ≈ FOS_mg_per_L / 60.0

Hidden (never measured at farm scale):
    S1 [g COD/L]   — estimated but wide uncertainty
    S2 [mmol/L]    — estimated; FOS/TAC tightens this
    X1 [g VSS/L]   — estimated; very wide uncertainty
    X2 [g VSS/L]   — estimated; key stability indicator

REFERENCES
----------
- Evensen, G. (1994). Sequential data assimilation with a nonlinear
  quasi-geostrophic model using Monte Carlo methods to forecast error
  statistics. J. Geophys. Res., 99(C5), 10143–10162.
- Bernard et al. (2001). Biotechnology and Bioengineering, 75(4), 424–438.
- Dochain, D. (2008). State and Parameter Estimation in Chemical and
  Biochemical Processes. CRC Press.

USAGE
-----
    from ad4_enkf import AD4EnKF, EnKFConfig, Observation

    config = EnKFConfig(n_ensemble=100, T_ref_celsius=35.0)
    enkf = AD4EnKF(config=config)

    # Daily update loop
    obs = Observation(Q_CH4_mL_per_L=175.0, T_celsius=36.5)
    enkf.update(obs)

    state = enkf.current_estimate()
    print(state.S2_mean, state.S2_std)          # VFA estimate with uncertainty
    print(state.souring_probability)             # P(S2 > 80 mmol/L)
    print(state.washout_probability)             # P(X2 < 0.1 g/L)
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Optional, List

import numpy as np
from scipy.integrate import solve_ivp


# ── Try to import AD4Params; fall back to embedded copy ───────────────────────
try:
    from ad4_simulator import AD4Params, AD4State
except ImportError:
    # Minimal embedded copy so this module is self-contained
    from dataclasses import dataclass as _dc

    @_dc
    class AD4Params:
        mu1_max: float = 1.20
        Ks1:     float = 7.10
        k1:      float = 42.14
        k2:      float = 116.50
        mu2_max: float = 0.74
        Ks2:     float = 9.28
        Ki2:     float = 256.0
        k3:      float = 268.0
        alpha:   float = 1.0
        k6:      float = 453.0

        def mu1(self, S1): return self.mu1_max * S1 / (self.Ks1 + S1)
        def mu2(self, S2): return self.mu2_max * S2 / (self.Ks2 + S2 + S2**2 / self.Ki2)

    @_dc
    class AD4State:
        S1: float = 3.0
        S2: float = 0.5
        X1: float = 0.5
        X2: float = 1.5
        def to_array(self): return np.array([self.S1, self.S2, self.X1, self.X2])
        @classmethod
        def from_array(cls, y): return cls(S1=y[0], S2=y[1], X1=y[2], X2=y[3])


# ═════════════════════════════════════════════════════════════════════════════
# Configuration
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class EnKFConfig:
    """
    Configuration for the AD4 Ensemble Kalman Filter.

    Tuning guide
    ------------
    n_ensemble: 50 is fast; 200 is more accurate. 100 is a good default
                for daily operation on an M4 Mac mini.

    process_noise_*: How much you trust the AD4 ODE model.
        Larger = ODE is uncertain (model error is large).
        Farm digesters have significant unmodelled dynamics (mixing,
        dead zones, feedstock variation) so these should be non-trivial.

    obs_noise_Q_CH4: How much you trust your methane flow meter.
        Typical farm-scale biogas meters: ±5-10% accuracy.
        At Q_CH4 ≈ 175 mL/L/d, noise_std ≈ 15-20 mL/L/d.

    obs_noise_S2_proxy: How much you trust FOS/TAC → S2 conversion.
        The conversion S2 ≈ FOS/60 is approximate; set this generously.
        Typical: 10-20 mmol/L.

    theta_arrhenius: Temperature sensitivity coefficient.
        1.035 is standard for mesophilic methanogens (Rittmann & McCarty).
        Range: 1.02-1.08 depending on population.

    T_ref_celsius: Reference temperature for mu2_max calibration.
        Use the temperature at which your AD4Params were derived (35°C).
    """
    n_ensemble:          int   = 100
    T_ref_celsius:       float = 35.0
    theta_arrhenius:     float = 1.035

    # Process noise (std dev of noise added per day to each state)
    process_noise_S1:    float = 1.0    # g COD/L/d — feedstock variation
    process_noise_S2:    float = 2.0    # mmol/L/d — VFA dynamics uncertainty
    process_noise_X1:    float = 0.05  # g VSS/L/d — slow biomass change
    process_noise_X2:    float = 0.05  # g VSS/L/d — slow biomass change

    # Observation noise (std dev of measurement error)
    obs_noise_Q_CH4:     float = 20.0  # mL/L/d — ~10% of typical Q_CH4
    obs_noise_S2_proxy:  float = 15.0  # mmol/L — FOS/TAC conversion error

    # Initial state uncertainty (std dev of initial ensemble spread)
    init_std_S1:         float = 5.0   # g COD/L
    init_std_S2:         float = 15.0  # mmol/L
    init_std_X1:         float = 0.3   # g VSS/L
    init_std_X2:         float = 0.5   # g VSS/L

    # Physical bounds for ensemble clipping (states must be non-negative)
    S2_souring_threshold:  float = 80.0   # mmol/L — watch zone
    X2_washout_threshold:  float = 0.10   # g VSS/L — critical


# ═════════════════════════════════════════════════════════════════════════════
# Observation container
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class Observation:
    """
    One time step of measurements fed to the EnKF.

    Q_CH4_mL_per_L is required. All others are optional.
    When a value is None, that observation is skipped for that time step.

    Unit conversion helpers are provided for common SCADA formats.
    """
    Q_CH4_mL_per_L:  float               # Methane flow (required)
    T_celsius:       float = 35.0        # Digester temperature
    S2_proxy_mmol_l: Optional[float] = None  # FOS/TAC → S2 (optional, weekly)

    @classmethod
    def from_scada(cls,
                   biogas_flow_nm3h: float,
                   ch4_pct: float,
                   digester_volume_m3: float,
                   digester_temp_c: float = 35.0,
                   fos_mg_per_l: Optional[float] = None) -> "Observation":
        """
        Convert raw SCADA plant_state values to an Observation.

        Args:
            biogas_flow_nm3h:    Biogas flow (Nm³/h) from plant_state
            ch4_pct:             CH4 percentage (%) from plant_state
            digester_volume_m3:  Digester volume (m³) — fixed plant parameter
            digester_temp_c:     Digester temperature from plant_state
            fos_mg_per_l:        FOS (volatile acids) in mg/L if available

        Returns:
            Observation ready for EnKF update
        """
        # Nm³/h → Nm³/d → m³ CH4/d → L CH4/d → mL CH4/L reactor/d
        Q_nm3_per_day   = biogas_flow_nm3h * 24.0 * (ch4_pct / 100.0)
        Q_mL_per_L      = Q_nm3_per_day * 1e6 / (digester_volume_m3 * 1000.0)

        # FOS/TAC → S2 proxy (acetic acid dominant assumption, MW=60 g/mol)
        S2_proxy = fos_mg_per_l / 60.0 if fos_mg_per_l is not None else None

        return cls(
            Q_CH4_mL_per_L  = Q_mL_per_L,
            T_celsius       = digester_temp_c,
            S2_proxy_mmol_l = S2_proxy,
        )


# ═════════════════════════════════════════════════════════════════════════════
# EnKF state estimate (output at each time step)
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class EnKFEstimate:
    """
    Posterior state estimate from the EnKF at one time step.

    All _mean and _std values are derived from the ensemble distribution.
    Probabilities are Monte Carlo estimates from the ensemble.
    """
    day:             int

    # Posterior means (best estimates of hidden states)
    S1_mean:         float   # g COD/L
    S2_mean:         float   # mmol/L — KEY indicator
    X1_mean:         float   # g VSS/L
    X2_mean:         float   # g VSS/L — KEY stability indicator
    Q_CH4_predicted: float   # mL/L/d — model prediction before update

    # Posterior standard deviations (uncertainty)
    S1_std:          float
    S2_std:          float
    X1_std:          float
    X2_std:          float

    # Derived risk probabilities (from ensemble fraction)
    souring_probability:  float  # P(S2 > config.S2_souring_threshold)
    washout_probability:  float  # P(X2 < config.X2_washout_threshold)

    # Innovation (observation minus prediction) — useful for diagnostics
    innovation_Q_CH4: float   # how surprised the filter was by Q_CH4

    # Temperature used for Arrhenius correction
    T_celsius:       float
    mu2_max_effective: float  # temperature-corrected mu2_max

    def S2_interval_95(self) -> tuple:
        """95% confidence interval for S2."""
        return (
            max(0.0, self.S2_mean - 1.96 * self.S2_std),
            self.S2_mean + 1.96 * self.S2_std,
        )

    def X2_interval_95(self) -> tuple:
        """95% confidence interval for X2."""
        return (
            max(0.0, self.X2_mean - 1.96 * self.X2_std),
            self.X2_mean + 1.96 * self.X2_std,
        )

    def risk_level(self) -> str:
        """
        Integrated risk assessment from both S2 and X2.

        Thresholds (aligned with doc4 Table 10 and doc5 FAQ):
          CRITICAL : washout_probability > 0.20
          WARNING  : souring_probability > 0.30  OR  S2_mean > 80 mmol/L
          WATCH    : souring_probability > 0.10
          HEALTHY  : all below thresholds
        """
        if self.washout_probability > 0.20:
            return "CRITICAL — high washout probability"
        if self.souring_probability > 0.30 or self.S2_mean > 80.0:
            return "WARNING — S2 likely in danger zone"
        if self.souring_probability > 0.10:
            return "WATCH — S2 elevated, monitor closely"
        return "HEALTHY"

    def to_dict(self) -> dict:
        s2_lo, s2_hi = self.S2_interval_95()
        x2_lo, x2_hi = self.X2_interval_95()
        return {
            "day":                  self.day,
            "S2_mean_mmol_per_L":   round(self.S2_mean, 2),
            "S2_std_mmol_per_L":    round(self.S2_std, 2),
            "S2_95pct_interval":    [round(s2_lo, 2), round(s2_hi, 2)],
            "X2_mean_g_per_L":      round(self.X2_mean, 4),
            "X2_std_g_per_L":       round(self.X2_std, 4),
            "X2_95pct_interval":    [round(x2_lo, 4), round(x2_hi, 4)],
            "S1_mean_g_per_L":      round(self.S1_mean, 3),
            "X1_mean_g_per_L":      round(self.X1_mean, 3),
            "Q_CH4_predicted":      round(self.Q_CH4_predicted, 1),
            "souring_probability":  round(self.souring_probability, 3),
            "washout_probability":  round(self.washout_probability, 4),
            "risk_level":           self.risk_level(),
            "mu2_max_effective":    round(self.mu2_max_effective, 4),
            "T_celsius":            round(self.T_celsius, 1),
            "innovation_Q_CH4":     round(self.innovation_Q_CH4, 2),
            "calibration_note": (
                "S2 and X2 are ESTIMATED, not measured. "
                "Uncertainty reflects unobservable internal states. "
                "Ki2=256 (Benyahia default) — souring threshold unvalidated."
            ),
        }


# ═════════════════════════════════════════════════════════════════════════════
# Core EnKF implementation
# ═════════════════════════════════════════════════════════════════════════════

class AD4EnKF:
    """
    Ensemble Kalman Filter wrapping the AD4 ODE process model.

    Algorithm (one time step):
    ─────────────────────────
    1. FORECAST: propagate each ensemble member forward one day
       using the AD4 ODE with temperature-corrected mu2_max.
       Add process noise to represent model uncertainty.

    2. PREDICT OBSERVATION: compute predicted Q_CH4 for each member
       using H(x) = k6 * mu2(S2) * X2.

    3. UPDATE (Kalman gain): compute ensemble covariances, apply
       Kalman gain to pull each member toward the actual observation.
       Perturb observations with measurement noise (Burgers 1998).

    4. CLIP: enforce physical bounds (all states ≥ 0).

    5. REPORT: compute posterior statistics and risk probabilities.

    State vector: x = [S1, S2, X1, X2]  (n_ensemble × 4 matrix)
    """

    def __init__(self,
                 params: Optional[AD4Params] = None,
                 config: Optional[EnKFConfig] = None,
                 initial_state: Optional[AD4State] = None,
                 D: float = 1.0 / 22.0,
                 S1_in: float = 25.0):
        """
        Initialise the EnKF.

        Args:
            params:        AD4Params kinetic parameters (Benyahia defaults if None)
            config:        EnKFConfig tuning parameters
            initial_state: Best guess at starting state (AD4State defaults if None)
            D:             Dilution rate d⁻¹ — set from plant HRT
            S1_in:         Influent COD g/L — set from feedstock records
        """
        self.params  = params  or AD4Params()
        self.config  = config  or EnKFConfig()
        self.D       = D
        self.S1_in   = S1_in
        self._day    = 0
        self._history: List[EnKFEstimate] = []

        # Initialise ensemble around initial_state with Gaussian spread
        s0 = (initial_state or AD4State()).to_array()
        cfg = self.config
        stds = np.array([cfg.init_std_S1, cfg.init_std_S2,
                         cfg.init_std_X1, cfg.init_std_X2])

        rng = np.random.default_rng(seed=42)
        noise = rng.normal(0, 1, (cfg.n_ensemble, 4)) * stds
        self._ensemble = np.clip(s0 + noise, 0.0, None)  # (N, 4), all ≥ 0
        self._rng = rng

    # ── Temperature correction ────────────────────────────────────────────────

    def _mu2_max_at_T(self, T: float) -> float:
        """Arrhenius-corrected mu2_max at temperature T (°C)."""
        return self.params.mu2_max * (
            self.config.theta_arrhenius ** (T - self.config.T_ref_celsius)
        )

    # ── AD4 ODE for a single ensemble member ─────────────────────────────────

    def _odes(self, t: float, y: np.ndarray,
              D: float, S1_in: float, mu2_max_eff: float) -> list:
        """AD4 ODE with externally supplied mu2_max (for temperature correction)."""
        S1, S2, X1, X2 = [max(v, 0.0) for v in y]
        p = self.params

        mu1 = p.mu1_max * S1 / (p.Ks1 + S1)
        mu2 = mu2_max_eff * S2 / (p.Ks2 + S2 + S2**2 / p.Ki2)

        dS1 = D * (S1_in - S1) - p.k1 * mu1 * X1
        dS2 = D * (0.0 - S2)   + p.k2 * mu1 * X1 - p.k3 * mu2 * X2
        dX1 = (mu1 - p.alpha * D) * X1
        dX2 = (mu2 - p.alpha * D) * X2
        return [dS1, dS2, dX1, dX2]

    def _propagate_one(self, y0: np.ndarray,
                       mu2_max_eff: float) -> np.ndarray:
        """Integrate one ensemble member forward one day."""
        sol = solve_ivp(
            fun=self._odes,
            t_span=(0.0, 1.0),
            y0=y0,
            args=(self.D, self.S1_in, mu2_max_eff),
            method="RK45",
            rtol=1e-4,
            atol=1e-6,
            dense_output=False,
        )
        return np.clip(sol.y[:, -1], 0.0, None)

    # ── Observation operator H(x) ─────────────────────────────────────────────

    def _predict_Q_CH4(self, ensemble: np.ndarray,
                       mu2_max_eff: float) -> np.ndarray:
        """
        H(x): predicted methane flow for each ensemble member.
        Q_CH4 [mL/L/d] = k6 * mu2(S2) * X2
        """
        S2 = ensemble[:, 1]
        X2 = ensemble[:, 3]
        p = self.params
        mu2 = mu2_max_eff * S2 / (p.Ks2 + S2 + S2**2 / p.Ki2)
        return p.k6 * mu2 * X2

    def _predict_S2(self, ensemble: np.ndarray) -> np.ndarray:
        """H(x) for direct S2 observation (FOS/TAC proxy)."""
        return ensemble[:, 1]

    # ── Kalman update step ────────────────────────────────────────────────────

    def _kalman_update(self,
                       ensemble: np.ndarray,
                       H_ensemble: np.ndarray,
                       obs_value: float,
                       obs_noise_std: float) -> np.ndarray:
        """
        Standard EnKF update (Burgers et al. 1998 perturbed observations).

        ensemble:      (N, 4) prior ensemble
        H_ensemble:    (N,)   predicted observations for each member
        obs_value:     scalar actual observation
        obs_noise_std: scalar observation noise std dev
        """
        N = ensemble.shape[0]

        # Perturb observations: each member gets a slightly different obs
        obs_perturbed = obs_value + self._rng.normal(0, obs_noise_std, N)

        # Ensemble means
        x_mean = ensemble.mean(axis=0)      # (4,)
        h_mean = H_ensemble.mean()          # scalar

        # Anomalies
        X_anom = ensemble  - x_mean         # (N, 4)
        H_anom = H_ensemble - h_mean        # (N,)

        # Cross-covariance P_xh and observation variance P_hh
        # Using (N-1) denominator (unbiased)
        P_xh = (X_anom.T @ H_anom) / (N - 1)   # (4,)
        P_hh = (H_anom  @ H_anom)  / (N - 1)   # scalar

        # Observation error variance
        R = obs_noise_std ** 2

        # Kalman gain K (4,)
        K = P_xh / (P_hh + R)

        # Update each member
        innovation = obs_perturbed - H_ensemble   # (N,)
        updated = ensemble + np.outer(innovation, K)   # (N, 4)

        return np.clip(updated, 0.0, None)

    # ── Main update method (call once per day) ────────────────────────────────

    def update(self, obs: Observation) -> EnKFEstimate:
        """
        Advance the filter by one time step.

        Call this once per day with the latest observations.
        Returns the posterior state estimate with uncertainty.

        Args:
            obs: Observation object with Q_CH4 (required),
                 T_celsius, and optionally S2_proxy_mmol_l.

        Returns:
            EnKFEstimate with posterior means, stds, and risk probabilities.
        """
        cfg = self.config
        N   = cfg.n_ensemble

        # Temperature-corrected mu2_max for this time step
        mu2_max_eff = self._mu2_max_at_T(obs.T_celsius)

        # ── 1. FORECAST: propagate all ensemble members one day ───────────────
        forecast = np.zeros((N, 4))
        for i in range(N):
            forecast[i] = self._propagate_one(self._ensemble[i], mu2_max_eff)

        # Add process noise
        noise_stds = np.array([cfg.process_noise_S1, cfg.process_noise_S2,
                                cfg.process_noise_X1, cfg.process_noise_X2])
        forecast += self._rng.normal(0, 1, (N, 4)) * noise_stds
        forecast  = np.clip(forecast, 0.0, None)

        # ── 2. PREDICT OBSERVATION ────────────────────────────────────────────
        H_Q = self._predict_Q_CH4(forecast, mu2_max_eff)   # (N,)
        Q_predicted_mean = float(H_Q.mean())
        innovation_Q = obs.Q_CH4_mL_per_L - Q_predicted_mean

        # ── 3. UPDATE: Q_CH4 observation (always available) ──────────────────
        updated = self._kalman_update(
            forecast, H_Q,
            obs.Q_CH4_mL_per_L,
            cfg.obs_noise_Q_CH4,
        )

        # ── 4. UPDATE: S2 proxy (FOS/TAC, if provided this time step) ────────
        if obs.S2_proxy_mmol_l is not None:
            H_S2 = self._predict_S2(updated)
            updated = self._kalman_update(
                updated, H_S2,
                obs.S2_proxy_mmol_l,
                cfg.obs_noise_S2_proxy,
            )

        # ── 5. Store updated ensemble ─────────────────────────────────────────
        self._ensemble = updated

        # ── 6. Compute posterior statistics ──────────────────────────────────
        means = updated.mean(axis=0)
        stds  = updated.std(axis=0)

        S2_col = updated[:, 1]
        X2_col = updated[:, 3]
        souring_prob = float((S2_col > cfg.S2_souring_threshold).mean())
        washout_prob = float((X2_col < cfg.X2_washout_threshold).mean())

        self._day += 1

        estimate = EnKFEstimate(
            day              = self._day,
            S1_mean          = float(means[0]),
            S2_mean          = float(means[1]),
            X1_mean          = float(means[2]),
            X2_mean          = float(means[3]),
            Q_CH4_predicted  = Q_predicted_mean,
            S1_std           = float(stds[0]),
            S2_std           = float(stds[1]),
            X1_std           = float(stds[2]),
            X2_std           = float(stds[3]),
            souring_probability  = souring_prob,
            washout_probability  = washout_prob,
            innovation_Q_CH4 = innovation_Q,
            T_celsius        = obs.T_celsius,
            mu2_max_effective = mu2_max_eff,
        )

        self._history.append(estimate)
        return estimate

    # ── Convenience methods ───────────────────────────────────────────────────

    def current_estimate(self) -> Optional[EnKFEstimate]:
        """Return the most recent posterior estimate."""
        return self._history[-1] if self._history else None

    def history(self) -> List[EnKFEstimate]:
        """Return full history of estimates."""
        return self._history

    def update_D(self, new_D: float):
        """Update dilution rate (call when HRT changes)."""
        if new_D <= 0:
            raise ValueError("D must be positive")
        self.D = new_D

    def update_S1_in(self, new_S1_in: float):
        """Update influent COD (call when feedstock changes)."""
        if new_S1_in < 0:
            raise ValueError("S1_in must be non-negative")
        self.S1_in = new_S1_in

    def souring_trend(self, window_days: int = 7) -> Optional[str]:
        """
        Detect rising S2 trend over the last N days.
        Returns: 'RISING', 'STABLE', 'FALLING', or None if insufficient history.
        """
        if len(self._history) < window_days:
            return None
        recent = [e.S2_mean for e in self._history[-window_days:]]
        slope = np.polyfit(range(len(recent)), recent, 1)[0]
        if slope > 1.0:
            return "RISING"
        if slope < -1.0:
            return "FALLING"
        return "STABLE"


# ═════════════════════════════════════════════════════════════════════════════
# MCP-ready wrapper — returns dicts suitable for tool responses
# ═════════════════════════════════════════════════════════════════════════════

class AD4EnKFServer:
    """
    Stateful EnKF instance designed for MCP server integration.

    Maintains a single filter instance across multiple tool calls.
    The filter is initialised once and updated daily as new
    plant_state values arrive.
    """

    def __init__(self,
                 digester_volume_m3: float = 2000.0,
                 params: Optional[AD4Params] = None,
                 config: Optional[EnKFConfig] = None):
        self.digester_volume_m3 = digester_volume_m3
        self._params = params or AD4Params()
        self._config = config or EnKFConfig()
        self._filter: Optional[AD4EnKF] = None
        self._initialised = False

    def initialise(self,
                   hrt_days: float,
                   S1_in: float = 25.0,
                   initial_state: Optional[AD4State] = None) -> dict:
        """
        Initialise the filter. Call once at startup with plant parameters.

        Args:
            hrt_days:      Current HRT from plant_state (days)
            S1_in:         Estimated influent COD (g/L)
            initial_state: Best guess at current digester state
        """
        D = 1.0 / hrt_days
        self._filter = AD4EnKF(
            params=self._params,
            config=self._config,
            initial_state=initial_state,
            D=D,
            S1_in=S1_in,
        )
        self._initialised = True
        return {
            "status":          "INITIALISED",
            "HRT_days":        hrt_days,
            "D_per_d":         round(D, 5),
            "S1_in_g_per_L":   S1_in,
            "n_ensemble":      self._config.n_ensemble,
            "T_ref_celsius":   self._config.T_ref_celsius,
            "message": (
                "EnKF initialised. Call enkf_update() daily with "
                "plant_state measurements to begin state estimation."
            ),
        }

    def step(self,
             biogas_flow_nm3h: float,
             ch4_pct: float,
             digester_temp_c: float = 35.0,
             fos_mg_per_l: Optional[float] = None,
             new_hrt_days: Optional[float] = None,
             new_S1_in: Optional[float] = None) -> dict:
        """
        Advance the filter by one day. Call once per day with SCADA values.

        Args:
            biogas_flow_nm3h: From plant_state
            ch4_pct:          From plant_state
            digester_temp_c:  From plant_state
            fos_mg_per_l:     FOS measurement if available this day (weekly)
            new_hrt_days:     Update HRT if it changed
            new_S1_in:        Update influent COD if feedstock changed

        Returns:
            EnKF posterior estimate as dict with risk assessment.
        """
        if not self._initialised or self._filter is None:
            return {
                "error": "EnKF not initialised. Call enkf_initialise() first.",
            }

        # Update operating conditions if they changed
        if new_hrt_days is not None:
            self._filter.update_D(1.0 / new_hrt_days)
        if new_S1_in is not None:
            self._filter.update_S1_in(new_S1_in)

        # Build observation from SCADA values
        obs = Observation.from_scada(
            biogas_flow_nm3h  = biogas_flow_nm3h,
            ch4_pct           = ch4_pct,
            digester_volume_m3= self.digester_volume_m3,
            digester_temp_c   = digester_temp_c,
            fos_mg_per_l      = fos_mg_per_l,
        )

        estimate = self._filter.update(obs)
        result   = estimate.to_dict()

        # Add trend information
        trend = self._filter.souring_trend(window_days=7)
        result["S2_trend_7d"] = trend

        # Operational guidance for LLM
        result["guidance"] = self._guidance(estimate, trend)

        return result

    def _guidance(self, est: EnKFEstimate, trend: Optional[str]) -> str:
        """Generate plain-language guidance for the LLM to pass to the operator."""
        parts = []
        risk = est.risk_level()

        if "CRITICAL" in risk:
            parts.append(
                f"URGENT: Estimated X2={est.X2_mean:.2f} g/L with "
                f"{est.washout_probability*100:.0f}% washout probability. "
                "Reduce feeding rate immediately."
            )
        elif "WARNING" in risk:
            parts.append(
                f"WARNING: Estimated S2={est.S2_mean:.1f} mmol/L "
                f"(95% CI: {est.S2_interval_95()[0]:.0f}–{est.S2_interval_95()[1]:.0f}). "
                "Monitor closely, consider reducing OLR."
            )
        elif "WATCH" in risk:
            parts.append(
                f"WATCH: S2 elevated ({est.S2_mean:.1f} mmol/L estimated). "
                "No immediate action needed but monitor trend."
            )
        else:
            parts.append(
                f"Digester appears healthy: S2≈{est.S2_mean:.1f} mmol/L, "
                f"X2≈{est.X2_mean:.2f} g/L (estimated)."
            )

        if trend == "RISING":
            parts.append(
                "S2 trend is RISING over the last 7 days — "
                "early warning of possible overloading."
            )
        elif trend == "FALLING":
            parts.append("S2 trend is FALLING — digester recovering.")

        temp_note = ""
        if est.T_celsius < 33.0:
            temp_note = (
                f"Temperature {est.T_celsius:.1f}°C is low for mesophilic. "
                f"mu2_max reduced to {est.mu2_max_effective:.3f} d⁻¹ "
                "(Arrhenius correction). D_crit is lower than usual."
            )
        elif est.T_celsius > 40.0:
            temp_note = (
                f"Temperature {est.T_celsius:.1f}°C is high — "
                "check for thermophilic transition risk."
            )
        if temp_note:
            parts.append(temp_note)

        parts.append(
            "NOTE: S2 and X2 are model estimates, not direct measurements. "
            f"Uncertainty: S2 ±{est.S2_std:.1f}, X2 ±{est.X2_std:.3f}."
        )

        return " | ".join(parts)

    def summary(self) -> dict:
        """Return a summary of filter health and history length."""
        if not self._initialised or self._filter is None:
            return {"status": "NOT_INITIALISED"}
        est = self._filter.current_estimate()
        return {
            "status":        "RUNNING",
            "days_tracked":  self._filter._day,
            "current_risk":  est.risk_level() if est else "UNKNOWN",
            "S2_trend_7d":   self._filter.souring_trend(),
            "n_ensemble":    self._config.n_ensemble,
        }


# ═════════════════════════════════════════════════════════════════════════════
# Self-test
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("AD4 EnKF Self-Test")
    print("Simulating 60 days of farm-scale digester operation")
    print("=" * 60)

    # Simulate a real plant using AD4 as ground truth,
    # then pretend we only observe Q_CH4 and T
    try:
        from ad4_simulator import AD4Simulator
        sim_available = True
    except ImportError:
        sim_available = False
        print("[ad4_simulator not found — using synthetic observations]")

    # Set up filter
    server = AD4EnKFServer(digester_volume_m3=2000.0)
    server.initialise(hrt_days=22.0, S1_in=25.0)

    # Synthetic observations: healthy baseline for 40 days,
    # then overload spike for 10 days
    rng = np.random.default_rng(123)

    print("\nDay | S2_est | S2_std | X2_est | Souring% | Risk")
    print("-" * 65)

    for day in range(1, 61):
        # Simulate overload spike days 41-50
        if 41 <= day <= 50:
            Q_base = 120.0   # reduced methane during overload
            fos = 4200.0 if day % 7 == 0 else None  # weekly FOS
        else:
            Q_base = 175.0
            fos = 600.0 if day % 7 == 0 else None

        # Add realistic sensor noise
        Q_obs = Q_base + rng.normal(0, 15)   # mL/L/d
        T_obs = 36.5 + rng.normal(0, 0.5)

        # Convert Q_obs [mL/L/d] → biogas_flow_nm3h for from_scada()
        # Inverse of: Q = flow_nm3h * 24 * ch4_frac * 1e6 / (V_m3 * 1000)
        ch4_frac = 0.62
        biogas_nm3h = max(0.0, Q_obs * 2000.0 * 1000.0 / (1e6 * 24.0 * ch4_frac))

        result = server.step(
            biogas_flow_nm3h = biogas_nm3h,
            ch4_pct          = 62.0,
            digester_temp_c  = T_obs,
            fos_mg_per_l     = fos,
        )

        if day % 5 == 0 or day in (41, 50, 60):
            print(
                f"{day:3d} | "
                f"{result['S2_mean_mmol_per_L']:6.1f} | "
                f"{result['S2_std_mmol_per_L']:6.1f} | "
                f"{result['X2_mean_g_per_L']:6.3f} | "
                f"{result['souring_probability']*100:8.1f}% | "
                f"{result['risk_level']}"
            )

    print("\n--- Final estimate ---")
    final = server._filter.current_estimate()
    print(json.dumps(final.to_dict(), indent=2))

    print("\n--- Filter summary ---")
    print(json.dumps(server.summary(), indent=2))
