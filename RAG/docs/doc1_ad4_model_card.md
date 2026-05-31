# AD4 Model Card — 4-State AM2 Anaerobic Digestion Simulator
## For RAG ingestion: Gemma 4 4B / biomethane MCP server

---

## 1. What This Model Is

The AD4 model is a **4-state simplification of the AM2 (Anaerobic Model 2)** developed by Bernard et al. (2001). It describes a continuous stirred-tank reactor (CSTR) anaerobic digester using four state variables and two microbial populations.

The 4-state model drops the Inorganic Carbon (C) and Alkalinity (Z) states from the full 6-state AM2. This is valid when:
- pH control is handled externally or assumed stable (7.0–7.4)
- The focus is on VFA accumulation (souring) and biomass washout
- A simplified control loop is sufficient (VFA-based, not pH-based)

---

## 2. The Four State Variables

| Symbol | Name | Unit | Healthy Range | Critical Threshold |
|--------|------|------|---------------|-------------------|
| S1 | Organic substrate (COD) | g COD/L | 2–8 g/L (effluent) | — |
| S2 | Volatile fatty acids (VFA) | mmol/L | < 30 mmol/L | > 150 mmol/L = souring |
| X1 | Acidogenic bacteria | g VSS/L | 0.3–1.5 g/L | < 0.01 = washout |
| X2 | Methanogenic archaea | g VSS/L | 1.0–3.0 g/L | < 0.05 = washout |

**S1 (Organic Substrate):** Raw organic matter entering the digester, measured as Chemical Oxygen Demand. Consumed by X1 (acidogens) to produce VFAs. Typical influent concentration: 15–60 g COD/L for farm-scale manure digesters.

**S2 (Volatile Fatty Acids, VFA):** Intermediate product of acidogenesis. Consumed by X2 (methanogens) to produce methane. S2 accumulation is the primary warning sign of digester stress. When S2 rises above ~150 mmol/L the Haldane inhibition term strongly suppresses methanogen growth, causing a positive feedback loop (more VFA → less methane → more VFA) that leads to digester failure if not corrected.

**X1 (Acidogens):** Bacteria that break down complex organics into VFAs and hydrogen. Grow on S1 via Monod kinetics. Faster-growing than methanogens; washout of X1 requires very high dilution rates (D > 1.0 d⁻¹ typically).

**X2 (Methanogens):** Archaea that convert VFAs to methane and CO2. Slower-growing than acidogens. The rate-limiting population. Washout of X2 causes complete digester failure. Sensitive to: high VFA (Haldane inhibition), ammonia > 3000 mg/L, temperature swings > 1°C/day.

---

## 3. Kinetic Expressions

### Acidogen growth (Monod):
```
mu1(S1) = mu1_max * S1 / (Ks1 + S1)
```
- Saturates as S1 increases. No inhibition term.
- mu1 approaches mu1_max = 1.20 d⁻¹ at high substrate concentrations.

### Methanogen growth (Haldane):
```
mu2(S2) = mu2_max * S2 / (Ks2 + S2 + S2²/Ki2)
```
- Increases with S2 up to a peak, then **decreases** at high S2 due to inhibition.
- Peak occurs at S2_opt = sqrt(Ks2 * Ki2) = sqrt(9.28 * 256) ≈ 48.7 mmol/L
- At S2 > 150 mmol/L, mu2 is severely suppressed — this is the souring danger zone.
- **The Haldane inhibition constant Ki2 = 256 mmol/L is the single most sensitive parameter for digester stability.** Lowering Ki2 shifts the souring threshold downward.

### Temperature correction (Arrhenius):
```
mu2_max_at_T = mu2_max * theta^(T - T_ref)
```
- `theta = 1.035` — standard for mesophilic methanogens (Rittmann & McCarty 2001)
- `T_ref = 35°C` — reference temperature for Benyahia parameters
- mu2_max and D_crit both scale with this correction every time temperature changes

**Temperature effect on D_crit (farm-scale seasonal reference):**

| Digester Temp | mu2_max_effective | D_crit | HRT_crit | Change vs 35°C |
|--------------|-------------------|--------|----------|----------------|
| 30°C | 0.608 d⁻¹ | 0.437 d⁻¹ | 2.3 days | −18.4% |
| 32°C | 0.667 d⁻¹ | 0.480 d⁻¹ | 2.1 days | −10.4% |
| 35°C | 0.740 d⁻¹ | 0.533 d⁻¹ | 1.9 days | reference |
| 38°C | 0.821 d⁻¹ | 0.591 d⁻¹ | 1.7 days | +10.9% |
| 40°C | 0.896 d⁻¹ | 0.645 d⁻¹ | 1.6 days | +21.0% |

**Key implication:** In winter, a digester running at T=31°C has ~14% less methanogen capacity than at summer T=37°C. The same feeding rate that is safe in summer may be at CAUTION level in winter. All three simulator MCP tools (`ad4_simulate`, `ad4_critical_dilution_rate`, `ad4_perturbation_test`) apply this correction automatically from `plant_state.digester_temp_c`.

---

## 4. Mass Balance ODEs (CSTR)

```
dS1/dt = D*(S1_in - S1) - k1*mu1(S1)*X1
dS2/dt = D*(0 - S2)     + k2*mu1(S1)*X1 - k3*mu2(S2)*X2
dX1/dt = (mu1(S1) - alpha*D) * X1
dX2/dt = (mu2(S2) - alpha*D) * X2
```

**Term-by-term explanation:**

- `D*(S1_in - S1)`: hydraulic dilution — substrate entering minus leaving
- `k1*mu1*X1`: substrate consumed by acidogen growth
- `D*(0 - S2)`: VFA washed out by hydraulic flow (no VFA in influent)
- `k2*mu1*X1`: VFA produced by acidogen metabolism
- `k3*mu2*X2`: VFA consumed by methanogen metabolism
- `(mu1 - alpha*D)*X1`: net acidogen growth minus washout; alpha=1.0 for plain CSTR
- `(mu2 - alpha*D)*X2`: net methanogen growth minus washout

**Washout condition:** X2 washes out when `mu2(S2) < alpha*D` for sustained periods. Because mu2 has a maximum (Haldane peak), there exists a **critical dilution rate D_crit** above which no stable methanogen population can exist.

---

## 5. Stoichiometric Parameters (Benyahia et al. 2012)

| Parameter | Symbol | Value | Unit | Meaning |
|-----------|--------|-------|------|---------|
| Acidogen max growth | mu1_max | 1.20 | d⁻¹ | Max specific growth rate |
| Acidogen half-sat | Ks1 | 7.10 | g COD/L | S1 at half-max mu1 |
| S1 consumption yield | k1 | 42.14 | g COD / g COD | COD consumed per unit acidogen growth |
| VFA production yield | k2 | 116.50 | mmol VFA / g COD | VFA produced per unit acidogen growth |
| Methanogen max growth | mu2_max | 0.74 | d⁻¹ | Max specific growth rate |
| Methanogen half-sat | Ks2 | 9.28 | mmol/L | S2 at half-max mu2 (no inhibition) |
| Haldane inhibition | Ki2 | 256.0 | mmol/L | S2 concentration that halves mu2_max |
| VFA consumption yield | k3 | 268.0 | mmol VFA / g VSS | VFA consumed per unit methanogen growth |
| CH4 production | k6 | 453.0 | mL CH4 / mmol S2 | Methane yield from S2 consumption |
| Washout factor | alpha | 1.0 | — | 1.0 = pure CSTR; < 1.0 = biomass retention |

---

## 6. Typical Operating Points (Mesophilic, Farm-Scale Manure)

| Operating Variable | Typical Value | Notes |
|-------------------|---------------|-------|
| Temperature | 35–38°C | Mesophilic range |
| HRT (hydraulic retention time) | 20–25 days | D = 0.04–0.05 d⁻¹ |
| Dilution rate D | 0.04–0.05 d⁻¹ | 1/HRT |
| Influent COD (S1_in) | 20–40 g/L | Manure-based feedstock |
| Steady-state S1 | 2–8 g/L | Residual substrate |
| Steady-state S2 | 5–30 mmol/L | Healthy: low VFA |
| Steady-state X1 | 0.3–1.0 g/L | Acidogen biomass |
| Steady-state X2 | 1.0–2.5 g/L | Methanogen biomass |
| Methane flow | 200–600 mL/L/d | Depends on OLR |
| Critical D (washout) | ~0.74 d⁻¹ | Theoretical max; avoid D > 0.5 d⁻¹ |

---

## 7. Failure Mode Reference Table

| Observation | Likely Cause | Simulation Signature |
|-------------|--------------|----------------------|
| S2 rising, X2 declining | Overloading or high D | S2 > 80, X2 dropping |
| S2 > 150 mmol/L | Souring in progress | Haldane inhibition feedback |
| X2 < 0.05 g/L | Methanogen washout | D exceeded D_crit |
| S1 not decreasing | X1 or X2 washout | Both biomass near zero |
| Methane drops suddenly | X2 collapse or souring | k6 * mu2 * X2 → 0 |
| S2 high but X2 still present | Early souring, recoverable | Reduce D or S1_in |
| X1 >> 0, X2 ≈ 0 | Classic washout pattern | Acidogens survive; methanogens do not |

---

## 8. MCP Tool Outputs — Field Definitions

### `ad4_simulate` / `ad4_perturbation_test`

- `dilution_rate_per_d`: D value used (d⁻¹)
- `HRT_days`: hydraulic retention time = 1/D
- `influent_COD_g_per_L`: S1_in value used
- `steady_state.S1_g_per_L`: residual substrate at end of simulation
- `steady_state.S2_mmol_per_L`: VFA at end — key health indicator
- `steady_state.X1_g_per_L`: acidogen biomass at end
- `steady_state.X2_g_per_L`: methanogen biomass at end — key stability indicator
- `methane_mL_per_L_per_d`: estimated volumetric methane production rate
- `washout`: True if X2 fell below 0.05 g/L — digester failure
- `souring`: True if S2 exceeded 150 mmol/L at any point
- `healthy`: True if S2 < 150 and X1 > 0.01 and X2 > 0.01
- `solver_ok`: True if ODE integration converged
- `interpretation.temperature_correction_applied`: True if T ≠ 35°C
- `interpretation.digester_temp_c`: temperature used for Arrhenius correction
- `interpretation.mu2_max_effective`: Arrhenius-corrected mu2_max actually used
- `interpretation.mu2_max_at_35c`: reference value (0.74 d⁻¹) for comparison

### `ad4_critical_dilution_rate`

- `D_crit_at_35c_per_d`: reference D_crit at 35°C (no correction)
- `D_crit_numerical_per_d`: bisection D_crit at actual plant temperature
- `D_crit_reduction_vs_35c_pct`: how much D_crit shrunk due to temperature
- `safety_margin_pct`: (D_crit − current_D) / D_crit × 100
- `status`: SAFE (>40%), CAUTION (20–40%), WARNING (<20%)
- `recommended_max_D_per_d`: 40% of D_crit — practical safe operating limit
- `temperature_note`: plain-language explanation of Arrhenius effect

### `enkf_update` — EnKF state estimation outputs

The EnKF estimates hidden states S2 and X2 from observable Q_CH4 and temperature. These are **model estimates with uncertainty**, not direct measurements.

- `S2_mean_mmol_per_L`: best estimate of VFA concentration
- `S2_std_mmol_per_L`: 1-sigma uncertainty on S2 — wide means filter is uncertain
- `S2_95pct_interval`: [lower, upper] — 95% confidence interval on S2
- `X2_mean_g_per_L`: best estimate of methanogen biomass
- `X2_std_g_per_L`: uncertainty on X2
- `X2_95pct_interval`: [lower, upper] — 95% confidence interval on X2
- `souring_probability`: fraction of ensemble with S2 > 80 mmol/L — P(souring risk)
- `washout_probability`: fraction of ensemble with X2 < 0.10 g/L — P(washout)
- `risk_level`: HEALTHY / WATCH / WARNING / CRITICAL — derived from both probabilities
- `S2_trend_7d`: RISING / STABLE / FALLING over last 7 days
- `mu2_max_effective`: Arrhenius-corrected mu2_max used in this update step
- `innovation_Q_CH4`: observed Q_CH4 minus predicted Q_CH4 — filter surprise signal
- `guidance`: plain-language operator guidance string for LLM to read out

### `enkf_status`

- `days_tracked`: how many daily updates the filter has processed
- `current_risk`: latest risk_level
- `S2_trend_7d`: VFA trend direction
- `operator_summary`: one-sentence summary for morning briefings

---

## 9. Key Relationships to Remember

1. **HRT and D are inverses:** HRT = 20 days → D = 0.05 d⁻¹. Increasing feed rate = increasing D = decreasing HRT.

2. **VFA (S2) is the primary control variable** in a 4-state model. Monitor S2 in effluent. Rising S2 is the earliest warning of overload or inhibition.

3. **The Haldane curve has a peak.** Methanogens grow fastest at S2 ≈ 49 mmol/L. Above that, more VFA actually *slows* their growth — the system can tip into an irreversible souring spiral.

4. **D_crit is temperature-dependent.** At 35°C (Benyahia reference) D_crit ≈ 0.53 d⁻¹. A 3°C drop to 32°C reduces D_crit by ~10% to 0.48 d⁻¹. In practice, operate at D < 0.4 × D_crit(T) where T is the current digester temperature. Use `ad4_critical_dilution_rate()` which corrects automatically.

5. **alpha < 1.0 enables higher OLR.** If X2 is retained (settler, membrane), the effective washout threshold rises. The default alpha = 1.0 assumes a plain CSTR with no biomass recycle.

---

*Reference: Bernard et al. (2001), "Dynamical model development and parameter identification for an anaerobic wastewater treatment process." Biotechnology and Bioengineering, 75(4), 424–438.*
*Parameters: Benyahia et al. (2012). Stoichiometric coefficients for farm-scale mesophilic manure digestion.*
