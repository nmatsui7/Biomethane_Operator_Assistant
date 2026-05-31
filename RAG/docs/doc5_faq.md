# Biomethane MCP Server — Operational FAQ
## Direct Q&A format for RAG retrieval
## Each question-answer pair is a self-contained chunk

---

## Q: What is the souring threshold for VFA and what should I do?

S2 (VFA) has four operational zones:

- **Healthy:** S2 < 30 mmol/L — no action needed
- **Watch:** S2 30–80 mmol/L — monitor trend daily; check if rising
- **Warning:** S2 80–150 mmol/L — reduce feeding rate by 20%; increase monitoring frequency
- **Critical:** S2 > 150 mmol/L — active souring; reduce D or S1_in immediately; risk of irreversible washout

The souring spiral is a positive feedback loop: high S2 suppresses methanogen growth (Haldane inhibition), which causes VFA to accumulate further, which suppresses methanogens further. Intervention is much easier before S2 exceeds 100 mmol/L. The `souring` flag in ad4_simulate becomes True when S2 exceeds 150 mmol/L at any point during the simulation.

---

## Q: What does ad4_simulate return and how do I interpret it?

`ad4_simulate` returns a dict with these key sections:

**steady_state block:**
- `S1_g_per_L` — residual substrate. High value means poor degradation.
- `S2_mmol_per_L` — VFA level. This is the primary health indicator. < 30 = healthy.
- `X1_g_per_L` — acidogen biomass. Rarely the problem unless very near zero.
- `X2_g_per_L` — methanogen biomass. < 0.05 = washout failure.

**Top-level flags:**
- `washout` — True if X2 fell below 0.05 g/L. Digester has failed.
- `souring` — True if S2 exceeded 150 mmol/L at any point.
- `healthy` — True if S2 < 150 AND X1 > 0.01 AND X2 > 0.01.
- `methane_mL_per_L_per_d` — methane production rate. Qualitative use only; not calibrated for farm-scale prediction.

**interpretation block:**
- `S2_status` — HEALTHY / WATCH / WARNING / CRITICAL based on S2 value
- `X2_status` — ROBUST / NORMAL / LOW / NEAR_WASHOUT / WASHOUT based on X2 value
- `methane_status` — GOOD / MODERATE / LOW / VERY_LOW
- `temperature_correction_applied` — True if digester_temp_c ≠ 35°C
- `mu2_max_effective` — Arrhenius-corrected mu2_max used in this run
- `kinetic_params` — reminder that Ki2 and mu2_max are Benyahia defaults, not plant-specific

**How to interpret a result:** Check `healthy` first. If False, check `washout` and `souring` flags to determine which failure mode. Then read `interpretation.S2_status` and `interpretation.X2_status` for the specific severity level.

---

## Q: How does temperature affect methanogen growth and digester safety?

Temperature directly reduces the maximum growth rate of methanogens (mu2_max) via the Arrhenius equation:

```
mu2_max_effective = mu2_max × 1.035^(T_celsius - 35)
```

**Practical effects on farm operation:**

| Digester Temp | mu2_max_effective | D_crit | Change in safe envelope |
|--------------|-------------------|--------|------------------------|
| 30°C | 0.608 d⁻¹ | 0.437 d⁻¹ | −18% vs summer |
| 32°C | 0.667 d⁻¹ | 0.480 d⁻¹ | −10% |
| 35°C | 0.740 d⁻¹ | 0.533 d⁻¹ | reference |
| 37°C | 0.821 d⁻¹ | 0.591 d⁻¹ | +11% |
| 40°C | 0.896 d⁻¹ | 0.645 d⁻¹ | +21% |

**Key rule:** Every 3°C drop in digester temperature reduces D_crit by approximately 10%. A feeding rate that is safe in summer (37°C) may be at CAUTION level in winter (31°C) without any change in operation.

All three simulator tools (`ad4_simulate`, `ad4_critical_dilution_rate`, `ad4_perturbation_test`) read `digester_temp_c` from plant_state and apply this correction automatically. The corrected value appears as `mu2_max_effective` in the tool response. The EnKF also applies Arrhenius correction on every daily update step.

---

## Q: What are the EnKF risk levels and what triggers each one?

The EnKF `risk_level` field has four values derived from `souring_probability` and `washout_probability`:

| Risk Level | Trigger condition | Recommended action |
|-----------|-------------------|-------------------|
| HEALTHY | souring_prob < 0.10 AND washout_prob < 0.01 | No action; continue monitoring |
| WATCH | souring_prob 0.10–0.30 | Monitor S2 trend; consider FOS/TAC measurement |
| WARNING | souring_prob > 0.30 OR S2_mean > 80 | Review feeding rate; reduce OLR if S2 trending up |
| CRITICAL | washout_prob > 0.20 | Reduce feeding immediately; urgent attention required |

`souring_probability` is the fraction of the 100-member ensemble with estimated S2 > 80 mmol/L. `washout_probability` is the fraction with estimated X2 < 0.10 g/L. These are model estimates — always report alongside the uncertainty interval.

`S2_trend_7d` adds directional context: RISING trend at WATCH level is more urgent than STABLE at WARNING level. If S2_trend_7d = RISING and souring_probability > 0.20, act before the next daily update.

---

## Q: What is the difference between ad4_simulate and the EnKF?

They serve different purposes and should not be confused:

**ad4_simulate (Layer 3 — deterministic simulation):**
- Runs the AD4 ODE forward in time at a fixed D and S1_in
- Answers: "What steady state will I reach at these operating conditions?"
- Output is a single deterministic prediction (no uncertainty)
- Use for: what-if planning, D_crit safety margin, overload scenario testing
- Does NOT use any plant measurements — purely physics-based

**EnKF — enkf_update (Layer 3b — state estimation):**
- Estimates the current hidden state of the actual digester
- Answers: "What is S2 probably right now, given my measured Q_CH4 and temperature?"
- Output includes uncertainty intervals and risk probabilities
- Uses daily plant_state measurements to update estimates
- Does NOT predict future states — it tracks the present

**Rule of thumb:** Use ad4_simulate before making a change ("if I increase OLR, what happens?"). Use enkf_status after something happens ("my biogas dropped — what is my estimated VFA right now?").

---

## Q: How do I calibrate k6 and what does it mean?

k6 is the methane production coefficient: mL of CH4 produced per mmol of VFA consumed by methanogens.

**Why only k6 is fitted (not Ki2 or mu2_max):**
S2 (VFA) is never directly measured at farm scale. Without S2 time-series, the Haldane kinetic parameters (Ki2, mu2_max, Ks2) cannot be uniquely identified. k6 can be fitted because it only requires observable Q_CH4 — which is measured continuously.

**How fitting works (analytical, not iterative):**
1. Run AD4 ODE at measured D and S1_in to get steady-state S2_ss and X2_ss (one ODE run)
2. Solve analytically: `k6 = Q_CH4_measured / (mu2(S2_ss) × X2_ss)`
3. Clip to physical bounds [100, 2500] mL/mmol

**What k6_std tells you:**
A high k6_std across the dataset means feedstock composition varies significantly across seasons. Low k6_std means consistent feedstock. If k6_std / k6_mean > 25%, re-fit k6 seasonally.

**What k6 does NOT tell you:**
A well-fitted k6 does not mean Ki2 and mu2_max are correct. The stability and souring-risk predictions from the simulator still depend on unvalidated Benyahia defaults for those parameters.

---

## Q: What parameters are not calibrated to my plant?

**Calibrated (have data support):**
- D — directly from HRT measurements
- k6 — fitted to observed Q_CH4 (analytically, bounded by Buswell chemistry)
- Temperature correction — directly from digester_temp_c sensor

**NOT calibrated (Benyahia 2012 literature defaults):**
- Ki2 = 256 mmol/L — Haldane inhibition constant; determines souring threshold
- mu2_max = 0.74 d⁻¹ — maximum methanogen growth rate; determines D_crit
- Ks2 = 9.28 mmol/L — methanogen half-saturation constant
- k2 = 116.5, k3 = 268.0 — VFA production/consumption yields

**Why this matters:**
If your methanogen population is less tolerant of VFA than the Benyahia lab reactor (lower Ki2), the true souring threshold is lower than 150 mmol/L. The simulator will underestimate souring risk. Weekly FOS/TAC measurements fed into the EnKF provide the best available evidence about the actual souring threshold for your plant.

---

## Q: What should I check if my biogas production drops suddenly?

Step 1 — Call `enkf_status()`. Check:
- `S2_mean` and `S2_trend_7d` — rising VFA is the most common cause
- `risk_level` — if WARNING or CRITICAL, souring is likely
- `X2_mean` — if < 0.5 g/L, methanogen biomass is declining

Step 2 — Check plant_state alerts via `check_alerts()`:
- Temperature drop? → Arrhenius effect on mu2_max; check heating system
- H2S spike? → Sulphur inhibition; check iron dosing
- pH < 6.8? → Acidification; alkalinity may be depleted (not modelled in 4-state)

Step 3 — If cause is unclear, call `ad4_simulate` at current D and S1_in:
- Compare predicted methane to observed
- Large discrepancy (> 30%) suggests either feedstock changed or model parameters need updating

Step 4 — Methane drop is a lagging indicator. If S2 is elevated and trending up, act on the S2 signal — do not wait for methane to recover on its own.

---

## Q: Is it safe to increase my feeding rate?

Call `ad4_critical_dilution_rate()`. It will:
1. Read current HRT from plant_state
2. Apply Arrhenius correction for current digester temperature
3. Return current safety margin as a percentage

**Interpret the result:**
- SAFE (> 40% margin) — increasing OLR is feasible; also run `ad4_perturbation_test` to check response
- CAUTION (20–40% margin) — possible, but run `ad4_perturbation_test` first with the proposed new S1_in
- WARNING (< 20% margin) — do not increase feeding rate; current operation is already close to washout threshold

Also check `enkf_status()` S2_trend_7d before increasing OLR. If S2 is already RISING, increasing the feeding rate will accelerate the trend toward souring regardless of what the D_crit safety margin says.

---

## Q: What does each field in the ad4_simulate output mean?

The ad4_simulate tool returns a dictionary with these key sections:

**Top-level fields:**
- `washout` (bool) — True if X2 fell below 0.05 g/L. Digester has failed.
- `souring` (bool) — True if S2 exceeded 150 mmol/L at any point.
- `healthy` (bool) — True if S2 < 150 AND X1 > 0.01 AND X2 > 0.01.
- `methane_mL_per_L_per_d` — methane production rate. Qualitative use only.
- `dilution_rate_D` — the D value used in simulation (1/HRT).

**steady_state block:**
- `S1_g_per_L` — residual substrate concentration. High value means poor degradation.
- `S2_mmol_per_L` — VFA level. This is the primary health indicator. < 30 = healthy.
- `X1_g_per_L` — acidogen biomass. Rarely the problem unless near zero.
- `X2_g_per_L` — methanogen biomass. < 0.05 = washout failure.

**interpretation block:**
- `S2_status` — HEALTHY / WATCH / WARNING / CRITICAL based on S2 value
- `X2_status` — ROBUST / NORMAL / LOW / NEAR_WASHOUT / WASHOUT
- `methane_status` — GOOD / MODERATE / LOW / VERY_LOW
- `temperature_correction_applied` — True if digester_temp_c ≠ 35°C
- `mu2_max_effective` — Arrhenius-corrected mu2_max used in this run

**How to read a result:** Check `healthy` first. If False, check `washout` and `souring` to determine failure mode. Then read `interpretation.S2_status` and `interpretation.X2_status` for severity level.

---

## Q: When is the EnKF risk_level WARNING or CRITICAL?

The EnKF risk_level field has four values determined by probability thresholds:

**HEALTHY:** souring_probability < 0.10 AND washout_probability < 0.01. No action needed.

**WATCH:** souring_probability between 0.10 and 0.30. Monitor S2 trend daily; consider getting a FOS/TAC measurement to tighten the estimate.

**WARNING:** souring_probability > 0.30 OR S2_mean > 80 mmol/L. Review feeding rate; if S2 is trending up, reduce OLR immediately.

**CRITICAL:** washout_probability > 0.20. Reduce feeding immediately; urgent attention required. Methanogen population may be collapsing.

The probabilities come from the 100-member ensemble. souring_probability is the fraction with S2 > 80 mmol/L. washout_probability is the fraction with X2 < 0.10 g/L. These are model estimates, not measurements.

---

## Q: Is it safe to increase my feeding rate?

Call the `ad4_critical_dilution_rate` tool. It reads the current HRT from plant_state, applies Arrhenius temperature correction, and returns a safety margin percentage.

**Interpret the safety margin:**
- **SAFE** (> 40% margin): Increasing OLR is feasible. Also run `ad4_perturbation_test` with the proposed new S1_in to verify stability.
- **CAUTION** (20–40% margin): Possible, but run `ad4_perturbation_test` first with the proposed new S1_in.
- **WARNING** (< 20% margin): Do not increase feeding rate. Current operation is already close to washout threshold.

Before increasing OLR, also check `enkf_status()` for S2_trend_7d. If S2 is already RISING, increasing the feeding rate will accelerate the trend toward souring regardless of what the D_crit safety margin says.

---

## Q: How do I use FOS and TAC measurements with the system?

FOS (Free Organic Acids) and TAC (Total Alkalinity) are lab measurements that provide ground truth for the EnKF.

**To use with EnKF:**
Call `enkf_update(fos_mg_per_l=YOUR_FOS_VALUE)` where YOUR_FOS_VALUE is the FOS measurement in mg/L from the lab.

**How it works:** FOS converts to S2 proxy via the formula S2 ≈ FOS / 60.0 (since acetic acid, the dominant VFA, has molecular weight ~60 g/mol).

**Why it helps:** Without FOS, the EnKF estimates S2 from biogas flow alone, which has limited observability (many S2/X2 combinations produce the same Q_CH4). Providing FOS weekly dramatically tightens the S2 estimate because it provides an actual observation of the hidden state.

**Example:** If FOS = 600 mg/L, then S2 ≈ 600/60 = 10 mmol/L (healthy). If FOS = 3000 mg/L, then S2 ≈ 50 mmol/L (in the watch zone).

The EnKF will report `fos_provided: true` in the response when you provide this measurement.

---

## Q: Why is my biomethane production lower in winter and what should I do?

In colder months, digester temperature drops below the mesophilic optimum (35°C). This reduces methanogen activity via the Arrhenius effect:

**The problem:**
- Every 1°C below 35°C reduces mu2_max by ~3.5%
- At 32°C, mu2_max drops from 0.74 to 0.67 d⁻¹ — a 10% reduction
- This lowers D_crit (critical dilution rate), so the same feeding rate becomes riskier
- Biogas production drops because methanogens can't process VFA as fast

**What to do:**
1. Check `enkf_status()` — if S2 is rising, the digester is stressed
2. Run `ad4_critical_dilution_rate()` at current temperature to see your actual safety margin
3. If margin is CAUTION or WARNING, reduce feeding rate by 10-20%
4. Check digester heating system — maintain 35°C if possible
5. The temperature correction is automatic in all simulator tools

---

## Q: My biogas production dropped 20 percent this week — what should I check first?

A sudden biogas drop usually indicates one of three problems: souring, methanogen washout, or temperature issues.

**Step 1: Check EnKF status**
```python
enkf_status()
```
Look at:
- `S2_mean` and `S2_trend_7d` — rising VFA is the most common cause
- `risk_level` — if WARNING or CRITICAL, souring is likely
- `X2_mean` — if < 0.5 g/L, methanogen population is declining

**Step 2: Check plant alerts**
```python
check_alerts()
```
- Temperature drop? → Arrhenius effect reduces mu2_max
- H2S spike? → Sulphur inhibition
- pH < 6.8? → Acidification

**Step 3: Run simulation at current conditions**
```python
ad4_simulate(D=YOUR_D, S1_in=YOUR_S1_IN)
```
Compare predicted vs observed methane. Large discrepancy (> 30%) suggests feedstock change or parameter drift.

**Remember:** Methane is a lagging indicator. If S2 is elevated, act on that signal — don't wait for methane to recover.

---

## Q: What does a souring_probability of 0.35 mean? Should I be worried?

`souring_probability` is the fraction of the 100 EnKF ensemble members that estimate S2 (VFA) above 80 mmol/L.

**Interpretation:**
- **0.35 = 35%** of ensemble members think S2 > 80 mmol/L
- This puts you in **WARNING** territory (threshold is > 0.30)
- Not an emergency yet, but you should review your feeding rate

**What to do:**
1. Check `S2_trend_7d` — if RISING, act sooner
2. Get a FOS/TAC lab measurement to confirm actual VFA
3. If S2 is trending up, reduce organic loading rate (OLR) by 15-20%
4. Increase monitoring frequency to daily

**The uncertainty matters:**
- If the 95% interval is (20, 120), the true S2 could be healthy or souring
- A FOS measurement would narrow this dramatically
- The EnKF is giving you a probability, not a certainty — use it to prioritize attention

---

## Q: When should I use ad4_simulate versus when should I use enkf_update?

**Use `ad4_simulate` for planning (before something happens):**
- "What happens if I increase my feeding rate?"
- "Is my current HRT safe at this temperature?"
- "What steady state should I expect at D=0.06 and S1_in=30?"
- "What is my D_crit safety margin?"

**Use `enkf_update` and `enkf_status` for diagnosis (after something happens):**
- "My biogas dropped — what is my VFA actually?"
- "How confident should I be in my current S2 estimate?"
- "What is my risk level right now?"

**Key difference:**
- `ad4_simulate` is physics-based, uses no plant data, answers "what if"
- `enkf_update` uses plant measurements (Q_CH4, temperature), answers "what is"

**Rule of thumb:**
- Planning a change → use simulator
- Understanding current state → use EnKF
- After a problem (biogas drop, temperature swing) → use EnKF

---

*Document: faq.md — for RAG ingestion*
*Last updated: April 2026*
*Chunk strategy: each Q&A section is one retrieval unit plus sub-chunks for specific concepts*
