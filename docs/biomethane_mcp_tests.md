# Biomethane MCP Server — LLM Functional Test Suite

Each test has an `id`, `section`, `prompt`, and `expect` field used by the
test runner. Do not rename these headings — the runner parses this file.

---

## TEST: A01
**section:** Plant State & Alerts
**prompt:** What is the current plant status summary?
**expect:** Returns overall_status, key_readings including temp, pH, CH4, H2S, purity, and a one_line_summary string.

## TEST: A02
**section:** Plant State & Alerts
**prompt:** Check all parameters against their alert thresholds and tell me what is alerting right now.
**expect:** Returns overall_status of NORMAL, WARNING, or CRITICAL. Lists any alerting parameters with severity and threshold values.

## TEST: A03
**section:** Plant State & Alerts
**prompt:** Update the plant state: set h2s_ppm to 650 and biomethane_purity_pct to 96.5. Then re-run the alert check and tell me what changed.
**expect:** update_plant_state accepts both values (accepted_count=2, rejected_count=0). Subsequent check_all_alerts shows h2s_ppm as CRITICAL and biomethane_purity_pct as CRITICAL or WARNING.

## TEST: A04
**section:** Plant State & Alerts
**prompt:** What is the current VFA to alkalinity ratio and is the digester at risk of acidification?
**expect:** Returns fos_tac_ratio, status (OPTIMAL/ACCEPTABLE/WARNING/CRITICAL), and an advice string. Ratio should be in range 0.1–0.8 for simulated data.

## TEST: A05
**section:** Plant State & Alerts
**prompt:** Try to update the plant state with an unknown parameter called reactor_pressure_bar set to 1.2. What does the server say?
**expect:** accepted_count=0, rejected_count=1. The rejection reason mentions "Unknown parameter" or "not in writable set".

## TEST: A06
**section:** Plant State & Alerts
**prompt:** Try to set digester_temp_c to the string value "hot". Call the update_plant_state tool with this invalid value and report what the server returns.
**expect:** rejected_count=1. Rejection reason mentions invalid type or must be numeric.

## TEST: A07
**section:** Plant State & Alerts
**prompt:** Show me the last 10 alerts from the alert history log.
**expect:** Returns total_alerts_in_log, returned (up to 10), and a records list. May be empty if no prior alert checks ran — that is also a valid result.

---

## TEST: B01
**section:** Feedstock Blending
**prompt:** List all available feedstocks and their biomethane potential values. Use the list_feedstocks tool.
**expect:** Returns feedstock_count=8 and a feedstocks list. Each entry has bmp_nl_kg_vs, cn_ratio, dm_pct, ammonia_risk, and h2s_risk.

## TEST: B02
**section:** Feedstock Blending
**prompt:** Blend 30 tonnes of Cattle slurry with 15 tonnes of Maize silage and 5 tonnes of Food waste. What is the expected daily biomethane yield in Nm3 and MWh?
**expect:** Returns blended cn_ratio, estimated_daily_output with biomethane_nm3 and biomethane_mwh. Should include ch4_fraction_used from live plant state. inhibition_warnings should mention food waste H2S risk.

## TEST: B03
**section:** Feedstock Blending
**prompt:** What happens if I try to blend six feedstocks at once? Use: Cattle slurry 10t, Pig manure 10t, Maize silage 10t, Food waste 10t, Grass silage 10t, Sewage sludge 10t. Call the blend_feedstocks tool to test this.
**expect:** Returns an error saying "Provide 1–5 feedstock entries" — the blend is rejected before calculation.

## TEST: B04
**section:** Feedstock Blending
**prompt:** Try blending a feedstock called Wood chips at 20 tonnes per day. What does the server return?
**expect:** Returns an errors list. Error message says the feedstock was not found and lists available feedstocks.

## TEST: B05
**section:** Feedstock Blending
**prompt:** Is the organic loading rate safe if I feed 7000 kg VS per day into a 2000 m3 digester?
**expect:** olr_kg_vs_m3_d=3.5, status=OPTIMAL or HIGH. Returns advice string referencing mesophilic range.

## TEST: B06
**section:** Feedstock Blending
**prompt:** First update ch4_pct in the plant state to 55. Then blend 30 tonnes of Cattle slurry with 15 tonnes of Maize silage. Does the biomethane estimate change compared to the default state?
**expect:** update_plant_state accepts ch4_pct=55. blend_feedstocks shows ch4_fraction_used=0.55. Biomethane Nm3 should be lower than the default (0.623) blend result.

---

## TEST: C01
**section:** KPI Rollups
**prompt:** Give me today's KPIs. Is the plant meeting the grid specification for purity?
**expect:** Returns biomethane_nm3, energy_mwh, co2_avoided_t, revenue_eur, and grid_spec_met as true or false. plant_utilisation_pct should be present.

## TEST: C02
**section:** KPI Rollups
**prompt:** Summarise the last 7 days of production. Which day had the highest output and which had the lowest?
**expect:** Returns period, totals, averages, week_on_week_production_pct, best_day and worst_day with dates and biomethane_nm3. daily_breakdown list has 7 entries.

## TEST: C03
**section:** KPI Rollups
**prompt:** Give me the monthly KPI report and tell me the annualised revenue projection.
**expect:** Returns 30-day totals, grid_spec_compliance_pct, and annualised_projection with revenue_eur. Compliance days should be between 0 and 30.

---

## TEST: D01
**section:** Lookup & Advisory
**prompt:** What are the safe operating ranges for all monitored parameters?
**expect:** topic resolves to "thresholds". Returns parameters dict covering temperature, pH, VFA, alkalinity, NH4, H2S, CH4, CN ratio, OLR, and HRT.

## TEST: D02
**section:** Lookup & Advisory
**prompt:** Look up h2s — what removal methods are available and which is most cost effective? Use the get_operational_reference tool with topic "h2s".
**expect:** topic alias "h2s" resolves to "desulphurisation". Returns methods list with name, efficiency, cost, and notes. Iron dosing or micro-aeration should appear as low-cost options.

## TEST: D03
**section:** Lookup & Advisory
**prompt:** What are the European biomethane grid injection quality standards?
**expect:** topic alias "grid" resolves to "grid_specs". Returns parameters including ch4_min_pct, h2s_max_mg_m3, o2_max_ppm, and wobbe_index.

## TEST: D04
**section:** Lookup & Advisory
**prompt:** Compare mesophilic and thermophilic digestion. Which regime is better suited to a food waste plant that requires hygienisation? Use get_operational_reference with topic "thermophilic".
**expect:** topic alias "thermophilic" resolves to "mesophilic_vs_thermophilic". Returns both regimes with pros, cons, temperature ranges, and typical_use. Thermophilic entry mentions pasteurisation or hygienisation.

## TEST: D05
**section:** Lookup & Advisory
**prompt:** Look up ammonia — what causes it and how do I address it? Use get_operational_reference with topic "ammonia".
**expect:** topic alias "ammonia" resolves to "inhibitors". Returns items list including free ammonia inhibitor entry with cause, threshold, and remedies.

## TEST: D06
**section:** Lookup & Advisory
**prompt:** Look up fault — what should I do if biogas flow has been dropping steadily for several days?
**expect:** topic alias "fault" resolves to "troubleshooting". Returns scenarios list. First scenario covers dropping biogas flow with causes and actions.

## TEST: D07
**section:** Lookup & Advisory
**prompt:** Try looking up the topic cheese. How does the server handle an unknown topic?
**expect:** Returns an error key saying topic not found. Returns available_topics list. Does NOT crash or return empty response.

---

## TEST: E01
**section:** Calibration — Buswell BMP
**prompt:** Calculate the theoretical biomethane potential for glucose (C6H12O6) using the Buswell equation.
**expect:** Returns ch4_mol_per_mol_substrate, co2_mol_per_mol_substrate, ch4_fraction_pct (~50%), and co2_fraction_pct (~50%). Reference to Buswell & Mueller (1952).

## TEST: E02
**section:** Calibration — Buswell BMP
**prompt:** Calculate Buswell BMP for cellulose (C6H10O5). What is the theoretical CH4 fraction?
**expect:** Returns ch4_fraction_pct close to 50%. Includes h2o_consumed_mol. Note on dividing by molecular weight to get NL CH4/g VS.

## TEST: E03
**section:** Calibration — Buswell by Class
**prompt:** Calculate BMP for lipid (tripalmitin) using the buswell_bmp_by_class tool.
**expect:** Returns substrate_class, molecular_weight (~807), bmp_nl_per_g_vs (~350-400), and bmp_nl_per_kg_vs.

## TEST: E04
**section:** Calibration — Buswell by Class
**prompt:** What happens if I ask for a substrate class called "plastic"? Use the buswell_bmp_by_class tool to test error handling.
**expect:** Returns error key with message about unknown class. Lists available classes: carbohydrate_cellulose, carbohydrate_glucose, protein_generic, lipid_tripalmitin, lipid_triolein.

## TEST: E05
**section:** Calibration — Energy Conversion
**prompt:** Calculate the energy conversion factor for 97.4% pure biomethane.
**expect:** Returns LHV_biomethane_kWh_per_Nm3 around 10.2-10.3. Shows STP_to_NTP_correction and comparison to script value (10.55).

## TEST: E06
**section:** Calibration — Energy Conversion
**prompt:** What is the kWh/Nm3 for pure methane (100% CH4)?
**expect:** Returns higher value than 97.4% case. Shows derivation from LHV CH4 = 35.88 MJ/Nm3. Reference: Perry & Green (2008).

## TEST: E07
**section:** Calibration — C/N Ratio
**prompt:** Calculate C/N ratio from elemental analysis: 44% carbon, 1.8% nitrogen (typical for maize silage).
**expect:** Returns cn_ratio around 24-25, status should be OPTIMAL. Advice mentions ideal range 20-30.

## TEST: E08
**section:** Calibration — C/N Ratio
**prompt:** What if nitrogen is 0.5%? Is this a problem?
**expect:** Returns high cn_ratio (>50), status HIGH or VERY HIGH. Advice mentions add nitrogen-rich co-substrate like manure.

## TEST: E09
**section:** Calibration — C/N Ratio
**prompt:** What happens if I set nitrogen to 0? Use the cn_ratio_from_composition tool with carbon=44 and nitrogen=0.
**expect:** Returns error: "Nitrogen fraction must be > 0". Division by zero protection.

## TEST: E10
**section:** Calibration — OLR from Recipe
**prompt:** Calculate OLR for recipe: 30t cattle slurry + 15t maize silage in a 2000 m3 digester.
**expect:** Returns olr_kg_vs_m3_day (~2.5-3.5), status OPTIMAL. Shows recipe_streams with vs_kg_per_day for each feedstock.

## TEST: E11
**section:** Calibration — OLR from Recipe
**prompt:** What if I feed 100t food waste into a 500 m3 digester? Use the olr_from_recipe tool.
**expect:** Returns olr_kg_vs_m3_day > 4.5, status OVERLOADED. Advice warns acidification risk.

## TEST: E12
**section:** Calibration — OLR from Recipe
**prompt:** Try a recipe with an unknown feedstock called "rock".
**expect:** Returns errors list with message "'rock' not in feedstock table."

## TEST: E13
**section:** Calibration — Biodegradability
**prompt:** Calculate biodegradability coefficient for carbohydrate_glucose with empirical BMP 400 NL/kg VS. Use the biodegradability_coefficient tool.
**expect:** Returns biodegradability_coefficient_eta around 0.8-0.9. Flag should contain GOOD or MODERATE.

## TEST: E14
**section:** Calibration — Biodegradability
**prompt:** What if empirical BMP is 1000 NL/kg VS for glucose? Is that possible?
**expect:** Returns eta > 1.0, flag IMPOSSIBLE. Message says empirical > theoretical — check units or measurement.

## TEST: E15
**section:** Calibration — Combined Workflow
**prompt:** First calculate Buswell BMP for protein_generic, then check if empirical BMP of 280 NL/kg VS is reasonable.
**expect:** Buswell returns theoretical ~400-450 NL/kg VS. Biodegradability check returns eta ~0.6-0.7 with MODERATE flag.
---

## TEST: F01
**section:** AD4 Simulation — Steady State
**prompt:** Run the AD4 digestion simulation at a typical farm-scale dilution rate of 0.05 per day with influent COD of 25 g/L. What is the steady-state methane flow rate and is the digester healthy?
**expect:** Returns steady_state with S1, S2, X2 values. methane_mL_per_L_per_d present. interpretation.methane_status is GOOD or MODERATE. interpretation.S2_status is HEALTHY or WATCH. healthy flag is true.

## TEST: F02
**section:** AD4 Simulation — Steady State
**prompt:** Compare two dilution rates: run ad4_simulate at D=0.04 and then at D=0.06, both with 25 g/L COD. Which gives higher methane output and which has higher VFA accumulation?
**expect:** Two ad4_simulate calls. Higher D (0.06) should give higher methane_mL_per_L_per_d but potentially higher S2. Lower D (0.04) gives more residence time and lower S2. Both should return healthy=true if within safe range.

## TEST: F03
**section:** AD4 Simulation — Temperature Effects
**prompt:** Run the simulator at digester temperature 30°C versus 37°C with dilution rate 0.05 and COD 25 g/L. How does the colder temperature affect the methane yield?
**expect:** Two ad4_simulate calls with digester_temp_c=30 and digester_temp_c=37. The 30°C run should show lower mu2_max_effective (Arrhenius correction applied). methane output lower at 30°C. interpretation.temperature_correction_applied=true for both.

## TEST: F04
**section:** AD4 Simulation — Washout
**prompt:** What happens if the dilution rate is set dangerously high at 0.8 per day with 25 g/L COD? Use ad4_simulate to test washout conditions.
**expect:** Returns washout=true or souring=true, or healthy=false. S2_mmol_per_L likely CRITICAL. X2 near zero (NEAR_WASHOUT or WASHOUT status). interpretation warns of failure condition.

## TEST: F05
**section:** AD4 Simulation — Boundary Errors
**prompt:** Try calling ad4_simulate with an invalid dilution rate of 0.0 and then with an impossible dilution rate of 5.0. What does the server return for each?
**expect:** Both return error keys. Error for 0.0 says dilution_rate must be in (0, 2.0]. Error for 5.0 says same. No crash. Two separate ad4_simulate calls expected.

## TEST: F06
**section:** AD4 Simulation — Critical Dilution Rate
**prompt:** What is the critical dilution rate for washout at the current plant temperature and COD of 25 g/L? How much safety margin does the current HRT of 22 days give?
**expect:** Returns D_crit_numerical_per_d, current_plant_D_per_d (≈0.0455 for HRT=22), safety_margin_pct, and status SAFE or CAUTION. HRT_crit_days present. recommended_min_HRT_days present.

## TEST: F07
**section:** AD4 Simulation — Critical Dilution Rate
**prompt:** How does a winter temperature of 28°C change the washout risk compared to the standard 35°C? Use ad4_critical_dilution_rate with digester_temp_c=28 and then digester_temp_c=35.
**expect:** Two ad4_critical_dilution_rate calls. D_crit at 28°C lower than at 35°C. D_crit_reduction_vs_35c_pct > 0 for the cold case. temperature_note mentions Arrhenius and theta=1.035. Safety margin smaller at 28°C.

## TEST: F08
**section:** AD4 Simulation — Perturbation & Recovery
**prompt:** Simulate a 10-day substrate overload spike where COD doubles from 25 to 50 g/L. Does the digester recover after 30 days back at normal COD? Use ad4_perturbation_test.
**expect:** Returns scenario=overload_spike. baseline_steady_state present. peak_stress_during_overload shows elevated S2_peak_mmol_per_L. post_recovery_state present. recovered_healthy and interpretation string present.

## TEST: F09
**section:** AD4 Simulation — Perturbation & Recovery
**prompt:** What happens with a severe overload: COD spikes to 120 g/L for 20 days in a 22-day HRT digester? Does it recover or fail? Use ad4_perturbation_test with overload_cod_g_per_l=120, overload_days=20, recovery_days=60.
**expect:** Returns washout_detected or souring_detected=true. peak_stress S2_peak likely CRITICAL (>150). interpretation says FAILED or RECOVERED depending on simulation outcome. temperature_note present.

## TEST: F10
**section:** AD4 Simulation — Perturbation Boundary Errors
**prompt:** Try calling ad4_perturbation_test with overload_days=0 (invalid) and then with recovery_days=1 (too short). What errors does the server return?
**expect:** Two calls. overload_days=0 returns error saying overload_days must be in [1, 60]. recovery_days=1 returns error saying recovery_days must be in [5, 200]. No crash.

---

## TEST: G01
**section:** EnKF State Estimation — Initialisation
**prompt:** Initialise the Ensemble Kalman Filter for a 2000 m3 digester with HRT 22 days and estimated influent COD of 25 g/L. Use enkf_initialise and confirm the filter is ready.
**expect:** Returns confirmation dict with digester_volume_m3=2000, hrt_days=22, s1_in_g_per_l=25. Filter status shows initialised=true or equivalent. n_ensemble present.

## TEST: G02
**section:** EnKF State Estimation — Initialisation
**prompt:** Re-initialise the EnKF for a smaller digester: 800 m3, HRT 18 days, COD 30 g/L, ensemble size 200. Then check the filter status with enkf_status.
**expect:** enkf_initialise returns confirmation with the new parameters. enkf_status returns days_tracked=0 or 1. initialised=true. No estimate yet (or initial estimate with high uncertainty).

## TEST: G03
**section:** EnKF State Estimation — Update Cycle
**prompt:** First initialise the EnKF with default parameters (2000 m3 digester, 22-day HRT, 25 g/L COD). Then advance one day using enkf_update. What are the estimated VFA (S2) and methanogen biomass (X2)?
**expect:** enkf_initialise called first, then enkf_update. Returns S2_mean, S2_std, X2_mean, X2_std. souring_probability and washout_probability present. risk_level present. plant_state_used shows the biogas and temperature values read from SQLite.

## TEST: G04
**section:** EnKF State Estimation — Update Cycle
**prompt:** Initialise the EnKF, then run 3 consecutive daily updates using enkf_update. After the third update, call enkf_status and report the current risk level and S2 trend.
**expect:** enkf_initialise called once. Three enkf_update calls. Final enkf_status returns days_tracked=3. operator_summary present with S2_mean, X2_mean, souring risk. S2 trend direction (stable/rising/falling) present.

## TEST: G05
**section:** EnKF State Estimation — FOS Measurement Injection
**prompt:** Initialise the EnKF, then run an update providing a FOS lab measurement of 800 mg/L. How does providing the FOS value affect the uncertainty in the S2 estimate compared to running without it?
**expect:** enkf_initialise called. enkf_update called with fos_mg_per_l=800. plant_state_used shows fos_provided=true. S2_std should be lower than a run without FOS (tighter estimate). S2_mean should be elevated reflecting high VFA reading.

## TEST: G06
**section:** EnKF State Estimation — Error Handling
**prompt:** Try calling enkf_update without first calling enkf_initialise. What error does the server return?
**expect:** Returns error key saying "EnKF not initialised. Call enkf_initialise() first." hint field present. No crash.

## TEST: G07
**section:** EnKF State Estimation — Status & Trend
**prompt:** Initialise the EnKF for a 2000 m3 digester, run 5 daily updates, then call enkf_status. What does the operator_summary say about the current VFA level and souring risk?
**expect:** 5 enkf_update calls after initialise. enkf_status returns days_tracked=5. latest_estimate present with S2_mean, X2_mean, souring_probability. operator_summary is a formatted string with units. risk_level present.
# Biomethane MCP Server — RAG Out-of-Distribution Stress Tests
## Section H: Questions NOT covered by doc5_faq.md
## Purpose: Test whether the system reasons from tool results and first principles
##           rather than retrieving and parroting FAQ content.
##
## Each test is deliberately outside FAQ coverage. The RAG retriever will either
## return weakly-relevant chunks or nothing. A good response uses the MCP tools
## directly and reasons from their output. A failing response hallucinates or
## refuses to engage.
##
## Categories:
##   H01–H05  Cross-tool reasoning (multi-step, no FAQ answer)
##   H06–H09  Numerical / quantitative reasoning from tool output
##   H10–H12  Feedstock and operational edge cases not in FAQ
##   H13–H15  Causal / diagnostic reasoning chains

---

## TEST: H01
**section:** RAG OOD — Cross-Tool Reasoning
**prompt:** The digester temperature has dropped to 29°C due to a heating failure. Without fixing the heater, what is the minimum safe HRT I should operate at? Use the simulator tools to find this.
**expect:** Calls ad4_critical_dilution_rate with digester_temp_c=29. Converts D_crit_numerical to HRT (1/D_crit). Applies the recommended 40% safety margin to give recommended_min_HRT_days. Does NOT just quote FAQ table values — reads from tool output.

## TEST: H02
**section:** RAG OOD — Cross-Tool Reasoning
**prompt:** I want to know if I can safely process 40 tonnes per day of Food waste in my 2000 m3 digester at HRT 22 days. First blend the recipe, then check the OLR, then simulate at the resulting dilution rate.
**expect:** Three tool calls: blend_feedstocks (40t food waste), olr_from_recipe or the vs_kg result, then ad4_simulate at D=1/22≈0.0455. Response synthesises inhibition_warnings from blend, OLR status, and healthy flag from simulation. Not in FAQ.

## TEST: H03
**section:** RAG OOD — Cross-Tool Reasoning
**prompt:** My EnKF has been showing souring_probability rising from 0.12 to 0.28 over 5 days. I want to know what D_crit headroom I have before running a perturbation test. Call the relevant tools in the right order.
**expect:** Calls enkf_status (or reports trend context), then ad4_critical_dilution_rate to get safety_margin_pct, then ad4_perturbation_test at reduced COD to model an OLR cut. Three-tool chain. FAQ covers each tool in isolation but not this sequence.

## TEST: H04
**section:** RAG OOD — Cross-Tool Reasoning
**prompt:** Compare the biomethane yield from blending 50 tonnes of Maize silage alone versus blending 25 tonnes of Maize silage with 25 tonnes of Cattle slurry. Which blend is better for grid injection purity?
**expect:** Two blend_feedstocks calls. Compares estimated_daily_output.biomethane_nm3 and biomethane_mwh. Notes ch4_fraction_used from live plant state. References inhibition_warnings difference. FAQ does not cover blend comparisons or purity optimisation.

## TEST: H05
**section:** RAG OOD — Cross-Tool Reasoning
**prompt:** What is the theoretical maximum dilution rate my digester can sustain at 38°C, and how does that compare to my current operating point? Express the answer as a safety margin in days of HRT buffer.
**expect:** Calls ad4_critical_dilution_rate with digester_temp_c=38. Reads current_plant_HRT_days and HRT_crit_days from result. Computes HRT buffer = HRT_crit_days - current_plant_HRT_days. Answers in days. Not in FAQ.

---

## TEST: H06
**section:** RAG OOD — Quantitative Reasoning
**prompt:** If I run the EnKF for 7 days and the souring_probability goes from 0.05 to 0.40, at what day did I cross the WARNING threshold? Use enkf_status to check current state, then explain the threshold crossing logic.
**expect:** Calls enkf_status. Explains that WARNING threshold is souring_probability > 0.30 (from tool knowledge, not just FAQ). Identifies day of crossing would be between day when probability crossed 0.30. Demonstrates numerical threshold reasoning.

## TEST: H07
**section:** RAG OOD — Quantitative Reasoning
**prompt:** Calculate the Buswell BMP for lipid (tripalmitin) and then use that theoretical BMP to check how realistic an empirical BMP of 500 NL/kg VS would be. What is the biodegradability coefficient and what does it mean?
**expect:** Calls buswell_bmp_by_class for lipid_tripalmitin, gets bmp_nl_per_kg_vs (~350-400). Then calls biodegradability_coefficient with empirical_bmp=500 and that theoretical value. Returns eta > 1.0, flag IMPOSSIBLE. Not directly in FAQ (FAQ covers glucose only for this workflow).

## TEST: H08
**section:** RAG OOD — Quantitative Reasoning
**prompt:** What OLR in kg VS/m3/day would I get from feeding 20 tonnes of Food waste and 10 tonnes of Pig manure into a 1500 m3 digester? Is that within the safe mesophilic range?
**expect:** Calls olr_from_recipe with the feedstock mix and digester_volume_m3=1500. Returns olr_kg_vs_m3_day, status. Explains whether OPTIMAL, HIGH, or OVERLOADED. FAQ does not cover Pig manure or 1500 m3 digester. Tool call required.

## TEST: H09
**section:** RAG OOD — Quantitative Reasoning
**prompt:** If my FOS lab reading is 2400 mg/L, what is the implied S2 in mmol/L and what EnKF risk level does that suggest before even running the filter?
**expect:** Computes S2 = 2400/60 = 40 mmol/L from the FOS conversion formula. Identifies this as WATCH zone (30-80 mmol/L). Notes this would translate to a souring_probability that could put the filter in WATCH territory. Does NOT call a tool — tests pure reasoning when RAG is unhelpful. Response should be grounded and numerical, not vague.

---

## TEST: H10
**section:** RAG OOD — Feedstock Edge Cases
**prompt:** What is the C/N ratio of a blend of 20 tonnes Grass silage and 20 tonnes Sewage sludge, and is the blend likely to cause ammonia inhibition?
**expect:** Calls blend_feedstocks with Grass silage and Sewage sludge. Reports blend_cn_ratio. Checks inhibition_warnings for ammonia risk. Sewage sludge is high-nitrogen — low C/N expected. FAQ does not cover this feedstock pair. Tool output drives the answer.

## TEST: H11
**section:** RAG OOD — Feedstock Edge Cases
**prompt:** List all available feedstocks. Which three have the highest BMP and which two carry the highest combined inhibition risk if blended together?
**expect:** Calls list_feedstocks. Ranks by bmp_nl_kg_vs. Identifies top 3. Cross-references ammonia_risk and h2s_risk flags to find highest combined inhibition pair. FAQ lists no feedstock rankings or risk combinations. Requires reasoning over the tool output.

## TEST: H12
**section:** RAG OOD — Feedstock Edge Cases
**prompt:** If I want to maximise daily biomethane output in MWh from exactly 50 wet tonnes of feedstock, which single feedstock from the available list should I choose and why?
**expect:** Calls list_feedstocks to get BMP and DM values. Reasons that highest BMP × VS loading gives highest output. Identifies the winner (likely Maize silage or Food waste). May call blend_feedstocks to verify. FAQ covers blending but not single-feedstock optimisation.

---

## TEST: H13
**section:** RAG OOD — Causal Diagnostic Chains
**prompt:** My methane percentage has dropped from 62% to 55% over the past week but biogas flow is unchanged. The digester temperature is normal. What does this pattern suggest and which tools should I use to investigate?
**expect:** Reasons that unchanged flow + dropping CH4 suggests CO2 increasing, possible early acidification or feedstock change — not a temperature or washout issue. Suggests update_plant_state to set ch4_pct=55, then enkf_update, then ad4_simulate to compare predicted vs observed. Diagnostic chain not covered in FAQ.

## TEST: H14
**section:** RAG OOD — Causal Diagnostic Chains
**prompt:** I ran ad4_perturbation_test with an overload and got souring_detected=true but recovered_healthy=true. What does this combination mean operationally and what should my monitoring frequency be after such an event?
**expect:** Explains that souring occurred during overload but the digester recovered — meaning the methanogen population survived. Recommends increased monitoring: daily enkf_update, weekly FOS/TAC. Notes X2_min during overload is the key indicator of population stress. Not in FAQ — requires synthesis from tool semantics.

## TEST: H15
**section:** RAG OOD — Causal Diagnostic Chains
**prompt:** The EnKF washout_probability has jumped from 0.02 to 0.25 in a single day. What are the three most likely causes, ranked by probability, and what single tool call would best distinguish between them?
**expect:** Lists: (1) sudden temperature drop reducing mu2_max, (2) OLR spike overloading methanogens, (3) H2S or ammonia inhibition. Recommends check_alerts as the single best differentiating call — it checks temperature, H2S, and pH simultaneously. Not in FAQ — requires causal reasoning over the tool ecosystem.
