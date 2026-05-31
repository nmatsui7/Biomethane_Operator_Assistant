# ingest_daemon — API Reference

**Module**: `src/ingest_daemon.py`

---

## Overview

24×7 SCADA file ingestion daemon designed to run via cron. Watches a network drive or local mount point for new or modified SCADA export files, runs each through `scada_mapper → StateBuffer`, and logs results in an `ingest_log` table. Idempotent: files already ingested are skipped by comparing `(path, mtime, size)`.

Designed for reliability:
- Network drive offline → clean exit with logged warning (no crash, no DB corruption).
- File parse failure → logged error, file marked in `ingest_log`, processing continues.
- Thread-safe — StateBuffer uses SQLite WAL mode, multiple cron instances cannot corrupt the DB.
- Live-appended files are re-ingested on mtime/size change.

### Cron setup (every 5 minutes)

```bash
*/5 * * * * cd /path/to/project && /path/to/venv/bin/python src/ingest_daemon.py >> logs/ingest.log 2>&1
```

### Design notes

- Uses `ingest_log` table in `plant_state.sqlite` to track processed files by `(path, mtime, size)`.
- Re-ingests if mtime or size changes — handles live-appended historian files.
- CUSUM filtering applied by StateBuffer — spikes silently discarded, raw payloads preserved in `raw_telemetry`.
- Network drive offline: catches `OSError`/`PermissionError` and logs a warning. MCP server continues serving last good state.

---

## Module-level constants

| Constant | Value | Description |
|---|---|---|
| `PROJECT_ROOT` | `Path` (resolved) | Project root via anchor detection (`pyproject.toml`, `setup.py`, `setup.cfg`, `.git`) |
| `DEFAULT_WATCH_DIR` | `str` | `{PROJECT_ROOT}/scada_exports` |
| `DEFAULT_FILE_PATTERN` | `str` | `"*.csv"` |
| `DEFAULT_LOOKBACK_MINUTES` | `int` | `15` — only scan files modified in the last 15 minutes |
| `DEFAULT_POLL_SUBFOLDERS` | `bool` | `False` |
| `DEFAULT_BIOGAS_SCALE` | `float` | `1.0` — scale factor for `biogas_flow_nm3h`; set to `1/24_000_000` for lab-scale mL/day data |
| `SITE_CONFIG` | `dict` | Loaded from `site_config.json` (searched `PROJECT_ROOT` then `PROJECT_ROOT/src/`) |
| `_DAEMON_CFG` | `dict` | `SITE_CONFIG.get("ingest_daemon", {})` — runtime defaults overrides |

---

## Functions

### `_ensure_ingest_log(db_path)`

```python
def _ensure_ingest_log(db_path: str)
```

Create the `ingest_log` tracking table in the StateBuffer database if it does not exist.

#### `ingest_log` table schema

```sql
CREATE TABLE IF NOT EXISTS ingest_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path      TEXT NOT NULL,
    file_mtime     REAL NOT NULL,
    file_size      INTEGER NOT NULL,
    ingested_at    TEXT NOT NULL,
    rows_accepted  INTEGER,
    rows_rejected  INTEGER,
    error_msg      TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingest_log_path ON ingest_log(file_path);
```

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `db_path` | `str` | Path to the StateBuffer SQLite database |

---

### `_already_ingested(db_path, file_path, mtime, size) -> bool`

```python
def _already_ingested(db_path: str, file_path: str, mtime: float, size: int) -> bool
```

Check whether the exact same file version has been ingested before.

#### Deduplication key

`(file_path, file_mtime, file_size)` — all three must match. If a file is modified (new mtime or size), it is considered new and re-ingested.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `db_path` | `str` | Path to the StateBuffer SQLite database |
| `file_path` | `str` | Absolute path to the file |
| `mtime` | `float` | File modification timestamp (seconds since epoch) |
| `size` | `int` | File size in bytes |

#### Returns

`True` if an `ingest_log` row exists with all three values matching.

---

### `_log_ingest(db_path, file_path, mtime, size, rows_accepted, rows_rejected, error_msg=None)`

```python
def _log_ingest(db_path: str, file_path: str, mtime: float, size: int,
                rows_accepted: int, rows_rejected: int, error_msg: Optional[str] = None)
```

Record a file ingestion attempt in the `ingest_log` table.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `db_path` | `str` | Path to the StateBuffer SQLite database |
| `file_path` | `str` | Absolute path to the file |
| `mtime` | `float` | File modification timestamp |
| `size` | `int` | File size in bytes |
| `rows_accepted` | `int` | Number of rows successfully ingested |
| `rows_rejected` | `int` | Number of rows skipped (no mappable values) |
| `error_msg` | `Optional[str]` | Error message if ingestion failed |

---

### `find_new_files(watch_dir, pattern, lookback_minutes, poll_subfolders) -> List[Tuple[Path, float, int]]`

```python
def find_new_files(
    watch_dir: Path,
    pattern: str,
    lookback_minutes: int,
    poll_subfolders: bool,
) -> List[Tuple[Path, float, int]]
```

Scan the watch directory for files matching the glob pattern that were modified within the lookback window.

#### Algorithm

1. Compute cutoff timestamp: `time.time() - lookback_minutes * 60`.
2. Use `watch_dir.glob(pattern)` or `watch_dir.rglob(pattern)` depending on `poll_subfolders`.
3. For each matching file, call `p.stat()`. If `st_mtime >= cutoff`, add `(path, mtime, size)` to results.
4. Files that raise `OSError` during stat are silently skipped.
5. If the watch directory itself raises `OSError`, log a warning and return empty list (network drive offline scenario).

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `watch_dir` | `Path` | Directory to scan |
| `pattern` | `str` | Glob pattern (e.g. `"*.csv"`, `"export_*.xlsx"`) |
| `lookback_minutes` | `int` | Only consider files modified within this many minutes |
| `poll_subfolders` | `bool` | If `True`, use recursive `rglob` instead of flat `glob` |

#### Returns

List of `(path, mtime, size)` tuples sorted alphabetically by path. Empty list if directory is inaccessible.

---

### `ingest_file(file_path, buf, biogas_scale) -> Tuple[int, int]`

```python
def ingest_file(
    file_path: Path,
    buf,                   # StateBuffer instance
    biogas_scale: float,
) -> Tuple[int, int]
```

Run one file through the `scada_mapper → StateBuffer` pipeline.

#### Algorithm

1. **Read file** — `scada_mapper.read_file()` (CSV or Excel, with encoding fallback).
2. **Detect vendor** — `scada_mapper.detect_scada_vendor()`.
3. **Auto-map columns** — `scada_mapper.auto_map_dataframe()`.
4. **If no mapping found**, skip the file entirely (`accepted=0, rejected=len(df)`).
5. **Detect timestamp column** — try the first 3 columns, parse as datetime; the first column with >50% successful parses is used as the timestamp source.
6. **For each row**:
   - Convert string values to `float`, strip `"kg"` suffix.
   - Apply scale factor if `biogas_scale != 1.0`: `val = val * biogas_scale`.
   - If no mappable values, count as rejected.
   - Insert via `buf.insert_live_data(mapped_row, timestamp=ts)`.
   - Use historical timestamp from the detected timestamp column if available; otherwise `None` (current time).

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `file_path` | `Path` | Path to the SCADA export file |
| `buf` | `StateBuffer` | Initialised StateBuffer instance |
| `biogas_scale` | `float` | Multiplicative scale factor for `biogas_flow_nm3h` values |

#### Returns

`Tuple[int, int]` — `(rows_accepted, rows_rejected)`.

---

### `run(args)`

```python
def run(args)
```

Main daemon execution: discover new files, ingest each, log results, and run a post-ingest CUSUM threshold check.

#### Algorithm

1. **Resolve paths and config** — Merge CLI args with `_DAEMON_CFG` and defaults:
   - `watch_dir` from `--watch` → `_DAEMON_CFG["watch_dir"]` → `DEFAULT_WATCH_DIR`
   - `pattern` from `--pattern` → `_DAEMON_CFG["file_pattern"]` → `DEFAULT_FILE_PATTERN`
   - `lookback` from `--lookback` → `_DAEMON_CFG["lookback_minutes"]` → `DEFAULT_LOOKBACK_MINUTES`
   - `poll_subfolders` from `--subfolders` → `_DAEMON_CFG["poll_subfolders"]` → `DEFAULT_POLL_SUBFOLDERS`
   - `biogas_scale` from `_DAEMON_CFG["biogas_scale_factor"]` → `DEFAULT_BIOGAS_SCALE`
   - `buffer_db_path` from `SITE_CONFIG["db_path"]` → `{PROJECT_ROOT}/data/plant_state.sqlite`
2. **Verify watch_dir** — If it does not exist, log a warning and exit cleanly (exit code 0 — cron will retry).
3. **Initialise StateBuffer** — `retention_hours` from `SITE_CONFIG["retention_hours"]` (default 48); optional `cusum_params` from `SITE_CONFIG["cusum_params"]`.
4. **Ensure ingest_log table** exists.
5. **Discover files** — `find_new_files()`.
6. **For each candidate file**:
   - Skip if `_already_ingested()` returns `True`.
   - Call `ingest_file()` inside a `try/except` block.
   - Log results via `_log_ingest()` (even on failure, with `error_msg`).
7. **Post-ingest check** — Read `buf.get_latest()` and `buf.get_cusum_status()`. Log a warning if any CUSUM accumulator is within 70% of its threshold `H`.
8. **Close StateBuffer**.

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `args` | `argparse.Namespace` | Parsed CLI arguments |

---

## CLI entry point `main()`

```python
python src/ingest_daemon.py [options]
```

### Arguments

| Argument | Type | Description |
|---|---|---|
| `--watch` | `str` | Network drive path to watch (directory). Default: `{PROJECT_ROOT}/scada_exports` or `site_config.json` `ingest_daemon.watch_dir` |
| `--pattern` | `str` | Glob pattern for file matching, e.g. `"*.csv"`, `"export_*.xlsx"`. Default: `"*.csv"` |
| `--lookback` | `int` | Only scan files modified in the last N minutes. Default: `15` |
| `--subfolders` | flag | Recurse into subfolders using `rglob` |
| `--dry-run` | flag | Discover and list files that would be ingested, but do not write to DB |

### `--dry-run` behaviour

When `--dry-run` is specified, the script:
1. Resolves watch_dir, pattern, and lookback from CLI args / site_config / defaults.
2. Calls `find_new_files()`.
3. Logs each file that would be processed (with mtime and size).
4. Exits without initialising StateBuffer or writing anything.

### Normal behaviour

1. Logs startup banner with resolved configuration.
2. Verifies watch directory is accessible (clean exit if not).
3. Opens StateBuffer with configured retention and CUSUM params.
4. Discovers matching files modified within the lookback window.
5. Skips already-ingested files; ingests new/modified ones.
6. Logs each file's accepted/rejected row count (or error).
7. Checks CUSUM threshold proximity and logs warnings.
8. Logs cycle totals and closes StateBuffer.

---

## `site_config.json` ingest_daemon configuration

All runtime defaults can be set in `site_config.json` under the `"ingest_daemon"` key:

```json
{
    "db_path": "data/plant_state.sqlite",
    "retention_hours": 48,
    "cusum_params": {
        "H": 5.0,
        "k": 1.0
    },
    "ingest_daemon": {
        "watch_dir":          "/Volumes/SCADA_Share/exports",
        "file_pattern":       "*.csv",
        "lookback_minutes":   15,
        "poll_subfolders":    false,
        "scale_biogas_flow":  true,
        "biogas_scale_factor": 1.0
    }
}
```

| Key | Type | Description |
|---|---|---|
| `watch_dir` | `str` | Absolute path to the SCADA export directory |
| `file_pattern` | `str` | Glob pattern for file matching |
| `lookback_minutes` | `int` | Scan window for recently modified files |
| `poll_subfolders` | `bool` | Recursive subdirectory scanning |
| `biogas_scale_factor` | `float` | Multiplicative scale factor for `biogas_flow_nm3h` (1.0 = already in Nm³/h; `1/24_000_000` for mL/day → Nm³/h) |

---

## Error handling summary

| Scenario | Behaviour |
|---|---|
| Network drive offline | `OSError` caught in `find_new_files()`; warning logged; clean exit |
| File not accessible during stat | Silently skipped |
| File fails to parse / map | `Exception` caught in `run()`; error logged; `ingest_log.error_msg` set; processing continues |
| No files to ingest | Logs `"No files modified in last N min — nothing to do."` and exits |
| Watch directory does not exist | Logs warning; clean exit (exit 0); cron retries next interval |

---

## Ingest log inspection

```bash
# Show last 20 ingestion events
sqlite3 data/plant_state.sqlite \
  "SELECT file_path, ingested_at, rows_accepted, error_msg \
   FROM ingest_log ORDER BY id DESC LIMIT 20;"

# Show failed ingests
sqlite3 data/plant_state.sqlite \
  "SELECT file_path, ingested_at, error_msg \
   FROM ingest_log WHERE error_msg IS NOT NULL;"

# Summary stats
sqlite3 data/plant_state.sqlite \
  "SELECT COUNT(*) AS total_files, SUM(rows_accepted) AS total_rows \
   FROM ingest_log;"
```
