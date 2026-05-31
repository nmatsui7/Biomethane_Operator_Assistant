# StateBuffer — API Reference

**Version:** 1.0.0  
**Module:** `src/StateBuffer.py`  
**Dependencies:** `sqlite3`, `pandas`, `json`, `pathlib`

---

## Overview

`StateBuffer` is a **SQLite-backed rolling buffer** for biogas plant state data. It sits between the SCADA mapping layer and the MCP inference layer:

1. **scada_mapper** translates vendor-specific SCADA tags into internal variable names.
2. **StateBuffer** ingests those mapped readings, filters anomalies with a **CUSUM control chart**, stores the rolling 48‑hour window, and exposes a clean DataFrame for downstream consumers.
3. **MCP tools** (`get_plant_state`, `get_state_buffer_status`, etc.) read from this buffer.

```
SCADA → scada_mapper.auto_map_dataframe() → StateBuffer.insert_live_data()
                                                │
                                          ┌─────▼──────┐
                                          │  CUSUM     │  spike? → discard
                                          │  filter    │  pass? → store
                                          └─────┬──────┘
                                                │
                          ┌─────────────────────┼─────────────────────┐
                          │                     │                     │
                    get_model_dataframe()  get_latest()  get_cusum_status()
                          │                     │
                     ┌────▼────┐          ┌─────▼─────┐
                     │ EnKF S2 │          │ MCP tools │
                     │ AD4 sim │          │ alerts    │
                     └─────────┘          └───────────┘
```

---

## Module-Level Constants

### `DEFAULT_CUSUM_PARAMS`

```python
DEFAULT_CUSUM_PARAMS: Dict[str, Dict[str, float]]
```

Default CUSUM parameters for an **industrial-scale** mesophilic digester (~6.0 m³, ~142 Nm³/h biogas). Five sensors monitored:

| Variable | `K` (allowance) | `H` (threshold) | Interpretation |
|---|---|---|---|
| `digester_temp_c` | 0.5 | 2.0 | ±2 °C from rolling mean |
| `digester_ph` | 0.1 | 0.5 | ±0.5 pH units |
| `biogas_flow_nm3h` | 10.0 | 50.0 | ±50 Nm³/h |
| `ch4_pct` | 1.0 | 5.0 | ±5 % CH₄ |
| `h2s_ppm` | 20.0 | 100.0 | ±100 ppm H₂S |

`K` is the allowance / slack — how much deviation is tolerated before the CUSUM accumulator starts building. `H` is the decision threshold — when the accumulator exceeds `H` the reading is rejected as a spike.

### `LAB_SCALE_CUSUM_PARAMS`

```python
LAB_SCALE_CUSUM_PARAMS: Dict[str, Dict[str, float]]
```

Same sensor set with `biogas_flow_nm3h` scaled for a **lab-scale** digester (~1.0 m³, ~0.00003 Nm³/h = 30 mL/h). Temperature, pH, CH₄, and H₂S tolerances are unchanged.

| Variable | `K` | `H` |
|---|---|---|
| `digester_temp_c` | 0.5 | 2.0 |
| `digester_ph` | 0.1 | 0.5 |
| `biogas_flow_nm3h` | 0.00001 | 0.00005 |
| `ch4_pct` | 1.0 | 5.0 |
| `h2s_ppm` | 20.0 | 100.0 |

### `PLANT_STATE_COLS`

```python
PLANT_STATE_COLS: list[str]
```

The ten column names present in the `plant_state` SQLite table, in order:

```python
[
    "digester_temp_c",
    "digester_ph",
    "vfa_mmol_l",
    "biogas_flow_nm3h",
    "ch4_pct",
    "h2s_ppm",
    "biomethane_purity_pct",
    "organic_load_kg_vs_d",
    "hydraulic_retention_days",
    "fos_mg_per_l",
]
```

---

## Class `StateBuffer`

```python
class StateBuffer:
```

---

### `__init__`

```python
def __init__(
    self,
    db_path: str = "plant_state.sqlite",
    retention_hours: int = 48,
    cusum_params: Optional[Dict[str, Dict[str, float]]] = None,
    lab_scale: bool = False,
)
```

Instantiate a new buffer. Opens (or creates) the SQLite database and initialises the schema.

| Parameter | Default | Description |
|---|---|---|
| `db_path` | `"plant_state.sqlite"` | Path to the SQLite file. Use `":memory:"` for testing. |
| `retention_hours` | `48` | Records older than this are purged on every `insert_live_data` call. |
| `cusum_params` | `None` | Per-variable `{K, H}` dict. Falls back to `DEFAULT_CUSUM_PARAMS` (or `LAB_SCALE_CUSUM_PARAMS` if `lab_scale=True`). |
| `lab_scale` | `False` | If `True`, use `LAB_SCALE_CUSUM_PARAMS` as the base set. |

**Note:** If `cusum_params` is provided it is shallow-merged over the base defaults using `{**DEFAULT_CUSUM_PARAMS, **cusum_params}`, so you only need to specify overrides.

---

### `from_config` (classmethod)

```python
@classmethod
def from_config(cls, config_path: str, lab_scale: bool = False) -> "StateBuffer"
```

Alternative constructor that reads connection parameters from a **`site_config.json`** file.

| Parameter | Description |
|---|---|
| `config_path` | Path to a JSON file (see format below). |
| `lab_scale` | Overrides the `lab_scale` field in the JSON if provided. |

Raises `FileNotFoundError` if the config path does not exist.

**`site_config.json` format:**

```json
{
    "db_path": "data/plant_state.sqlite",
    "retention_hours": 48,
    "lab_scale": false,
    "cusum_params": {
        "digester_temp_c":    {"K": 0.5,  "H": 2.0},
        "digester_ph":        {"K": 0.1,  "H": 0.5},
        "biogas_flow_nm3h":   {"K": 10.0, "H": 50.0},
        "ch4_pct":            {"K": 1.0,  "H": 5.0},
        "h2s_ppm":            {"K": 20.0, "H": 100.0}
    }
}
```

All keys are optional — missing keys fall back to the defaults described above.

---

### `insert_live_data`

```python
def insert_live_data(self, mapped_data: Dict[str, float], timestamp: Optional[float] = None)
```

Ingest a pre-mapped sensor reading. **Must** use internal variable names (the output of `scada_mapper.auto_map_dataframe`), not raw SCADA tags.

| Parameter | Description |
|---|---|
| `mapped_data` | Dictionary mapping internal variable names to their values. Only keys present in `PLANT_STATE_COLS` are CUSUM-filtered and persisted; others are silently ignored. |
| `timestamp` | Unix timestamp for the reading. If `None`, `time.time()` is used. Pass this explicitly for **replay scenarios** so the retention window works correctly. |

**Internal flow:**
1. Store the raw `payload` dict (as JSON) in the `raw_telemetry` table — this is the audit trail.
2. For each key that appears in both `mapped_data` and `PLANT_STATE_COLS`, run `_apply_cusum()`. Spikes become `None`.
3. Write the CUSUM-filtered row into `plant_state` (rejected sensors get `NULL`).
4. Purge records older than `retention_seconds`.

**Example:**

```python
mapping = auto_map_dataframe(df, vendor)   # scada_mapper
buf.insert_live_data(
    {
        "digester_temp_c": 36.8,
        "ch4_pct": 62.1,
        "biogas_flow_nm3h": 45.2,
    },
    timestamp=pd.Timestamp("2024-01-15").timestamp(),
)
```

---

### `insert_manual_fostac`

```python
def insert_manual_fostac(self, fos_mg_per_l: float)
```

Record a manual FOS/TAC titration result (weekly lab measurement). Stored as a sparse row — all other columns are `NULL`. **No CUSUM is applied** — lab values are assumed accurate and are used directly by the EnKF to tighten the S2 estimate.

| Parameter | Description |
|---|---|
| `fos_mg_per_l` | FOS/TAC result in mg CaCO₃/L. |

Uses `time.time()` for the timestamp.

---

### `get_model_dataframe`

```python
def get_model_dataframe(self) -> pd.DataFrame
```

Return the full `plant_state` history as a **Pandas DataFrame** for downstream consumers (EnKF S2, AD4 simulator).

| Aspect | Behaviour |
|---|---|
| **Index** | UTC datetime (converted from Unix seconds via `pd.to_datetime(unit="s", utc=True)`) |
| **Columns** | All `PLANT_STATE_COLS` in order |
| **Missing values** | Forward-filled (`df.ffill()` — EnKF tolerates missing readings) |
| **Empty buffer** | Returns an empty DataFrame (not `None`) |

---

### `get_latest`

```python
def get_latest(self) -> Optional[Dict[str, Optional[float]]]
```

Return the most recent clean plant state row as `{column: value}`, or `None` if the buffer is empty. Useful for the `get_plant_state` MCP tool.

`None` values are preserved for columns with no recent reading.

---

### `get_cusum_status`

```python
def get_cusum_status(self) -> Dict[str, Dict]
```

Return current CUSUM accumulator state for every monitored variable. Useful for diagnostics — high `s_pos` or `s_neg` values indicate a sensor drifting toward the anomaly threshold.

**Return structure:**

```python
{
    "digester_temp_c": {
        "s_pos": 0.72,        # Current positive CUSUM accumulator
        "s_neg": 0.0,         # Current negative CUSUM accumulator
        "rolling_mean": 36.8, # EMA of accepted readings (alpha=0.05)
        "H": 2.0,             # Decision threshold for this variable
    },
    ...
}
```

---

### `close`

```python
def close(self)
```

Close the SQLite connection. Call this when shutting down to release the file lock and flush WAL.

---

## Private Methods

### `_initialize_db`

```python
def _initialize_db(self)
```

Called once from `__init__`. Creates three tables (if they do not exist) and seeds the `cusum_state` rows for each variable in `self.cusum_params`.

Pragmas set:
- `journal_mode = WAL` — supports concurrent reads from MCP tool calls.
- `synchronous = NORMAL` — balances durability and write performance.

### `_apply_cusum`

```python
def _apply_cusum(
    self,
    model_tag: str,
    current_val: float,
    cursor: sqlite3.Cursor,
) -> Optional[float]
```

Apply a **one-sided CUSUM control chart** to a single sensor reading.

| Return value | Meaning |
|---|---|
| `current_val` | Reading accepted — within tolerance |
| `None` | Spike detected — reading discarded |

**Algorithm:**

Let `x` = `current_val`, `μ` = `rolling_mean`, `K` = allowance, `H` = threshold.

```
S⁺ = max(0, S⁺ + x − (μ + K))     # detects upward shifts
S⁻ = max(0, S⁻ + (μ − K) − x)     # detects downward shifts

if S⁺ > H or S⁻ > H → anomaly, reset both accumulators, discard reading
else → accept reading, update rolling mean via EMA:
      μ ← 0.05 × x + 0.95 × μ
```

- The **rolling mean** is initialised to the first accepted value.
- Variables **not** in `cusum_params` (e.g. `fos_mg_per_l`) pass through unchanged.
- If a tag appears that was not seeded at init time, a new `cusum_state` row is inserted on the fly.

### `_purge_old_records`

```python
def _purge_old_records(self, current_ts: int, cursor: sqlite3.Cursor)
```

Delete records from both `raw_telemetry` and `plant_state` where `ts_utc < current_ts − retention_seconds`. Called at the end of every `insert_live_data` call.

---

## Database Schema

### Table `raw_telemetry`

Audit trail of all raw payloads received.

| Column | Type | Description |
|---|---|---|
| `ts_utc` | `INTEGER PRIMARY KEY` | Unix timestamp |
| `payload` | `TEXT` | JSON dump of the original `mapped_data` dict |

### Table `plant_state`

CUSUM-filtered plant state rows. The primary record set consumed by MCP, EnKF, and AD4.

| Column | Type | Description |
|---|---|---|
| `ts_utc` | `INTEGER PRIMARY KEY` | Unix timestamp |
| `digester_temp_c` | `REAL` | Digester temperature (°C) |
| `digester_ph` | `REAL` | pH |
| `vfa_mmol_l` | `REAL` | Volatile fatty acids (mmol/L) |
| `biogas_flow_nm3h` | `REAL` | Biogas flow (Nm³/h) |
| `ch4_pct` | `REAL` | Methane concentration (%) |
| `h2s_ppm` | `REAL` | Hydrogen sulphide (ppm) |
| `biomethane_purity_pct` | `REAL` | Upgraded biomethane purity (%) |
| `organic_load_kg_vs_d` | `REAL` | Organic loading rate (kg VS/d) |
| `hydraulic_retention_days` | `REAL` | Hydraulic retention time (days) |
| `fos_mg_per_l` | `REAL` | FOS/TAC titration (mg CaCO₃/L) |

Any column may be `NULL` (sensor absent or CUSUM-rejected at that timestamp).

### Table `cusum_state`

Per-sensor CUSUM accumulator state, persisted so the filter survives a restart.

| Column | Type | Default | Description |
|---|---|---|---|
| `model_tag` | `TEXT PRIMARY KEY` | — | Internal variable name |
| `s_pos` | `REAL` | `0.0` | Positive CUSUM accumulator |
| `s_neg` | `REAL` | `0.0` | Negative CUSUM accumulator |
| `rolling_mean` | `REAL` | `NULL` | EMA of accepted readings |

### Table `ingest_log` (added by `ingest_daemon`)

Created externally by the ingest daemon — not part of `_initialize_db`.

| Column | Type | Description |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | Row ID |
| `ts_utc` | `INTEGER` | Ingest timestamp |
| `file_name` | `TEXT` | Source SCADA file |
| `status` | `TEXT` | `"ok"` or `"error"` |
| `message` | `TEXT` | Error details if failed |

---

## CUSUM Algorithm — Detailed Description

The implementation uses a **one-sided** (dual-accumulator) CUSUM control chart:

```
              ┌──────────────────────┐
              │     new reading x     │
              └──────────┬───────────┘
                         │
              ┌──────────▼───────────┐
              │  μ = rolling_mean    │
              │  K = allowance       │
              │  H = threshold       │
              └──────────┬───────────┘
                         │
              ┌──────────▼───────────┐
    ┌─────────┤  S⁺ += x − (μ + K)  ├──────────┐
    │         │  S⁻ += (μ − K) − x  │          │
    │         └──────────┬───────────┘          │
    │                    │                      │
    ▼                    ▼                      ▼
 S⁺ > H or S⁻ > H?    No → accept          Tag not in
    │                                       params?
    Yes                                     │
    │                                       ▼
    ▼                                  pass through
 discard x                             unchanged
 reset S⁺, S⁻
```

**Key properties:**

- **One-sided:** separate accumulators for positive and negative shifts. This is appropriate because a sudden *drop* in biogas flow (blockage) and a sudden *surge* (sensor fault) are both meaningful anomalies.
- **Memoryless reset:** after a spike both accumulators go to zero; the rolling mean is **not** updated.
- **EMA baseline:** `μ = 0.05 × x + 0.95 × μ` on accepted readings (α = 0.05), giving a slowly adapting target that follows diurnal trends without reacting to short bursts of noise.
- **First-reading init:** the first reading for any variable initialises `μ` directly and is always accepted.

---

## Usage Flow

### Standard pipeline

```python
from src.StateBuffer import StateBuffer
from src.scada_mapper import auto_map_dataframe

# 1. Load a SCADA CSV and map tags to internal names
df = pd.read_csv("scada_export.csv", parse_dates=["timestamp"])
mapping = auto_map_dataframe(df, vendor="siemens")

# 2. Create the buffer (from config, or with inline params)
buf = StateBuffer(
    db_path="data/plant_state.sqlite",
    retention_hours=48,
    lab_scale=True,          # lab-scale digester
)

# 3. Insert each row
for ts, row in mapping.iterrows():
    buf.insert_live_data(row.to_dict(), timestamp=ts.timestamp())

# 4. Serve clean data to MCP / EnKF
df_clean = buf.get_model_dataframe()
latest = buf.get_latest()
status = buf.get_cusum_status()

# 5. Shut down
buf.close()
```

### With `from_config`

```python
buf = StateBuffer.from_config("site_config.json", lab_scale=False)
```

### MCP integration

The MCP server (`bio_methane_operations_mcp_server_v5.py`) calls:

- `buf.insert_live_data(mapped)` inside the `ingest_scada_file` tool.
- `buf.insert_manual_fostac(value)` inside the `record_fostac` tool.
- `buf.get_latest()` inside the `get_plant_state` tool.
- `buf.get_model_dataframe()` inside the `enkf_update` tool.
- `buf.get_cusum_status()` inside the `get_state_buffer_status` tool.

---

## `site_config.json` — Full Reference

```json
{
    "db_path": "data/plant_state.sqlite",
    "retention_hours": 48,
    "lab_scale": false,
    "cusum_params": {
        "digester_temp_c":    {"K": 1.0,  "H": 3.0},
        "digester_ph":        {"K": 0.1,  "H": 0.5},
        "biogas_flow_nm3h":   {"K": 10.0, "H": 50.0},
        "ch4_pct":            {"K": 1.0,  "H": 5.0},
        "h2s_ppm":            {"K": 20.0, "H": 100.0}
    }
}
```

| Key | Type | Default | Description |
|---|---|---|---|
| `db_path` | `str` | `"plant_state.sqlite"` | SQLite database file path |
| `retention_hours` | `int` | `48` | Rolling window size in hours |
| `lab_scale` | `bool` | `false` | Switch to `LAB_SCALE_CUSUM_PARAMS` for biogas flow |
| `cusum_params` | `object` | `DEFAULT_CUSUM_PARAMS` | Per-sensor `{K, H}` overrides |

Only sensors with an entry in `cusum_params` are CUSUM-filtered. Any sensor listed in `PLANT_STATE_COLS` but absent from `cusum_params` passes through unfiltered.

---

## Notes

- **Thread safety:** The connection uses `check_same_thread=False`. WAL mode allows concurrent readers (e.g. MCP tools calling `get_model_dataframe`) while a writer is active.
- **Memory mode:** Use `db_path=":memory:"` for unit tests. The schema is re-created on every instantiation.
- **Precision:** Accumulators and rolling means are stored as `REAL` (IEEE 64-bit float). Values in the `get_cusum_status` return are rounded to 4 decimal places.
- **Forward fill:** `get_model_dataframe` applies `df.ffill()` so the EnKF never sees `NaN` between sensor scans. If the buffer is completely empty the returned DataFrame has no rows.
