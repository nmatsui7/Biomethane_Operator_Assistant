# Biomethane Router — API Reference

**Module**: `src/biomethane_router.py`

---

## Overview

The biomethane router is a **deterministic intent routing layer** that sits between
the operator's natural language message and the LLM. It classifies the operator's
request into one of five intent categories, executes a fixed tool chain for that
intent, and returns a structured context string + tool trace for the LLM to
synthesise into a final response.

### Architecture

```
Operator message
      │
      ▼
┌──────────┐    classify()
│  ROUTER  │ ──→ 5 intents + UNKNOWN
│          │
│  Intent  │ ──→ e.g. "SOURING"
│  detected│
└────┬─────┘
     │
     ▼
┌──────────┐
│  Chain   │ ──→ Deterministic MCP tool sequence
│  runner  │      (2–3 tools per intent)
└────┬─────┘
     │
     ▼
(context_string, intent_label, tool_trace_list)
     │
     ▼
    LLM  ──→ Natural language operator response
```

### Return format

```python
context_string:  Optional[str]   # Pre-computed tool data + LLM guardrails
intent_label:    str             # "STATUS" | "SOURING" | "SCENARIO" |
                                 # "PRODUCTION" | "REFERENCE" | "UNKNOWN"
tool_trace_list: list[str]       # Tool names (and args) called in the chain
```

When `context_string` is `None` (UNKNOWN intent or chain failure), the LLM falls
back to its own tool-selection behaviour.

---

## Module-level constants

### Souring thresholds

```python
_SOURING_WATCH  = 0.10    # souring_probability ≥ this → WATCH advisory
_SOURING_ACTION = 0.30    # souring_probability ≥ this → trigger perturbation test
```

These thresholds follow the same conventions used in `ad4_enkf.py` risk levels:

| Level | Condition | Behaviour |
|-------|-----------|-----------|
| HEALTHY | `< 0.10` | No special message |
| WATCH | `≥ 0.10` | Advise operator to monitor trend |
| ACTION | `≥ 0.30` | Run `ad4_perturbation_test` with current influent COD |

### OLR threshold

```python
_OLR_HIGH = 3.5  # kg VS / m³ / day
```

Used in the SCENARIO chain: if the operator's proposed blend exceeds this OLR,
the LLM is instructed to call `ad4_perturbation_test` to model the overload
response.

### Intent rules

```python
_INTENT_RULES: list[tuple[str, list[str]]]
```

A list of `(intent_label, keyword_list)` pairs. The first matching intent wins.
Evaluated in **priority order** — SOURING is checked first, STATUS last.

| Priority | Intent | Sample keywords |
|----------|--------|-----------------|
| 1 | `SOURING` | `souring`, `vfa`, `fos`, `fos/tac`, `acidif`, `methanogen`, `s2`, `x2`, `risk`, `washout` |
| 2 | `SCENARIO` | `what if`, `overload`, `blend`, `feedstock mix`, `olr`, `can i add`, `safe to` |
| 3 | `PRODUCTION` | `kpi`, `production`, `revenue`, `yield`, `output`, `daily report`, `grid spec`, `target` |
| 4 | `REFERENCE` | `what causes`, `why is`, `how does`, `explain`, `h2s`, `ammonia`, `inhibition`, `temperature range` |
| 5 | `STATUS` | `status`, `alarm`, `temperature`, `ph`, `check`, `morning briefing`, `is everything` |

Messages that match no rules return `"UNKNOWN"`, leaving tool selection to the
LLM.

---

## Functions

### `classify`

```python
def classify(message: str) -> str
```

Scans the message (lowercased) against `_INTENT_RULES` in order. Returns the
first matching intent label, or `"UNKNOWN"` if no keywords are found.

#### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `message` | `str` | Raw operator message |

#### Returns

| Value | Condition |
|-------|-----------|
| `"STATUS"` | Plant state / alert / check keywords |
| `"SOURING"` | VFA / methanogen / souring keywords |
| `"SCENARIO"` | What-if / blend / overload keywords |
| `"PRODUCTION"` | KPI / yield / report keywords |
| `"REFERENCE"` | Explanation / troubleshooting keywords |
| `"UNKNOWN"` | No rule matched |

---

### `_chain_status`

```python
def _chain_status(mcp) -> tuple[str, list[str]]
```

Calls two MCP tools in sequence:

1. `get_plant_state({})` — current digester sensor readings
2. `check_alerts({})` — triggered alert thresholds

Returns a context block with both results and an instruction telling the LLM not
to repeat those calls.

#### Tool trace

```python
["get_plant_state", "check_alerts"]
```

---

### `_extract_fos`

```python
def _extract_fos(message: str) -> Optional[float]
```

Extracts a FOS/VFA concentration (mg/L) from free text. The message must contain
at least one of `fos`, `vfa`, `volatile`, or `tac`. If present, the first
`<number> mg/L` pattern (with optional space before `mg`) is returned.

#### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `message` | `str` | Operator message containing a lab result |

#### Returns

- `float` — the extracted concentration in mg/L
- `None` — no FOS/VFA context words or no numeric pattern found

#### Matched patterns

```
"4500 mg/L"    → 4500.0
"800 mg/l"     → 800.0
"1200mg/L"     → 1200.0
"FOS is 600"   → None  (no "mg/L" unit)
```

---

### `_chain_souring`

```python
def _chain_souring(mcp, user_message: str = "") -> tuple[str, list[str]]
```

The most complex chain. Performs the following:

1. **FOS extraction** — if `_extract_fos` returns a value, it is passed as
   `fos_mg_per_l` to `enkf_update` for tighter S₂ estimation.
2. **EnKF update** — calls `enkf_update({})` or `enkf_update({"fos_mg_per_l": ...})`.
3. **Threshold check** — inspects `souring_probability` from the result:
   - `≥ _SOURING_ACTION` (0.30): calls `ad4_perturbation_test` with the current
     influent COD (`S1_in_g_per_L`) to model a 10-day overload / 30-day recovery
     scenario.
   - `≥ _SOURING_WATCH` (0.10): includes a WATCH advisory (no tool call).
   - `< 0.10`: no additional advice.

The returned context instructs the LLM to state S₂ and souring probability in
plain language without mentioning EnKF, AD4, or perturbation test terminology.

#### Tool trace

```python
# Without FOS data:
["enkf_update", "ad4_perturbation_test"]   # if souring_prob >= 0.30
["enkf_update"]                              # if souring_prob < 0.30

# With FOS data:
["enkf_update(fos_mg_per_l=600.0)", "ad4_perturbation_test"]
```

---

### `_chain_scenario`

```python
def _chain_scenario(mcp, user_message: str) -> tuple[str, list[str]]
```

Fetches the current plant state (for temperature / HRT context) and returns
instructions for the LLM to:

1. Call `blend_feedstocks` with the operator's proposed recipe.
2. Call `olr_from_recipe` using `_DIGESTER_VOLUME_M3`.
3. If OLR exceeds `_OLR_HIGH` (3.5 kg VS/m³/day), also call
   `ad4_perturbation_test` with `_S1_IN_G_PER_L`.

The chain itself only fetches plant state; the LLM is expected to make the
blend/OLR/simulation calls.

#### Tool trace

```python
["get_plant_state"]
```

---

### `_chain_production`

```python
def _chain_production(mcp, user_message: str) -> tuple[str, list[str]]
```

Calls `get_kpi_summary({"period": period})` where `period` is extracted from the
operator's message. Returns the KPI data and instructs the LLM not to repeat the
call.

#### Tool trace

```python
["get_kpi_summary(daily)"]
# or
["get_kpi_summary(weekly)"]
```

---

### `_extract_period`

```python
def _extract_period(message: str) -> str
```

Heuristic period extraction. Returns `"weekly"` if the message contains any of:
`week`, `7 day`, `7-day`, `last week`, `weekly`. Returns `"daily"` otherwise.
The `"monthly"` period is explicitly not supported and falls back to `"weekly"`.

---

### `_map_reference_topic`

```python
def _map_reference_topic(message: str) -> Optional[str]
```

Maps the operator message to one of four valid topics accepted by
`get_operational_reference`:

| Topic | Trigger keywords |
|-------|-----------------|
| `fos_tac` | `fos`, `tac`, `fos/tac`, `buffer`, `alkalinity`, `acidif` |
| `temperature` | `temperature`, `temp`, `mesophilic`, `thermophilic`, `degrees` |
| `olr` | `organic loading`, `olr`, `loading rate`, `kg vs`, `vs/m` |
| `cn_ratio` | `c/n`, `cn ratio`, `carbon nitrogen`, `carbon to nitrogen` |

Returns `None` if no topic matches — the caller (`_chain_reference`) falls
through to UNKNOWN rather than returning a misleading "No reference found"
response.

---

### `_chain_reference`

```python
def _chain_reference(mcp, user_message: str) -> tuple[str, list[str]]
```

Maps the message to a topic via `_map_reference_topic`. If a valid topic is
found, calls `get_operational_reference({"topic": topic})`. If the topic is
`None`, returns `("", [])` which causes `route()` to return `UNKNOWN`.

#### Tool trace

```python
["get_operational_reference(fos_tac)"]
# or empty [] if no valid topic
```

---

### `_load_site_geometry`

```python
def _load_site_geometry() -> tuple[float, float]
```

Called once at module import. Searches for `site_config.json` in `src/` and the
project root (in that order). Reads the `estimated_geometry` block and returns
`(digester_volume_m3, s1_in_g_per_l)`.

Raises `RuntimeError` if:
- The file is not found in either location.
- Either key is missing from `estimated_geometry`.

Both values are considered critical — wrong volume or COD values produce
physically meaningless simulation results.

#### site_config.json geometry format

```json
{
  "estimated_geometry": {
    "digester_volume_m3": 1.0,
    "hrt_days": 17,
    "feed_kg_vs_day": 1.28,
    "s1_in_g_per_l": 18.0
  }
}
```

Only `digester_volume_m3` and `s1_in_g_per_l` are required by the router. The
values are exposed as module-level constants:

```python
_DIGESTER_VOLUME_M3: float   # m³ — digester working volume
_S1_IN_G_PER_L:      float   # g COD/L — baseline influent substrate COD
```

---

### `route`

```python
def route(
    user_message: str,
    mcp,
) -> tuple[Optional[str], str, list[str]]
```

Public entry point. Classifies the message, runs the corresponding tool chain,
and returns the triple `(context, intent, tools_called)`.

#### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `user_message` | `str` | The operator's natural language message |
| `mcp` | MCP client | An object with a `call_tool(tool_name, args)` method |

#### Returns

| Field | Type | Description |
|-------|------|-------------|
| `context` | `Optional[str]` | Pre-computed tool data with LLM guardrails, or `None` for UNKNOWN / failures |
| `intent` | `str` | The classified intent label |
| `tools_called` | `list[str]` | Names (and args) of MCP tools called during the chain |

#### Behaviour by intent

| Intent | Tools called | Context returned |
|--------|-------------|-----------------|
| `STATUS` | `get_plant_state`, `check_alerts` | Plant state + alerts |
| `SOURING` | `enkf_update` (± `ad4_perturbation_test`) | EnKF estimate ± perturbation scenario |
| `SCENARIO` | `get_plant_state` | Plant state + instructions for LLM tool calls |
| `PRODUCTION` | `get_kpi_summary(period)` | KPI data |
| `REFERENCE` | `get_operational_reference(topic)` | Reference data, or → UNKNOWN if no topic matched |
| `UNKNOWN` | (none) | `None` — LLM handles tool selection independently |

#### Error handling

If the tool chain raises an exception, `route` catches it, logs a warning, and
returns `(None, intent, [])` — the LLM receives no context but is told the
original intent, giving it the opportunity to handle the error.

---

## Usage example

```python
from biomethane_router import route

# 'mcp' is the MCP tool server client
context, intent, tools = route("What is the current VFA level?", mcp)

print(intent)        # "SOURING"
print(tools)         # ["enkf_update"]

# context contains:
#   === ROUTER: SOURING chain ===
#   EnKF estimate:
#   { "souring_probability": 0.15, "S2_mean_mmol_per_L": 45.3, ... }
#   Souring probability (0.15) is in WATCH range ...
#   INSTRUCTIONS FOR LLM: ...
```

```python
# UNKNOWN example — LLM handles tool selection
context, intent, tools = route("How is the weather today?", mcp)
print(intent)        # "UNKNOWN"
print(context)       # None
```
