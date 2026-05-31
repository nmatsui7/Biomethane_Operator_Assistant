# AD4 Diagnostic Lookup Tables
## Explicit failure modes and corrective actions for Gemma 4 4B
## Format: observation → diagnosis → recommended action

---

## TABLE 1: S2 (VFA) Diagnostic — Primary Control Variable

| S2 Value (mmol/L) | Status | Diagnosis | Recommended Action |
|-------------------|--------|-----------|-------------------|
| < 30 | HEALTHY | Normal operation, methanogens active | No action needed |
| 30 – 80 | WATCH | Elevated — check if trending up | Review OLR, check temperature; monitor trend daily |
| 80 – 150 | WARNING | Significant VFA accumulation — Haldane inhibition beginning | Reduce feeding rate by 20%; increase monitoring frequency |
| >= 150 | CRITICAL | Active souring — positive feedback loop in progress | Emergency: reduce D or S1_in immediately; risk of irreversible washout |

**Key rule:** If S2 is rising consistently over multiple measurement periods, act before it reaches 100 mmol/L. The souring spiral is much easier to prevent than reverse.

---

## TABLE 2: X2 (Methanogen) Biomass Diagnostic

| X2 Value (g VSS/L) | Status | Diagnosis | Recommended Action |
|--------------------|--------|-----------|-------------------|
| > 2.0 | ROBUST | Healthy methanogen population | No action |
| 1.0 – 2.0 | NORMAL | Adequate population for stable operation | No action |
| 0.1 – 1.0 | LOW | Methanogens under stress or declining | Investigate cause (VFA, ammonia, temperature) |
| 0.05 – 0.1 | NEAR_WASHOUT | Population nearly eliminated | Reduce D to minimum, consider re-inoculation |
| <= 0.05 | WASHOUT | Methanogens washed out — digester failed | Stop feeding; full re-inoculation required |

**Note:** X2 decline is often a lagging indicator — by the time X2 drops noticeably, S2 has already been elevated for some time. X2 < 0.5 g/L combined with rising S2 is a very serious signal.

---

## TABLE 3: Methane Production Rate Diagnostic

| CH4 Rate (mL/L/d) | Status | Diagnosis | Note |
|-------------------|--------|-----------|------|
| > 300 | GOOD | Healthy production | Normal for manure-based digester |
| 150 – 300 | MODERATE | Acceptable but check OLR | May indicate suboptimal loading |
| 50 – 150 | LOW | Underperformance — check X2 and S2 | Possible inhibition or low loading |
| <= 50 | VERY_LOW | Severe underperformance or failure | X2 declining or VFA inhibited; methane near zero |

**Methane is a lagging indicator.** A drop in methane production means the system has already been stressed for days or weeks. Do not use methane flow alone as the primary monitoring variable.

---

## TABLE 4: Dilution Rate (D) and HRT — Operating Guidance

| D (d⁻¹) | HRT (days) | Status | Notes |
|---------|-----------|--------|-------|
| 0.02 – 0.04 | 25 – 50 | CONSERVATIVE | Very stable, low throughput |
| 0.04 – 0.07 | 14 – 25 | OPTIMAL | Standard mesophilic operation |
| 0.07 – 0.12 | 8 – 14 | HIGH | Acceptable if OLR is moderate |
| 0.12 – 0.25 | 4 – 8 | VERY HIGH | Risk of washout with high-OLR feed |
| 0.25 – 0.37 | 2.7 – 4 | CRITICAL | Approaching theoretical D_crit |
| > 0.37 | < 2.7 | WASHOUT | Exceeds Haldane peak — methanogens cannot survive |

**For default parameters (Benyahia 2012):**
- D_crit ≈ 0.37 d⁻¹ (HRT ≈ 2.7 days)
- Safe operating maximum: D < 0.15 d⁻¹ (HRT > 7 days)
- Recommended for manure digesters: D = 0.04–0.05 d⁻¹ (HRT = 20–25 days)

---

## TABLE 5: Influent COD (S1_in) — Overloading Reference

| S1_in (g COD/L) | OLR Context | Expected S2 at Steady State | Risk |
|-----------------|-------------|----------------------------|------|
| 5 – 15 | Low loading | < 10 mmol/L | None |
| 15 – 30 | Normal manure | 5 – 20 mmol/L | Low |
| 30 – 50 | High loading | 20 – 60 mmol/L | Moderate — monitor |
| 50 – 70 | Very high | 50 – 120 mmol/L | High — check D first |
| > 70 | Extreme | Likely souring | Reduce immediately or lower D |

**Note:** High S1_in is safer at low D (long HRT). Danger zone is high S1_in AND high D simultaneously.

---

## TABLE 5b: FOS/TAC — VFA to Alkalinity Ratio Monitoring

Use the `get_vfa_alkalinity_ratio()` tool to calculate the FOS/TAC ratio:

| fos_tac_ratio | Status | Diagnosis | Recommended Action |
|---------------|--------|-----------|-------------------|
| < 0.1 | LOW | Process stable but possibly underloaded | No action needed — normal for high-loaded plants |
| 0.1 – 0.3 | OPTIMAL | Healthy buffering capacity | Maintain current operation |
| 0.3 – 0.5 | HIGH | Elevated — monitor closely | Check feeding rate, temperature |
| > 0.5 | CRITICAL | High acidification risk | Immediate action required |

**Formula:** `fos_tac = (VFA_mmol_L * 50) / Alkalinity_mg_CaCO3_L`

**Example:**
- VFA = 8.5 mmol/L, Alkalinity = 2800 mg/L CaCO3
- fos_tac = (8.5 × 50) / 2800 = 0.152 → OPTIMAL

**Key insight:** The FOS/TAC ratio is the most practical farm-scale indicator of digester health. Traditional lab testing measures FOS (volatile acids) and TAC (total alkalinity) separately, but this tool computes the ratio directly from plant sensors.

---

## TABLE 6: Simulation Failure Modes and Debugging

| Symptom in Simulation | Likely Parameter Issue | Debug Step |
|-----------------------|----------------------|------------|
| Washout at low D (< 0.05 d⁻¹) | Ki2 too low, or mu2_max too low | Check Ki2 and mu2_max values |
| S2 never rises above 5 mmol/L even at high loading | k2 too low (VFA production too slow) | Verify k2 = 116.5 |
| Methane extremely high (> 2000 mL/L/d) | k6 too high | Verify k6 = 453.0 |
| solver_ok = False | Numerical instability | Increase rtol/atol, or check for negative state values |
| X1 → 0 at D = 0.05 | alpha parameter error | Confirm alpha = 1.0 for CSTR |
| S2 rises then falls then rises (oscillation) | HRT near stability boundary | Increase simulation days; may be transient |
| S1 = S1_in at steady state | X1 washed out (very rare) | D may be too high for acidogens |

---

## TABLE 7: Comparison with Plant State Sensor Values

When the MCP server returns plant_state data, map to simulator variables as follows:

| Plant State Variable | Simulator Variable | Mapping Notes |
|---------------------|-------------------|---------------|
| `vfa_mmol_l` | S2 (steady state) | Direct — compare with simulation S2 output |
| `hydraulic_retention_days` | 1/D | D = 1 / hydraulic_retention_days |
| `organic_load_kg_vs_d` | Related to S1_in | OLR = S1_in * D * (VS/COD ratio) |
| `digester_temp_c` | Arrhenius correction | **Auto-applied** to mu2_max in all simulator and EnKF tools |
| `biogas_flow_nm3h` | Q_CH4 (EnKF input) | Used by `enkf_update()` automatically from plant_state |
| `ch4_pct` | Q_CH4 scaling | Combined with biogas_flow_nm3h for EnKF observation |

**FOS/TAC → S2 conversion (if farm measures FOS/TAC weekly):**
```
S2_mmol_per_L ≈ FOS_mg_per_L / 60.0
```
- Assumes acetic acid dominant (MW = 60 g/mol) — approximate but useful
- FOS = 600 mg/L → S2 ≈ 10 mmol/L (healthy)
- FOS = 3000 mg/L → S2 ≈ 50 mmol/L (watch zone)
- FOS = 9000 mg/L → S2 ≈ 150 mmol/L (critical)
- Pass to EnKF via: `enkf_update(fos_mg_per_l=<value>)` — dramatically tightens S2 estimate

**VFA cross-check:** If plant sensor shows `vfa_mmol_l` = 45 and simulation steady-state S2 = 45 mmol/L at the same D and S1_in, the model is well-calibrated. Large discrepancies suggest parameter recalibration is needed.

---

## TABLE 8: Recovery Procedures After Disturbance

| Failure Type | Recovery Action | Simulation to Run | Expected Recovery Time |
|--------------|----------------|-------------------|----------------------|
| Mild souring (S2 80–120) | Reduce S1_in by 30% | run_perturbation with lower S1_in | 5–15 days |
| Moderate souring (S2 120–150) | Reduce D by 30%, reduce S1_in by 50% | run_perturbation with both changes | 15–30 days |
| Severe souring (S2 > 150) | Halt feeding for 5–10 days, then restart at low D | Schedule: D_low for 30d, then ramp | 30–60 days |
| Near-washout (X2 < 0.1) | Halt feeding immediately, inoculate | Cannot fully simulate inoculation | 20–45 days |
| Full washout | Complete restart | run from AD4State with fresh X2 | 30–60 days |

---

## QUICK REFERENCE: What to Tell the LLM

When answering questions about the AD4 simulator, always:

1. **State the current S2 level first** — it is the most important health indicator.
2. **State the current D and compare to D_crit at current temperature** — is the digester in a safe operating region?
3. **Check if washout or souring flags are True** — these are binary failure indicators.
4. **Use the methane flow as a performance metric, not a health metric** — it is a lagging indicator.
5. **Any increase in S2 combined with decrease in X2 is a serious warning** — act before thresholds are exceeded.
6. **Always note the temperature** — D_crit at 31°C is ~14% lower than at 37°C. Seasonal cooling shrinks the safe envelope.

---

## TABLE 9: Temperature Effect on D_crit (Arrhenius, theta=1.035)

Use this table to answer seasonal questions without running a simulation.

| Digester Temp (°C) | mu2_max_eff (d⁻¹) | D_crit (d⁻¹) | HRT_crit (days) | Change vs 35°C |
|-------------------|-------------------|--------------|-----------------|----------------|
| 28 | 0.572 | 0.411 | 2.4 | −23.3% |
| 30 | 0.608 | 0.437 | 2.3 | −18.4% |
| 32 | 0.667 | 0.480 | 2.1 | −10.4% |
| 35 | 0.740 | 0.533 | 1.9 | reference |
| 37 | 0.821 | 0.591 | 1.7 | +10.9% |
| 40 | 0.896 | 0.645 | 1.6 | +21.0% |

**Practical rule:** Every 3°C drop in digester temperature reduces D_crit by ~10%. If your plant typically runs at HRT=22d (D=0.045) and the digester cools from 37°C to 31°C in winter, your safety margin drops from ~87% to ~79% — still safe, but worth knowing.

---

## TABLE 10: EnKF Output Interpretation

The EnKF (`enkf_update`, `enkf_status`) estimates hidden states. All values are model estimates, not direct measurements.

| Field | Good value | Watch | Action needed |
|-------|-----------|-------|---------------|
| `S2_mean_mmol_per_L` | < 30 | 30–80 | > 80 |
| `S2_std_mmol_per_L` | < 10 (tight) | 10–25 | > 25 (filter uncertain) |
| `X2_mean_g_per_L` | > 1.0 | 0.1–1.0 | < 0.1 |
| `souring_probability` | < 0.10 | 0.10–0.30 | > 0.30 |
| `washout_probability` | < 0.01 | 0.01–0.10 | > 0.10 |
| `risk_level` | HEALTHY | WATCH | WARNING / CRITICAL |
| `S2_trend_7d` | STABLE/FALLING | — | RISING |
| `innovation_Q_CH4` | near 0 | ±50 | > ±100 (sensor or model problem) |

**What high `S2_std` means:** The filter is uncertain about the VFA level. This happens when: (a) no FOS/TAC has been provided recently, (b) Q_CH4 has been stable for a long time (less information), or (c) the digester is near an unstable equilibrium. Provide `fos_mg_per_l` in the next `enkf_update()` call to tighten the estimate.

**What large `innovation_Q_CH4` means:** The model predicted a very different methane flow than was observed. Possible causes: sudden feedstock change, sensor issue, or the kinetic parameters are poorly matched to this plant. If innovation > ±150 mL/L/d for more than 3 consecutive days, consider re-running `enkf_initialise()` with updated `s1_in_g_per_l`.

---

## TABLE 11: When to Use Each Tool — Decision Guide

**USE `ad4_simulate` for:**
- "Is my current HRT safe at today's temperature?"
- "What steady state should I expect at D=0.06 and S1_in=30 g/L?"
- "How does my operating point compare to D_crit?"

**USE `ad4_critical_dilution_rate` for:**
- "What is my safety margin from washout right now?"
- "How does my safe operating envelope change in winter vs summer?"
- "Can I safely increase my feeding rate?"

**USE `ad4_perturbation_test` for:**
- "What happens if I get a high-COD delivery next week?"
- "How long will recovery take after a 7-day overload?"
- "Will my digester survive if I double the feeding rate for 10 days?"

**USE `enkf_update` + `enkf_status` for:**
- "What is my estimated VFA level right now?"
- "Is my VFA trending up or down this week?"
- "What is the probability my digester is at risk of souring?"
- Daily morning briefing: call `enkf_status()` for operator summary

**DO NOT USE simulator tools for:**
- "How much methane will I produce tomorrow?" → Use `buswell_bmp` + `olr_from_recipe`
- "What is my plant efficiency?" → Use `validate_bmp_against_scada`
- "Why did production drop last Tuesday?" → No time-series historian in this system
- Quantitative production forecasting → AD4 is calibrated for qualitative stability only

**DO NOT USE EnKF outputs as if they are measurements:**
- Always state "estimated S2" not "measured S2"
- Always report the uncertainty interval alongside the mean
- If `S2_std` > 20 mmol/L, the estimate is too uncertain to act on — request FOS/TAC

---

## PARAMETER SENSITIVITY RANKING (Most to Least Sensitive)

For calibration purposes, the parameters that most affect simulation results are:

1. **Ki2** (Haldane inhibition constant) — determines souring threshold
2. **mu2_max** (methanogen max growth rate) — determines D_crit
3. **Ks2** (methanogen half-saturation) — affects low-VFA growth efficiency
4. **k3** (VFA consumption yield) — affects VFA steady-state balance
5. **k2** (VFA production yield) — affects VFA production rate
6. **mu1_max** (acidogen max growth rate) — rarely limiting
7. **k1, Ks1** — affect substrate removal efficiency

**Calibration priority:** Measure or fit Ki2 and mu2_max first. These two parameters control whether the digester is stable or not.

---

*Generated for RAG ingestion: Gemma 4 4B / AD4 MCP biomethane assistant*
*Based on: Bernard et al. (2001), Benyahia et al. (2012)*
