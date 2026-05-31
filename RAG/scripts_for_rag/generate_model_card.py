"""
generate_model_card.py
======================
Auto-generates the parameter-dependent sections of doc1_ad4_model_card.md
from your current AD4Params instance.

Run after every calibration / grid-search tuning session:
    python generate_model_card.py

Outputs:
    doc1_ad4_model_card.md  (full model card, parameters updated)
    calibration_log.jsonl   (append-only tuning history)

Usage with custom params:
    from ad4_simulator import AD4Params
    from generate_model_card import generate_model_card

    p = AD4Params(Ki2=180.0, mu2_max=0.65)
    generate_model_card(p, notes="Grid search run 3, manure batch B")
"""

import json
import math
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Import your simulator — adjust path if needed
# ---------------------------------------------------------------------------
import sys
sys.path.insert(0, str(Path(__file__).parent))

from ad4_simulator import AD4Params, AD4Simulator, AD4State


# ---------------------------------------------------------------------------
# Derived quantities
# ---------------------------------------------------------------------------

def derived_quantities(p: AD4Params) -> dict:
    """Compute all derived values from a parameter set."""
    S2_opt = math.sqrt(p.Ks2 * p.Ki2)
    mu2_peak = p.mu2(S2_opt)
    D_crit = mu2_peak                          # washout when D > mu2_peak
    HRT_crit = round(1.0 / D_crit, 2) if D_crit > 0 else None
    safe_D_max = round(D_crit * 0.40, 4)      # 40% of D_crit as practical limit
    safe_HRT_min = round(1.0 / safe_D_max, 1) if safe_D_max > 0 else None

    return {
        "S2_opt_mmol_per_L":    round(S2_opt, 2),
        "mu2_peak_per_d":       round(mu2_peak, 4),
        "D_crit_per_d":         round(D_crit, 4),
        "HRT_crit_days":        HRT_crit,
        "safe_D_max_per_d":     safe_D_max,
        "safe_HRT_min_days":    safe_HRT_min,
    }


# ---------------------------------------------------------------------------
# Markdown generators
# ---------------------------------------------------------------------------

def _param_table(p: AD4Params, d: dict) -> str:
    rows = [
        ("mu1_max", p.mu1_max, "d⁻¹", "Acidogen max growth rate"),
        ("Ks1",     p.Ks1,     "g COD/L", "Acidogen half-saturation constant"),
        ("k1",      p.k1,      "g COD / g COD", "Substrate consumption yield"),
        ("k2",      p.k2,      "mmol VFA / g COD", "VFA production yield"),
        ("mu2_max", p.mu2_max, "d⁻¹", "Methanogen max growth rate"),
        ("Ks2",     p.Ks2,     "mmol/L", "Methanogen half-saturation constant"),
        ("Ki2",     p.Ki2,     "mmol/L", "Haldane inhibition constant ← most sensitive"),
        ("k3",      p.k3,      "mmol VFA / g VSS", "VFA consumption yield"),
        ("k6",      p.k6,      "mL CH4 / mmol S2", "Methane production coefficient"),
        ("alpha",   p.alpha,   "—", "Biomass washout factor (1.0 = CSTR)"),
    ]
    header = "| Parameter | Symbol | Value | Unit | Meaning |\n"
    header += "|-----------|--------|-------|------|---------|\n"
    body = ""
    for sym, val, unit, meaning in rows:
        body += f"| {meaning.split('←')[0].strip()} | {sym} | **{val}** | {unit} | {meaning} |\n"
    return header + body


def _derived_table(d: dict) -> str:
    return f"""| Derived Quantity | Value |
|------------------|-------|
| Optimal VFA for mu2 peak (S2_opt) | **{d['S2_opt_mmol_per_L']} mmol/L** |
| Peak achievable mu2 | **{d['mu2_peak_per_d']} d⁻¹** |
| Critical dilution rate (D_crit) | **{d['D_crit_per_d']} d⁻¹** |
| Critical HRT (HRT_crit) | **{d['HRT_crit_days']} days** |
| Recommended max D (40% of D_crit) | **{d['safe_D_max_per_d']} d⁻¹** |
| Recommended min HRT | **{d['safe_HRT_min_days']} days** |
"""


def _souring_threshold_note(p: AD4Params, d: dict) -> str:
    """Generate a plain-language souring risk note calibrated to these params."""
    s2_opt = d["S2_opt_mmol_per_L"]
    s2_warn = round(s2_opt * 1.5, 1)   # 1.5x opt = watch zone
    s2_crit = round(s2_opt * 3.0, 1)   # 3x opt = critical
    return (
        f"With Ki2 = {p.Ki2} mmol/L, the Haldane peak occurs at S2 = {s2_opt} mmol/L.\n"
        f"- **Watch zone:** S2 > {s2_warn} mmol/L\n"
        f"- **Critical zone:** S2 > {s2_crit} mmol/L\n"
        f"- **Default souring flag threshold:** 150 mmol/L (fixed in simulator)\n\n"
        f"{'⚠️  NOTE: Ki2 < 200 → methanogens are sensitive. ' if p.Ki2 < 200 else ''}"
        f"{'✓  Ki2 ≥ 200 → methanogens are relatively tolerant of VFA.' if p.Ki2 >= 200 else ''}"
    )


# ---------------------------------------------------------------------------
# Full model card
# ---------------------------------------------------------------------------

def generate_model_card(
    params: AD4Params = None,
    notes: str = "",
    output_path: str = "doc1_ad4_model_card.md",
    log_path: str = "calibration_log.jsonl",
) -> str:
    """
    Generate a complete model card markdown from an AD4Params instance.

    Parameters
    ----------
    params      : AD4Params (defaults to AD4Params() if None)
    notes       : free-text annotation for calibration log
    output_path : where to write the model card
    log_path    : append-only JSONL calibration history file

    Returns
    -------
    Path to the generated model card file.
    """
    p = params or AD4Params()
    d = derived_quantities(p)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # --- Write calibration log entry ---
    log_entry = {
        "timestamp": timestamp,
        "notes": notes,
        "params": {
            "mu1_max": p.mu1_max, "Ks1": p.Ks1,
            "k1": p.k1, "k2": p.k2,
            "mu2_max": p.mu2_max, "Ks2": p.Ks2,
            "Ki2": p.Ki2, "k3": p.k3,
            "k6": p.k6, "alpha": p.alpha,
        },
        "derived": d,
    }
    with open(log_path, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    # --- Build markdown ---
    md = f"""# AD4 Model Card — 4-State AM2 Anaerobic Digestion Simulator
## Auto-generated: {timestamp}
{f'## Calibration notes: {notes}' if notes else ''}
---

## 1. What This Model Is

The AD4 model is a **4-state simplification of the AM2 (Anaerobic Model 2)** by Bernard et al. (2001).
It describes a CSTR anaerobic digester with two microbial populations and four state variables:
S1 (substrate), S2 (VFA), X1 (acidogens), X2 (methanogens).

The 4-state version omits Inorganic Carbon (C) and Alkalinity (Z) from the full 6-state AM2.
Valid when pH control is external and the focus is on VFA accumulation and biomass washout.

---

## 2. Current Parameter Set

*Last calibrated: {timestamp}*
{f'*Notes: {notes}*' if notes else ''}

### Kinetic and Stoichiometric Parameters

{_param_table(p, d)}

### Derived Operating Limits

{_derived_table(d)}

### Souring Risk Thresholds (calibrated to Ki2 = {p.Ki2})

{_souring_threshold_note(p, d)}

---

## 3. The Four State Variables

| Symbol | Name | Unit | Healthy Range | Critical Threshold |
|--------|------|------|---------------|-------------------|
| S1 | Organic substrate (COD) | g COD/L | 2–8 g/L (effluent) | — |
| S2 | Volatile fatty acids (VFA) | mmol/L | < {round(d['S2_opt_mmol_per_L'] * 0.6, 0):.0f} mmol/L | > {round(d['S2_opt_mmol_per_L'] * 3.0, 0):.0f} mmol/L |
| X1 | Acidogenic bacteria | g VSS/L | 0.3–1.5 g/L | < 0.01 = washout |
| X2 | Methanogenic archaea | g VSS/L | 1.0–3.0 g/L | < 0.05 = washout |

---

## 4. Kinetic Equations

### Acidogen growth (Monod):
```
mu1(S1) = {p.mu1_max} * S1 / ({p.Ks1} + S1)
```
- At S1 = {p.Ks1} g/L: mu1 = {p.mu1_max/2} d⁻¹ (half-maximum)
- Saturates at mu1_max = {p.mu1_max} d⁻¹

### Methanogen growth (Haldane):
```
mu2(S2) = {p.mu2_max} * S2 / ({p.Ks2} + S2 + S2² / {p.Ki2})
```
- Peak mu2 = **{d['mu2_peak_per_d']} d⁻¹** at S2 = **{d['S2_opt_mmol_per_L']} mmol/L**
- Above S2 = {d['S2_opt_mmol_per_L']} mmol/L: VFA inhibits methanogens (decreasing mu2)

---

## 5. Mass Balance ODEs (CSTR, alpha = {p.alpha})

```
dS1/dt = D*(S1_in - S1)  -  {p.k1}  * mu1(S1) * X1
dS2/dt = D*(0    - S2)   +  {p.k2}  * mu1(S1) * X1  -  {p.k3} * mu2(S2) * X2
dX1/dt = (mu1(S1) - {p.alpha}*D) * X1
dX2/dt = (mu2(S2) - {p.alpha}*D) * X2
```

**Methane production:**
```
Q_CH4 = {p.k6} * mu2(S2) * X2   [mL CH4 / L / d]
```

---

## 6. Operating Envelope (these parameters)

| Operating Variable | Value | Notes |
|-------------------|-------|-------|
| Critical D (washout) | {d['D_crit_per_d']} d⁻¹ | Do not exceed |
| Critical HRT | {d['HRT_crit_days']} days | Do not go below |
| Recommended max D | {d['safe_D_max_per_d']} d⁻¹ | 40% safety margin |
| Recommended min HRT | {d['safe_HRT_min_days']} days | Safe operating floor |
| Optimal S2 for mu2 | {d['S2_opt_mmol_per_L']} mmol/L | Peak methanogen activity |

---

## 7. Failure Mode Reference

| Observation | Diagnosis | Action |
|-------------|-----------|--------|
| S2 > {round(d['S2_opt_mmol_per_L'] * 1.5, 0):.0f} mmol/L | VFA in watch zone | Monitor closely |
| S2 > {round(d['S2_opt_mmol_per_L'] * 3.0, 0):.0f} mmol/L | Active souring | Reduce D or S1_in immediately |
| X2 < 0.5 g/L | Methanogen stress | Investigate inhibitors |
| X2 < 0.05 g/L | Washout | Emergency — halt or restart |
| D > {d['D_crit_per_d']} d⁻¹ | Exceeds D_crit | Methanogens cannot survive |
| Methane drops >20% | Lagging warning | Check S2 first |

---

## 8. Calibration History

See `calibration_log.jsonl` for full parameter history across tuning runs.

---

*Reference: Bernard et al. (2001), Biotechnology and Bioengineering, 75(4), 424–438.*
*Parameters: Benyahia et al. (2012) — adjusted per calibration notes above.*
"""

    out = Path(output_path)
    out.write_text(md)
    print(f"✓ Model card written to: {out.resolve()}")
    print(f"✓ Calibration log appended: {Path(log_path).resolve()}")
    print(f"\nDerived quantities:")
    for k, v in d.items():
        print(f"  {k}: {v}")
    return str(out)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate AD4 model card from parameters")
    parser.add_argument("--Ki2",     type=float, default=None, help="Haldane inhibition constant")
    parser.add_argument("--mu2_max", type=float, default=None, help="Methanogen max growth rate")
    parser.add_argument("--Ks2",     type=float, default=None, help="Methanogen half-saturation")
    parser.add_argument("--mu1_max", type=float, default=None, help="Acidogen max growth rate")
    parser.add_argument("--Ks1",     type=float, default=None, help="Acidogen half-saturation")
    parser.add_argument("--k1",      type=float, default=None)
    parser.add_argument("--k2",      type=float, default=None)
    parser.add_argument("--k3",      type=float, default=None)
    parser.add_argument("--k6",      type=float, default=None)
    parser.add_argument("--alpha",   type=float, default=None)
    parser.add_argument("--notes",   type=str,   default="", help="Calibration notes")
    parser.add_argument("--output",  type=str,   default="doc1_ad4_model_card.md")
    parser.add_argument("--log",     type=str,   default="calibration_log.jsonl")

    args = parser.parse_args()

    # Build params — only override what was passed
    kwargs = {k: v for k, v in vars(args).items()
              if v is not None and k not in ("notes", "output", "log")}
    p = AD4Params(**kwargs)

    generate_model_card(
        params=p,
        notes=args.notes,
        output_path=args.output,
        log_path=args.log,
    )
