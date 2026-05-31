"""
ad4_simulator.py
================
Standalone 4-state AM2 anaerobic digestion simulator.

States:
    S1  - Organic substrate (COD, g/L)
    S2  - Volatile fatty acids (mmol/L)
    X1  - Acidogenic bacteria concentration (g/L)
    X2  - Methanogenic archaea concentration (g/L)

Kinetics:
    mu1 - Monod  (X1 growth on S1)
    mu2 - Haldane (X2 growth on S2, with inhibition at high VFA)

Reference:
    Bernard et al. (2001); 4-state simplification per 2024 industrial research.
    Stoichiometric coefficients from Benyahia et al. (2012).

Usage:
    from ad4_simulator import AD4Simulator, AD4Params, AD4State

    sim = AD4Simulator()
    result = sim.run(days=30, D=0.5, S1_in=25.0)
    print(result.steady_state())
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import fsolve


# ---------------------------------------------------------------------------
# Parameter set
# ---------------------------------------------------------------------------

@dataclass
class AD4Params:
    """
    Kinetic and stoichiometric parameters for the 4-state AM2 model.

    Defaults are representative of a farm-scale manure digester operating at
    ~35°C (mesophilic). Adjust for your specific feedstock and temperature.

    Units
    -----
    mu_max  : d⁻¹
    Ks      : g COD/L  (S1) or mmol/L (S2)
    Ki2     : mmol/L
    k1–k3   : stoichiometric yield coefficients (dimensionless / mixed)
    kLa     : d⁻¹  (gas-liquid mass transfer, used for CH4 output estimate)
    alpha   : HRT multiplier for biomass washout (1.0 = no retention, <1 with settler)
    """

    # --- Acidogens (X1 / S1 → S2) ---
    mu1_max: float = 1.20       # Max growth rate, d⁻¹
    Ks1:     float = 7.10       # Half-saturation constant, g COD/L
    k1:      float = 42.14      # S1 consumption yield (g COD / g COD)
    k2:      float = 116.50     # S2 production yield  (mmol VFA / g COD)

    # --- Methanogens (X2 / S2 → CH4) ---
    mu2_max: float = 0.74       # Max growth rate, d⁻¹
    Ks2:     float = 9.28       # Half-saturation, mmol/L
    Ki2:     float = 256.0      # Inhibition constant, mmol/L
    k3:      float = 268.0      # S2 consumption yield (mmol VFA / g VSS)

    # --- Shared ---
    alpha:   float = 1.0        # Biomass washout factor (1.0 = CSTR, no retention)

    # --- Gas output (simplified) ---
    k6:      float = 453.0      # CH4 production coefficient (mL CH4 / mmol S2 consumed)

    def mu1(self, S1: float) -> float:
        """Monod growth rate for acidogens."""
        return self.mu1_max * S1 / (self.Ks1 + S1)

    def mu2(self, S2: float) -> float:
        """Haldane growth rate for methanogens (inhibited at high VFA)."""
        return self.mu2_max * S2 / (self.Ks2 + S2 + S2**2 / self.Ki2)

    def mu2_max_achievable(self) -> float:
        """Peak of the Haldane curve (occurs at S2 = sqrt(Ks2 * Ki2))."""
        S2_opt = np.sqrt(self.Ks2 * self.Ki2)
        return self.mu2(S2_opt)

    def critical_dilution_rate(self) -> float:
        """
        Approximate washout threshold for methanogens.
        Above this D the digester will fail.
        """
        return self.mu2_max_achievable()

    def mu2_max_at_temp(self,
                        T_celsius: float,
                        T_ref: float = 35.0,
                        theta: float = 1.035) -> float:
        """
        Temperature-corrected maximum methanogen growth rate (Arrhenius-type).

        Uses the modified Arrhenius equation standard for mesophilic digesters
        (Rittmann & McCarty 2001, theta=1.035 for 20–40°C range).

        Args:
            T_celsius: Actual digester temperature (°C).
            T_ref:     Reference temperature at which mu2_max was calibrated
                       (default 35°C for Benyahia params).
            theta:     Temperature sensitivity coefficient.
                       1.035 is standard for mesophilic methanogens.
                       Range: 1.02 (robust) – 1.08 (sensitive population).

        Returns:
            Corrected mu2_max (d⁻¹).

        Examples:
            At 32°C: mu2_max * 1.035^(32-35) = 0.74 * 0.901 = 0.667 d⁻¹
            At 38°C: mu2_max * 1.035^(38-35) = 0.74 * 1.109 = 0.821 d⁻¹
        """
        return self.mu2_max * (theta ** (T_celsius - T_ref))

    def mu2_at_temp(self, S2: float, T_celsius: float,
                    T_ref: float = 35.0, theta: float = 1.035) -> float:
        """
        Haldane growth rate with Arrhenius temperature correction.

        Combines mu2_max_at_temp() with the standard Haldane inhibition term.
        Use this instead of mu2() when temperature deviates from T_ref.

        Args:
            S2:        VFA concentration (mmol/L).
            T_celsius: Actual digester temperature (°C).
            T_ref:     Reference temperature (default 35°C).
            theta:     Arrhenius coefficient (default 1.035).

        Returns:
            Temperature-corrected Haldane growth rate (d⁻¹).
        """
        mu2_max_T = self.mu2_max_at_temp(T_celsius, T_ref, theta)
        return mu2_max_T * S2 / (self.Ks2 + S2 + S2**2 / self.Ki2)

    def critical_dilution_rate_at_temp(self,
                                       T_celsius: float,
                                       T_ref: float = 35.0,
                                       theta: float = 1.035) -> float:
        """
        Temperature-corrected washout threshold.

        D_crit scales directly with the Haldane peak, which scales with
        mu2_max_at_temp(). This is the operationally relevant D_crit —
        in winter when the digester cools, the safe envelope shrinks.

        Args:
            T_celsius: Actual digester temperature (°C).
            T_ref:     Reference temperature (default 35°C).
            theta:     Arrhenius coefficient (default 1.035).

        Returns:
            Temperature-corrected D_crit (d⁻¹).
        """
        mu2_max_T = self.mu2_max_at_temp(T_celsius, T_ref, theta)
        # D_crit = peak of Haldane with corrected mu2_max
        # Peak occurs at S2_opt = sqrt(Ks2 * Ki2), independent of mu2_max
        S2_opt = np.sqrt(self.Ks2 * self.Ki2)
        return mu2_max_T * S2_opt / (self.Ks2 + S2_opt + S2_opt**2 / self.Ki2)


# ---------------------------------------------------------------------------
# Initial / boundary conditions
# ---------------------------------------------------------------------------

@dataclass
class AD4State:
    """A snapshot of the digester state vector."""
    S1: float = 3.0     # g COD/L
    S2: float = 0.5     # mmol/L  (healthy: low VFA)
    X1: float = 0.5     # g VSS/L
    X2: float = 1.5     # g VSS/L

    def to_array(self) -> np.ndarray:
        return np.array([self.S1, self.S2, self.X1, self.X2])

    @classmethod
    def from_array(cls, y: np.ndarray) -> "AD4State":
        return cls(S1=y[0], S2=y[1], X1=y[2], X2=y[3])

    def is_healthy(self, vfa_threshold: float = 150.0) -> bool:
        """Rough health check: low VFA, positive biomass."""
        return (self.S2 < vfa_threshold
                and self.X1 > 0.01
                and self.X2 > 0.01)

    def __repr__(self) -> str:
        return (f"AD4State(S1={self.S1:.3f} g/L, S2={self.S2:.2f} mmol/L, "
                f"X1={self.X1:.3f} g/L, X2={self.X2:.3f} g/L)")


# ---------------------------------------------------------------------------
# Simulation result
# ---------------------------------------------------------------------------

@dataclass
class AD4Result:
    """
    Output of a simulation run.

    Attributes
    ----------
    t       : time vector (days)
    S1, S2, X1, X2 : state trajectories (same length as t)
    params  : the AD4Params used
    D       : dilution rate used (d⁻¹)
    S1_in   : influent substrate concentration (g COD/L)
    success : whether the ODE solver converged
    message : solver status message
    """
    t:       np.ndarray
    S1:      np.ndarray
    S2:      np.ndarray
    X1:      np.ndarray
    X2:      np.ndarray
    params:  AD4Params
    D:       float
    S1_in:   float
    success: bool
    message: str

    # --- Derived outputs ---

    def methane_flow(self) -> np.ndarray:
        """
        Approximate volumetric CH4 production rate (mL/L/d).
        Based on S2 consumption by X2.
        """
        mu2_vec = np.array([self.params.mu2(s2) for s2 in self.S2])
        return self.params.k6 * mu2_vec * self.X2

    def vfa_risk(self, threshold: float = 150.0) -> np.ndarray:
        """Boolean array: True where S2 exceeds souring threshold."""
        return self.S2 > threshold

    def washout_detected(self, x2_floor: float = 0.05) -> bool:
        """True if methanogen population collapsed."""
        return bool(self.X2[-1] < x2_floor)

    def souring_detected(self, threshold: float = 150.0) -> bool:
        """True if VFA exceeded threshold at any point."""
        return bool(np.any(self.S2 > threshold))

    def steady_state(self) -> AD4State:
        """Final state of the simulation (approximation of steady state)."""
        return AD4State(
            S1=float(self.S1[-1]),
            S2=float(self.S2[-1]),
            X1=float(self.X1[-1]),
            X2=float(self.X2[-1]),
        )

    def summary(self) -> dict:
        """Human-readable summary dict, suitable for MCP tool responses."""
        ss = self.steady_state()
        ch4 = self.methane_flow()
        return {
            "duration_days":        float(self.t[-1]),
            "dilution_rate_per_d":  self.D,
            "HRT_days":             round(1.0 / self.D, 2) if self.D > 0 else None,
            "influent_COD_g_per_L": self.S1_in,
            "steady_state": {
                "S1_g_per_L":    round(ss.S1, 4),
                "S2_mmol_per_L": round(ss.S2, 4),
                "X1_g_per_L":    round(ss.X1, 4),
                "X2_g_per_L":    round(ss.X2, 4),
            },
            "methane_mL_per_L_per_d": round(float(ch4[-1]), 2),
            "washout":  self.washout_detected(),
            "souring":  self.souring_detected(),
            "healthy":  ss.is_healthy(),
            "solver_ok": self.success,
        }


# ---------------------------------------------------------------------------
# Core simulator
# ---------------------------------------------------------------------------

class AD4Simulator:
    """
    4-state AM2 anaerobic digestion simulator.

    Parameters
    ----------
    params : AD4Params, optional
        Kinetic parameters. Defaults to farm-scale mesophilic manure values.

    Examples
    --------
    Basic run:
        sim = AD4Simulator()
        result = sim.run(days=60, D=0.5, S1_in=25.0)
        print(result.summary())

    Custom parameters:
        p = AD4Params(mu2_max=0.6, Ki2=200.0)
        sim = AD4Simulator(params=p)

    Scan dilution rates to find washout threshold:
        sim = AD4Simulator()
        for D in [0.3, 0.5, 0.7, 0.9]:
            r = sim.run(days=100, D=D, S1_in=25.0)
            print(D, r.summary()['washout'])
    """

    def __init__(self, params: Optional[AD4Params] = None):
        self.params = params or AD4Params()

    # ------------------------------------------------------------------
    # ODE right-hand side
    # ------------------------------------------------------------------

    def _odes(self,
              t: float,
              y: np.ndarray,
              D: float,
              S1_in: float) -> list[float]:
        """
        Continuous-stirred tank reactor (CSTR) mass balances.

        d/dt [S1]  =  D*(S1_in - S1)  -  k1*mu1*X1
        d/dt [S2]  =  D*(0    - S2)   +  k2*mu1*X1  -  k3*mu2*X2
        d/dt [X1]  =  (mu1 - alpha*D) * X1
        d/dt [X2]  =  (mu2 - alpha*D) * X2
        """
        S1, S2, X1, X2 = y

        # Clamp negatives (numerical noise near zero)
        S1 = max(S1, 0.0)
        S2 = max(S2, 0.0)
        X1 = max(X1, 0.0)
        X2 = max(X2, 0.0)

        p   = self.params
        mu1 = p.mu1(S1)
        mu2 = p.mu2(S2)

        dS1 = D * (S1_in - S1) - p.k1 * mu1 * X1
        dS2 = D * (0.0   - S2) + p.k2 * mu1 * X1 - p.k3 * mu2 * X2
        dX1 = (mu1 - p.alpha * D) * X1
        dX2 = (mu2 - p.alpha * D) * X2

        return [dS1, dS2, dX1, dX2]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self,
            days:       float = 60.0,
            D:          float = 0.5,
            S1_in:      float = 25.0,
            initial:    Optional[AD4State] = None,
            n_points:   int   = 500,
            rtol:       float = 1e-6,
            atol:       float = 1e-8) -> AD4Result:
        """
        Simulate the digester over a time horizon.

        Parameters
        ----------
        days    : simulation duration (d)
        D       : dilution rate (d⁻¹); HRT = 1/D
        S1_in   : influent organic substrate concentration (g COD/L)
        initial : starting state; defaults to AD4State() if None
        n_points: number of output time points
        rtol, atol : ODE solver tolerances

        Returns
        -------
        AD4Result
        """
        if D <= 0:
            raise ValueError("Dilution rate D must be positive.")
        if S1_in < 0:
            raise ValueError("Influent substrate S1_in must be non-negative.")

        y0 = (initial or AD4State()).to_array()
        t_span = (0.0, days)
        t_eval = np.linspace(0.0, days, n_points)

        sol = solve_ivp(
            fun=self._odes,
            t_span=t_span,
            y0=y0,
            t_eval=t_eval,
            args=(D, S1_in),
            method="RK45",
            rtol=rtol,
            atol=atol,
            dense_output=False,
        )

        if not sol.success:
            warnings.warn(f"ODE solver warning: {sol.message}")

        return AD4Result(
            t=sol.t,
            S1=sol.y[0],
            S2=sol.y[1],
            X1=sol.y[2],
            X2=sol.y[3],
            params=self.params,
            D=D,
            S1_in=S1_in,
            success=sol.success,
            message=sol.message,
        )

    def run_perturbation(self,
                         initial:   AD4State,
                         schedule:  list[dict],
                         n_points_per_segment: int = 200) -> AD4Result:
        """
        Run a multi-segment simulation where D or S1_in changes over time.

        Parameters
        ----------
        initial  : starting state
        schedule : list of dicts, each with keys:
                     'days'  - duration of this segment
                     'D'     - dilution rate for this segment
                     'S1_in' - influent COD for this segment
        n_points_per_segment : output resolution per segment

        Example
        -------
        schedule = [
            {'days': 30, 'D': 0.5,  'S1_in': 25.0},   # normal
            {'days': 10, 'D': 0.5,  'S1_in': 60.0},   # overload spike
            {'days': 30, 'D': 0.5,  'S1_in': 25.0},   # recovery
        ]
        result = sim.run_perturbation(AD4State(), schedule)
        """
        t_all, S1_all, S2_all, X1_all, X2_all = [], [], [], [], []
        t_offset = 0.0
        state = initial

        for seg in schedule:
            seg_days = seg['days']
            seg_D    = seg['D']
            seg_S1in = seg['S1_in']

            res = self.run(
                days=seg_days,
                D=seg_D,
                S1_in=seg_S1in,
                initial=state,
                n_points=n_points_per_segment,
            )

            t_all.append(res.t + t_offset)
            S1_all.append(res.S1)
            S2_all.append(res.S2)
            X1_all.append(res.X1)
            X2_all.append(res.X2)

            t_offset += seg_days
            state = res.steady_state()

        return AD4Result(
            t=np.concatenate(t_all),
            S1=np.concatenate(S1_all),
            S2=np.concatenate(S2_all),
            X1=np.concatenate(X1_all),
            X2=np.concatenate(X2_all),
            params=self.params,
            D=schedule[-1]['D'],
            S1_in=schedule[-1]['S1_in'],
            success=True,
            message="multi-segment run",
        )

    def find_steady_state(self,
                          D: float,
                          S1_in: float,
                          initial: Optional[AD4State] = None) -> AD4State:
        """
        Numerically solve for the steady-state equilibrium.

        Uses long-run simulation rather than algebraic solver,
        which is more robust across washout / souring regimes.
        """
        result = self.run(days=500, D=D, S1_in=S1_in, initial=initial,
                          n_points=1000)
        return result.steady_state()

    def scan_dilution_rates(self,
                            D_range:    tuple[float, float] = (0.1, 1.2),
                            n_steps:    int   = 24,
                            S1_in:      float = 25.0,
                            sim_days:   float = 200.0) -> list[dict]:
        """
        Sweep dilution rates and report steady-state outputs.

        Useful for generating operating envelope data for your MCP tool.

        Returns list of summary dicts, one per D value.
        """
        D_values = np.linspace(D_range[0], D_range[1], n_steps)
        results = []
        state = AD4State()   # start fresh at lowest D, carry state upward

        for D in D_values:
            res = self.run(days=sim_days, D=D, S1_in=S1_in,
                           initial=state, n_points=500)
            s = res.summary()
            s['D'] = round(float(D), 4)
            results.append(s)
            state = res.steady_state()

        return results

    def critical_D(self, S1_in: float = 25.0) -> float:
        """
        Estimate the washout dilution rate by bisection.
        Above this D the methanogens cannot sustain themselves.
        """
        D_lo, D_hi = 0.01, 3.0

        def _x2_final(D):
            res = self.run(days=300, D=D, S1_in=S1_in, n_points=300)
            return res.X2[-1]

        # Binary search
        for _ in range(30):
            D_mid = (D_lo + D_hi) / 2.0
            if _x2_final(D_mid) > 0.05:
                D_lo = D_mid
            else:
                D_hi = D_mid
            if (D_hi - D_lo) < 1e-4:
                break

        return round((D_lo + D_hi) / 2.0, 4)
