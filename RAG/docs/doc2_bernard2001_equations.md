# Bernard et al. (2001) — AM2 Kinetics Reference
## Extracted equations for RAG chunking (4-state subset only)
## Source: Bernard, Hadj-Sadok, Dochain, Genovesi, Steyer (2001)
## "Dynamical model development and parameter identification for an anaerobic wastewater treatment process"
## Biotechnology and Bioengineering, 75(4), 424–438.

---

## CHUNK 1: Model Purpose and Scope

The AM2 model describes a two-population continuous anaerobic digestion process. Two sequential biological reactions are modeled:

- **Reaction 1 (Acidogenesis):** Acidogenic bacteria (X1) consume organic substrate (S1) and produce volatile fatty acids (S2) as an intermediate.
- **Reaction 2 (Methanogenesis):** Methanogenic archaea (X2) consume VFAs (S2) and produce methane (CH4) and carbon dioxide (CO2).

The 4-state version tracks: S1 (substrate), S2 (VFA), X1 (acidogens), X2 (methanogens). Inorganic carbon (C) and alkalinity (Z) are omitted in the 4-state reduction.

---

## CHUNK 2: Acidogen Growth Rate — Monod Kinetics

**Equation:**
```
mu1(S1) = mu1_max * S1 / (Ks1 + S1)
```

**Physical meaning:** The growth rate of acidogens follows Monod (Michaelis-Menten) kinetics. It increases with substrate S1 but saturates at mu1_max as substrate becomes abundant.

- At S1 << Ks1: mu1 ≈ (mu1_max / Ks1) * S1  — linear, substrate-limited
- At S1 >> Ks1: mu1 ≈ mu1_max               — saturated, growth-rate-limited
- At S1 = Ks1: mu1 = mu1_max / 2             — half-maximum rate

**Parameters (Benyahia 2012, mesophilic manure):**
- mu1_max = 1.20 d⁻¹
- Ks1 = 7.10 g COD/L

**No inhibition term.** Acidogens are not inhibited by high VFA in this model. They are robust and typically persist unless dilution rate is very high (D > 1.0 d⁻¹).

---

## CHUNK 3: Methanogen Growth Rate — Haldane Kinetics (Substrate Inhibition)

**Equation:**
```
mu2(S2) = mu2_max * S2 / (Ks2 + S2 + S2²/Ki2)
```

**Physical meaning:** Methanogens follow Haldane kinetics — growth increases with VFA (S2) but is inhibited at high VFA concentrations. This non-monotonic relationship is the core instability mechanism of anaerobic digestion.

**Key behavior:**
- At low S2: growth is substrate-limited (increasing mu2)
- At S2_opt = sqrt(Ks2 * Ki2): mu2 reaches its maximum achievable value
- At S2 > S2_opt: VFA inhibits methanogens (decreasing mu2) — DANGER ZONE

**Peak VFA concentration (S2_opt):**
```
S2_opt = sqrt(Ks2 * Ki2) = sqrt(9.28 * 256) ≈ 48.7 mmol/L
```

**Maximum achievable mu2:**
```
mu2_peak = mu2_max * S2_opt / (Ks2 + S2_opt + S2_opt²/Ki2)
         ≈ mu2_max / (1 + 2*sqrt(Ks2/Ki2))
         ≈ 0.37 d⁻¹  (for default parameters)
```

**Parameters (Benyahia 2012):**
- mu2_max = 0.74 d⁻¹
- Ks2 = 9.28 mmol/L
- Ki2 = 256.0 mmol/L

**Critical insight:** The inhibition constant Ki2 = 256 mmol/L is the most sensitive parameter in the model. If Ki2 is lower (e.g., 100 mmol/L for a stressed or non-acclimated population), the souring threshold occurs at much lower VFA concentrations.

---

## CHUNK 4: Substrate Mass Balance — S1

**ODE:**
```
dS1/dt = D*(S1_in - S1) - k1*mu1(S1)*X1
```

**Term meanings:**
- `D*(S1_in - S1)`: Net hydraulic flow — substrate entering with influent minus substrate leaving with effluent. Positive when S1 < S1_in (normal operation).
- `k1*mu1(S1)*X1`: Substrate consumed by acidogen growth. k1 is the yield coefficient: grams of COD consumed per gram of acidogen growth.

**At steady state:** dS1/dt = 0, so: D*(S1_in - S1*) = k1 * mu1(S1*) * X1*

**Units:** g COD/L per day
**k1 = 42.14** g COD consumed per g COD equivalent of acidogen growth

---

## CHUNK 5: VFA Mass Balance — S2

**ODE:**
```
dS2/dt = D*(0 - S2) + k2*mu1(S1)*X1 - k3*mu2(S2)*X2
```

**Term meanings:**
- `D*(0 - S2)` = `-D*S2`: VFA washed out by hydraulic flow. Influent VFA assumed zero (S2_in = 0).
- `k2*mu1(S1)*X1`: VFA produced by acidogenesis. k2 is the VFA yield from acidogen growth.
- `k3*mu2(S2)*X2`: VFA consumed by methanogenesis. k3 is the VFA consumption yield.

**S2 is the critical balance variable.** Souring occurs when VFA production exceeds VFA consumption plus washout:
```
k2*mu1*X1  >  D*S2 + k3*mu2*X2
```

**Units:** mmol/L per day
**k2 = 116.50** mmol VFA produced per g COD growth of X1
**k3 = 268.0** mmol VFA consumed per g VSS growth of X2

---

## CHUNK 6: Acidogen Biomass Balance — X1

**ODE:**
```
dX1/dt = (mu1(S1) - alpha*D) * X1
```

**Term meanings:**
- `mu1(S1)*X1`: growth of acidogens
- `alpha*D*X1`: washout of acidogens by hydraulic dilution

**alpha:** Biomass retention factor. alpha = 1.0 for a plain CSTR (no cell recycle). alpha < 1.0 when a settler or membrane retains biomass (effective HRT for cells > HRT for liquid).

**Washout condition for X1:** mu1(S1) < alpha*D
Since mu1_max = 1.20 d⁻¹, X1 washes out only if D > 1.20 d⁻¹ (HRT < 0.83 days) in the worst case. This is rarely reached in practice. Acidogens are generally stable.

---

## CHUNK 7: Methanogen Biomass Balance — X2

**ODE:**
```
dX2/dt = (mu2(S2) - alpha*D) * X2
```

**Term meanings:**
- `mu2(S2)*X2`: growth of methanogens (Haldane-limited)
- `alpha*D*X2`: washout of methanogens

**Washout condition for X2:** mu2(S2) < alpha*D

Because mu2 has a **maximum achievable value** (≈ 0.37 d⁻¹ for default parameters), if D exceeds this maximum, no VFA concentration can support methanogen growth. The digester will inevitably fail.

**Critical dilution rate:**
```
D_crit = mu2_max / (1 + 2*sqrt(Ks2/Ki2))
       ≈ 0.74 / (1 + 2*sqrt(9.28/256))
       ≈ 0.37 d⁻¹
```

Corresponding critical HRT: HRT_crit = 1/D_crit ≈ 2.7 days.

**In practice, operate at D < 0.15 d⁻¹ (HRT > 7 days) for stable mesophilic operation with safety margin.**

---

## CHUNK 8: Methane Production (Simplified)

**Equation:**
```
Q_CH4(t) = k6 * mu2(S2(t)) * X2(t)
```

**Units:** mL CH4 / (L reactor * day)

**k6 = 453.0** mL CH4 per mmol VFA consumed by methanogens.

**Physical meaning:** Methane production is directly proportional to methanogen activity. When X2 declines (washout approaching) or when mu2 is suppressed by high S2 (Haldane inhibition), methane production drops.

**Methane flow is a lagging indicator.** By the time methane drops noticeably, S2 has usually already risen significantly. Monitor S2 directly rather than relying solely on biogas flow as an early warning.

---

## CHUNK 9: Stability Analysis Summary (Bernard 2001)

The AM2 model has two stable equilibria (operating points) and one unstable equilibrium:

**Equilibrium 1 — Normal operation (desired):**
- X1 > 0, X2 > 0
- Low S2 (methanogens active, consuming VFA)
- Stable when D < D_crit and OLR is within capacity

**Equilibrium 2 — Washout (failure):**
- X1 ≈ 0, X2 ≈ 0
- S1 = S1_in (substrate passes through unconverted)
- S2 elevated (no methanogens to consume VFA)
- This is always mathematically stable — once in washout, system stays there without intervention

**Unstable equilibrium:**
- Exists between the two stable equilibria
- Represents the "tipping point" — small perturbations push the system toward either normal operation or complete washout

**Practical implication:** If a disturbance pushes S2 above a threshold or X2 below a threshold, the system may cross the unstable equilibrium and cascade to washout. Recovery requires: reducing D, reducing OLR (S1_in), or adding buffer/alkalinity to counteract acidification.

---

## CHUNK 10: The Souring Spiral (Non-linear Feedback)

The most dangerous failure mode in the 4-state model involves a positive feedback loop driven by Haldane inhibition:

```
OLR spike
  → S2 rises above S2_opt (~49 mmol/L)
  → mu2 decreases (Haldane inhibition)
  → VFA accumulates faster (less consumption)
  → S2 rises further
  → mu2 decreases further
  → ... runaway acidification
  → X2 washout
```

**Intervention window:** The loop can be broken if:
1. **D is reduced** (lower influent flow → lower OLR, more residence time for methanogens)
2. **S1_in is reduced** (lower organic loading → less VFA production)
3. In a 6-state model, **alkalinity addition** buffers pH, but this is not represented in the 4-state model

**Detection in simulation:** Monitor for `souring = True` (S2 > 150 mmol/L) or `washout = True` (X2 < 0.05 g/L) in MCP tool output. Rising S2 trend is the earliest warning.

---

*Citation: Bernard, O., Hadj-Sadok, Z., Dochain, D., Genovesi, A., & Steyer, J.P. (2001). Dynamical model development and parameter identification for an anaerobic wastewater treatment process. Biotechnology and Bioengineering, 75(4), 424–438.*
