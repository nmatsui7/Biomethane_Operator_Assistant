# replay_dataset — API Reference

**Module**: `src/replay_dataset.py`

---

## Overview

Historical data replay pipeline that feeds CSV rows through the full **scada_mapper → StateBuffer → MCP → EnKF** stack one day at a time. Each row from the R1-FLEX dataset is mapped to internal variable names, inserted into StateBuffer (with CUSUM filtering), pushed to the MCP server via `update_plant_state`, then run through `enkf_update` and `check_alerts`. Results are accumulated into a structured JSON report.

### Pipeline per day

```
CSV row
  │
  ▼  scada_mapper       (column → internal variable name)
  ▼  StateBuffer.insert_live_data()  (CUSUM filter → plant_state.sqlite)
  ▼  MCP update_plant_state          (clean state → MCP server)
  ▼  MCP enkf_initialise/enkf_update (EnKF state estimation)
  ▼  MCP check_alerts                (alert status)
  │
  ▼  replay_result.json  (per-day record + summary)
```

### What it validates

- EnKF tracking of real biogas trends (X2, S2 vs actual output)
- `check_alerts` firing on days with repair events or outliers
- `souring_probability` rising before sustained declines (e.g. Oct 15–19)
- CUSUM correctly discarding instrument errors (e.g. 0°C temperature sensor)

---

## Module-level constants

| Constant | Value | Description |
|---|---|---|
| `PROJECT_ROOT` | `Path` (resolved) | Project root, found by looking for `pyproject.toml`, `setup.py`, `setup.cfg`, or `.git` anchors; falls back to `.env` `PROJECT_ROOT=` line, then parent directory |
| `VENV_PYTHON` | `str` | Path to virtual environment Python, from `VENV_PYTHON` env var or auto-detected `{PROJECT_ROOT}/.venv/bin/python`; falls back to `sys.executable` |
| `MCP_SERVER_SCRIPT` | `str` | Path to MCP server script, from `MCP_SERVER_SCRIPT` env var; defaults to `{PROJECT_ROOT}/src/bio_methane_operations_mcp_server_v5.py` |
| `DEFAULT_CSV` | `Path` | `{PROJECT_ROOT}/sample_data/ri_flex.csv` |
| `DEFAULT_BUFFER_DB` | `Path` | `{PROJECT_ROOT}/data/replay_buffer.sqlite` |
| `DEFAULT_OUTPUT` | `Path` | `{PROJECT_ROOT}/reports/replay_result.json` |
| `CATTLE_SLURRY_VS_PCT` | `float` | `6.4` — volatile solids percentage for cattle slurry, used for OLR estimation |

### OLR estimation

```python
vs_kg_day = 20.0 * (CATTLE_SLURRY_VS_PCT / 100.0)  # 1.28 kg VS/day
olr = vs_kg_day / digester_volume_m3
```

---

## Class `MCPClient` (lines 92–197)

A lightweight JSON-RPC 2.0 client that communicates with the MCP server over stdio. Spawns the MCP server as a subprocess and sends/receives JSON-RPC messages over stdin/stdout.

### `__init__()`

```python
def __init__(self)
```

Initialise client state. Sets `proc=None`, creates a `queue.Queue` for incoming responses and a `threading.Lock` for serialised writes.

#### Attributes

| Attribute | Type | Description |
|---|---|---|
| `proc` | `subprocess.Popen \| None` | MCP server process handle |
| `r_queue` | `queue.Queue` | Thread-safe response queue populated by `_read_loop` |
| `_lock` | `threading.Lock` | Serialises writes to stdin |
| `_req_id` | `int` | Monotonically incrementing request identifier |

---

### `start(lab_scale=False) -> bool`

```python
def start(self, lab_scale: bool = False) -> bool
```

Spawn the MCP server subprocess and initialise the JSON-RPC session.

#### Algorithm

1. Build `PYTHONPATH` with the venv's `site-packages` directory (for dependency resolution).
2. Launch `{VENV_PYTHON} {MCP_SERVER_SCRIPT} [--lab-scale]` with `stdin=PIPE, stdout=PIPE, stderr=PIPE`.
3. Start daemon threads:
   - `_stderr_reader` — drains stderr to prevent OS pipe buffer blocking.
   - `_read_loop` — reads stdout line by line, parses JSON, pushes to `r_queue`.
4. Sleep 1.5 s for server initialisation.
5. Send `initialize` JSON-RPC request with protocol version `2024-11-05`.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `lab_scale` | `bool` | `False` | Pass `--lab-scale` to the MCP server for lab-scale temperature thresholds (20–30 °C) |

#### Returns

`True` on success, `False` if the subprocess fails to start.

---

### `stop()`

```python
def stop(self)
```

Terminate the MCP server process.

---

### `call(tool, args, timeout=30) -> dict`

```python
def call(self, tool: str, args: dict, timeout: int = 30) -> dict
```

Call a tool on the MCP server via JSON-RPC and return the parsed response.

#### Algorithm

1. Build `tools/call` JSON-RPC payload with `name=tool`, `arguments=args`.
2. Send via `_send_recv` with the given timeout.
3. If timeout (no response), return `{"error": "timeout"}`.
4. If JSON-RPC error object present, return `{"error": "JSON-RPC {code}: {message}"}`.
5. Parse response `content`:
   - Single text block: attempt `json.loads()`, return parsed dict.
   - Multiple text blocks (e.g. `check_alerts` returning a list of alert dicts): parse each block, return single result if only one, otherwise return list.

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `tool` | `str` | — | MCP tool name (e.g. `"update_plant_state"`, `"enkf_update"`, `"check_alerts"`) |
| `args` | `dict` | — | Tool arguments as a JSON-compatible dict |
| `timeout` | `int` | `30` | Response wait timeout in seconds |

#### Returns

Parsed JSON response as a `dict` (or list of dicts for multi-content responses).

---

### `_next_id()`

```python
def _next_id(self) -> int
```

Increment and return the next JSON-RPC request ID. Starts at 0, first call returns 1.

---

### `_read_loop()`

```python
def _read_loop(self)
```

Background thread target. Reads stdout line by line, parses JSON, pushes to `r_queue`. Runs until the subprocess exits (pipe closes).

---

### `_stderr_reader()`

```python
def _stderr_reader(self)
```

Background thread target. Drains stderr to prevent OS pipe buffer deadlock. Output is silently discarded (not logged to stdout).

---

### `_send(payload)`

```python
def _send(self, payload: dict)
```

Thread-safe: acquire `_lock`, write `json.dumps(payload) + "\n"` to stdin, flush.

---

### `_recv(timeout=30)`

```python
def _recv(self, timeout: int = 30) -> dict | None
```

Blocking read from `r_queue` with timeout. Returns `None` on timeout.

---

### `_send_recv(payload, timeout=30)`

```python
def _send_recv(self, payload: dict, timeout: int = 30) -> dict | None
```

Send a JSON-RPC payload and wait for the response. Calls `_send()` then `_recv()`.

---

## Functions

### `load_and_map(csv_path) -> (df, mapping, date_col)`

```python
def load_and_map(csv_path: Path) -> Tuple[pd.DataFrame, dict, str]
```

Load a SCADA CSV file, auto-detect the vendor, build a column mapping, and flag outlier rows.

#### Algorithm

1. Call `scada_mapper.read_file()` to load the CSV (with encoding fallback).
2. Call `scada_mapper.detect_scada_vendor()` to identify the SCADA vendor.
3. Call `scada_mapper.auto_map_dataframe()` to build `{internal: actual}` mapping.
4. Use the first column as the date column; parse as datetime.
5. Extract raw biogas and temperature values using mapped column names.
6. Flag outlier rows: `biogas_raw < 0`, `biogas_raw > 5000`, `temp_raw < 5`, `temp_raw > 50`.
7. Extract a `Comment` column if present.
8. Sort by date and reset index.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `csv_path` | `Path` | Path to the SCADA CSV file |

#### Returns

| Return | Type | Description |
|---|---|---|
| `df` | `pd.DataFrame` | Augmented DataFrame with columns `biogas_raw`, `temp_raw`, `is_outlier`, `comment` |
| `mapping` | `dict` | `{internal_variable_name: actual_column_name}` |
| `date_col` | `str` | Name of the date column (always `df.columns[0]`) |

#### Outlier detection rules

| Condition | Flagged as outlier |
|---|---|
| `biogas_raw < 0` | Yes |
| `biogas_raw > 5000` | Yes |
| `temp_raw < 5` | Yes |
| `temp_raw > 50` | Yes |

Outlier rows are **kept** in the replay but flagged in the output record.

---

### `replay_day(row, mapping, buf, mcp, enkf_initialised, args) -> tuple[dict, bool]`

```python
def replay_day(row: dict, mapping: dict, buf: StateBuffer,
               mcp: MCPClient, enkf_initialised: bool,
               args) -> Tuple[dict, bool]
```

Run one day of the replay pipeline. Returns a result record and the updated `enkf_initialised` flag.

#### Pipeline steps

1. **Build mapped row** — Convert CSV row values to float, apply scale conversion (`mL/day → Nm³/h` via `/ 24_000_000` for `biogas_flow_nm3h`).
2. **StateBuffer.insert_live_data()** — Insert mapped values into StateBuffer with historical timestamp (enables CUSUM filtering on historical data). Falls back to current time on parse failure.
3. **Read back clean state** — Call `buf.get_model_dataframe()`, read the last row (latest clean state after CUSUM).
4. **MCP update_plant_state** — Push `digester_temp_c`, `biogas_flow_nm3h`, `ch4_pct` (if available) to the MCP server.
5. **MCP check_alerts** — Retrieve alert status. Multi-severity aggregation:
   - `HIGH`/`CRITICAL` → `"CRITICAL"`
   - `MEDIUM` → `"WARNING"`
   - Otherwise → `"INFO"`
   - No alerts → `"OK"`
6. **MCP EnKF** (skipped if `args.no_enkf`):
   - First day only: `enkf_initialise` with digester geometry, HRT, S1_in, ensemble size, reference temperature.
   - Each day: `enkf_update` with optional `fos_mg_per_l` lab value. Records `S2_mean`, `X2_mean`, `souring_probability`, `risk_level`.
   - If `"not available"` in error, disables EnKF for all remaining days.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `row` | `dict` | Single CSV row as a dict (with `date`, `biogas_raw`, `temp_raw`, `is_outlier`, `comment`) |
| `mapping` | `dict` | Column mapping from `load_and_map` |
| `buf` | `StateBuffer` | Initialised StateBuffer instance |
| `mcp` | `MCPClient` | Connected MCP client |
| `enkf_initialised` | `bool` | Whether EnKF has been initialised this run |
| `args` | `argparse.Namespace` | CLI args (`.dry_run`, `.no_enkf`, `.digester_volume`, `.hrt_days`) |

#### Returns

| Return | Type | Description |
|---|---|---|
| `result` | `dict` | Per-day record (see schema below) |
| `enkf_initialised` | `bool` | Updated flag (may flip to `True` after first init) |

#### Per-day record schema

```python
{
    "date":              str,       # "2024-06-01"
    "actual_biogas_mL":  float,     # Raw biogas in mL/day (or None)
    "actual_temp_c":     float,     # Raw temperature in °C (or None)
    "is_outlier":        bool,      # Flagged as instrument outlier
    "comment":           str|None,  # CSV comment, if any
    "cusum_passed":      dict,      # Clean values after CUSUM filter
    "enkf_S2_mean":      float,     # EnKF S2 state estimate (or None)
    "enkf_X2_mean":      float,     # EnKF X2 state estimate (or None)
    "enkf_souring_prob": float,     # Souring probability 0-1 (or None)
    "enkf_risk_level":   str,       # "LOW", "MEDIUM", "HIGH", "CRITICAL" (or None)
    "alert_status":      str,       # "OK", "INFO", "WARNING", "CRITICAL" (or None)
    "errors":            list,      # Error messages from MCP calls
    "elapsed_s":         float,     # Wall-clock seconds for this day
}
```

---

### `compute_summary(records) -> dict`

```python
def compute_summary(records: list) -> dict
```

Aggregate per-day records into a summary statistics dictionary.

#### Computation rules

| Statistic | Rule |
|---|---|
| `days_processed` | `len(records)` |
| `days_clean` | Records where `is_outlier == False` and `actual_biogas_mL` is truthy |
| `days_outlier` | Records where `is_outlier == True` |
| `days_with_alerts` | Records with `alert_status` in `("WARNING", "CRITICAL")` |
| `souring_events` | Records with `enkf_souring_prob > 0.3` |
| `repair_events_in_log` | Records where `comment` contains `"repair"` or `"repar"` (case-insensitive) |
| `biogas_mean_mL` | Mean of `actual_biogas_mL` across clean records |
| `biogas_min_mL` | Minimum of clean biogas values |
| `biogas_max_mL` | Maximum of clean biogas values |
| `alert_dates` | List of dates with active alerts |
| `souring_dates` | List of dates with souring probability > 0.3 |
| `repair_dates` | List of dates with repair comments |
| `outlier_dates` | List of outlier dates |

#### Returns

```python
{
    "days_processed":       int,
    "days_clean":           int,
    "days_outlier":         int,
    "days_with_alerts":     int,
    "souring_events":       int,
    "repair_events_in_log": int,
    "biogas_mean_mL":       float,   # or None
    "biogas_min_mL":        float,   # or None
    "biogas_max_mL":        float,   # or None
    "alert_dates":          [str],
    "souring_dates":        [str],
    "repair_dates":         [str],
    "outlier_dates":        [str],
}
```

---

## CLI entry point `main()`

```python
python src/replay_dataset.py [options]
```

### Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--csv` | `Path` | `DEFAULT_CSV` | Path to input CSV file |
| `--buffer-db` | `Path` | `DEFAULT_BUFFER_DB` | Path to StateBuffer SQLite database |
| `--output` | `Path` | `DEFAULT_OUTPUT` | Path for output JSON report |
| `--start` | `str` | `None` | Start date `YYYY-MM-DD` (inclusive) |
| `--end` | `str` | `None` | End date `YYYY-MM-DD` (inclusive) |
| `--digester-volume` | `float` | `1.0` | Digester volume in m³ (estimated default for R1-FLEX) |
| `--hrt-days` | `float` | `17.0` | Hydraulic retention time in days (estimated default for R1-FLEX) |
| `--no-enkf` | flag | `False` | Skip EnKF initialisation and updates (faster, useful for alert-only testing) |
| `--dry-run` | flag | `False` | Show column mapping and configuration only; no MCP calls, DB writes, or output file |
| `--lab-scale` | flag | `False` | Pass `--lab-scale` to MCP server for lab-scale temperature thresholds |

### Behaviour

1. Resolve and validate the CSV path.
2. Load and map columns via `load_and_map()`.
3. Filter by `--start`/`--end` date range if provided.
4. Compute estimated OLR from `CATTLE_SLURRY_VS_PCT`.
5. If `--dry-run`, print mapping and exit.
6. Initialise `StateBuffer` with 48-hour retention (historical timestamps purge correctly).
7. Start MCP server via `MCPClient.start()`.
8. Iterate over CSV rows day by day:
   - Call `replay_day()` for each row.
   - Print progress line with date, biogas, temperature, outlier tag, comment.
   - Print result status (alert level, EnKF risk, error count).
9. Stop MCP server, close StateBuffer.
10. Compute summary via `compute_summary()`.
11. Write structured JSON report to `--output`.

---

## Output JSON schema

The output file is a single JSON object with three top-level keys:

### `meta`

```python
{
    "reactor":             "R1-FLEX",
    "generated_at":        str,     # ISO 8601 UTC timestamp
    "csv_source":          str,     # Path to input CSV
    "buffer_db":           str,     # Path to buffer database
    "days":                int,     # Total days replayed
    "digester_volume_m3":  float,
    "hrt_days":            float,
    "olr_estimated":       float,   # kg VS/m³/day
}
```

### `summary`

See `compute_summary()` return schema above.

### `records`

Array of per-day record dicts (see `replay_day()` return schema above).

---

## Unit conversions

### mL/day → Nm³/h

The R1-FLEX dataset records biogas production in **mL/day** (lab-scale, ~860 mL/day peak). The MCP system expects **Nm³/h** (industrial-scale, ~142 Nm³/h). Conversion is applied at two points:

```python
# In replay_day() and ingest_csv_to_buffer():
if internal == "biogas_flow_nm3h":
    val = val / 24_000_000  # mL/day → Nm³/h
```

Derivation:

```
x mL/day ÷ 1,000,000 (mL → m³) ÷ 24 (day → h) = x / 24,000,000 Nm³/h
```

---

## Usage examples

```bash
# Full year replay (default)
python src/replay_dataset.py

# Date range
python src/replay_dataset.py --start 2024-06-01 --end 2024-09-30

# Custom geometry
python src/replay_dataset.py --digester-volume 1.2 --hrt-days 20

# Skip EnKF (faster, alert-only testing)
python src/replay_dataset.py --no-enkf

# Dry run — no MCP/DB writes, only show column mapping
python src/replay_dataset.py --dry-run

# Custom output path
python src/replay_dataset.py --output reports/my_replay.json

# Lab-scale temperature thresholds
python src/replay_dataset.py --lab-scale
```
