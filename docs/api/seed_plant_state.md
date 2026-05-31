# seed_plant_state — API Reference

**Module**: `src/seed_plant_state.py`

---

## Overview

Initialise the MCP server's `plant_state` SQLite database from historical CSV data. The pipeline runs each CSV row through `scada_mapper` column mapping and `StateBuffer` CUSUM filtering, then computes period-averaged seed values (mean biogas, mean temperature, OLR, etc.) and writes them into the MCP server's `biomethane.db` via direct SQLite updates to the `plant_state` table.

### Full pipeline

```
ri_flex.csv
  │
  ▼  scada_mapper            (fuzzy column mapping)
  ▼  StateBuffer             (CUSUM filtering → plant_state.sqlite)
  ▼  this script             (reads StateBuffer → seeds MCP server DB)
```

### Why seed values are needed

The MCP server internal model needs initial `plant_state` values for:
- `digester_temp_c` — steady-state digester temperature
- `biogas_flow_nm3h` — expected biogas production rate
- `ch4_pct` — expected methane concentration
- OLR, HRT, digester volume
- VS loading rate

These are computed as period means from clean (CUSUM-filtered) historical data.

---

## Module-level constants

| Constant | Value | Description |
|---|---|---|
| `PROJECT_ROOT` | `Path` (resolved) | Project root via anchor detection (`pyproject.toml`, `setup.py`, `setup.cfg`, `.git`) or `.env` `PROJECT_ROOT=` |
| `SITE_CONFIG` | `dict` | Loaded from `site_config.json` (looked up in `PROJECT_ROOT` then `PROJECT_ROOT/src/`) |
| `DIGESTER_VOLUME_DEFAULT` | `float` | From `SITE_CONFIG["estimated_geometry"]["digester_volume_m3"]` or `6.0` |
| `HRT_DAYS_DEFAULT` | `float` | From `SITE_CONFIG["estimated_geometry"]["hrt_days"]` or `17.0` |
| `DEFAULT_CSV` | `Path` | `{PROJECT_ROOT}/sample_data/ri_flex.csv` |
| `DEFAULT_BUFFER_DB` | `Path` | `{PROJECT_ROOT}/data/plant_state.sqlite` |
| `DEFAULT_MCP_DB` | `Path` | `{PROJECT_ROOT}/data/biomethane.db` |
| `CATTLE_SLURRY_VS_PCT` | `float` | `6.4` — volatile solids percentage for cattle slurry |

### OLR scale threshold references

The OLR is computed but thresholds are **not hardcoded** — they are advisory in the documentation:

| OLR range | Interpretation |
|---|---|
| `< 1.5 kg VS/m³/day` | Low-loading (stable, low risk) |
| `1.5 – 3.0 kg VS/m³/day` | Moderate (typical for agri-scale) |
| `> 3.0 kg VS/m³/day` | High-loading (monitor for instability) |

---

## Functions

### `load_site_config() -> dict`

```python
def load_site_config() -> dict
```

Load `site_config.json` from the project root or `src/` subdirectory.

#### Search order

1. `{PROJECT_ROOT}/site_config.json`
2. `{PROJECT_ROOT}/src/site_config.json`

Returns `{}` if neither exists.

---

### `select_period(df, date_col, period) -> DataFrame`

```python
def select_period(df: pd.DataFrame, date_col: str, period: str) -> pd.DataFrame
```

Filter a DataFrame to a named period for seed value computation.

#### Period definitions

| Period | Filter |
|---|---|
| `"last30"` | Last 30 days of data (from the maximum date in the column) |
| `"annual"` | All rows (full year) |
| `"peak"` | October, November, December |
| `"winter"` | January, February, March |

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `df` | `pd.DataFrame` | Full dataset |
| `date_col` | `str` | Name of the datetime column |
| `period` | `str` | One of `"last30"`, `"annual"`, `"peak"`, `"winter"` |

#### Returns

Filtered `pd.DataFrame`.

#### Raises

`ValueError` — for an unknown period string.

---

### `ingest_csv_to_buffer(csv_path, buffer_db, period, dry_run=False) -> StateBuffer`

```python
def ingest_csv_to_buffer(csv_path: Path, buffer_db: Path,
                         period: str, dry_run: bool = False) -> StateBuffer
```

Full pipeline: load CSV → scada_mapper → StateBuffer CUSUM filtering.

#### Algorithm

1. **Load CSV** — `scada_mapper.read_file()` with encoding fallback.
2. **Detect vendor** — `scada_mapper.detect_scada_vendor()`.
3. **Auto-map columns** — `scada_mapper.auto_map_dataframe()` builds `{internal: actual}` mapping.
4. **Period selection** — `select_period()` filters rows.
5. **StateBuffer ingestion** — For each row:
   - Convert string values to `float`, strip `"kg"` suffix.
   - Apply **scale conversion**: `biogas_flow_nm3h` values divided by `24_000_000` (mL/day → Nm³/h).
   - Insert via `buf.insert_live_data()` (triggers CUSUM filter).
   - Rows with no mappable values are counted as rejected.
6. **Buffer persistence** — Uses `":memory:"` if `dry_run=True`, otherwise the given `buffer_db` path with `retention_hours=99999` (preserves all historical data).

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `csv_path` | `Path` | — | Path to input CSV |
| `buffer_db` | `Path` | — | Path for StateBuffer SQLite database |
| `period` | `str` | — | One of `"last30"`, `"annual"`, `"peak"`, `"winter"` |
| `dry_run` | `bool` | `False` | Use in-memory buffer, no persistent DB |

#### Returns

Initialised `StateBuffer` instance populated with filtered + CUSUM-filtered data.

---

### `compute_seed_values(buf, digester_volume_m3, hrt_days) -> dict`

```python
def compute_seed_values(buf: StateBuffer, digester_volume_m3: float,
                        hrt_days: float) -> dict
```

Read clean (CUSUM-filtered) data from StateBuffer and compute period-averaged seed values for the MCP server.

#### Computed values

| Seed field | Source | Formula |
|---|---|---|
| `digester_temp_c` | `StateBuffer.get_model_dataframe()["digester_temp_c"].mean()` | Mean temperature |
| `biogas_flow_nm3h` | `StateBuffer.get_model_dataframe()["biogas_flow_nm3h"].mean()` | Mean biogas (already converted to Nm³/h) |
| `ch4_pct` | `StateBuffer.get_model_dataframe()["ch4_pct"].mean()` | Mean methane concentration |
| `vs_kg_per_day` | Fixed calculation | `20.0 × (6.4 / 100) = 1.28 kg VS/day` |
| `olr_kg_vs_m3_day` | Derived | `vs_kg_per_day / digester_volume_m3` |
| `digester_volume_m3` | Parameter | User-provided or site_config default |
| `hrt_days` | Parameter | User-provided or site_config default |
| `n_rows_clean` | Count | Number of clean rows in StateBuffer |

#### Feed rate assumption

The script assumes a **feed rate of 20 L/day** of cattle slurry at 6.4% VS:

```python
vs_kg_per_day = 20.0 * (CATTLE_SLURRY_VS_PCT / 100.0)  # = 1.28 kg VS/day
```

This is based on the R1-FLEX reactor geometry:
- Working volume: ~850 L
- Feed volume: ~50 L/day → HRT ~17 days
- Slurry VS: 6.4% → 1.28 kg VS/day at 20 L/day feed equivalent

#### Parameters

| Parameter | Type | Description |
|---|---|---|
| `buf` | `StateBuffer` | Populated StateBuffer instance |
| `digester_volume_m3` | `float` | Digester volume in m³ |
| `hrt_days` | `float` | Hydraulic retention time in days |

#### Returns

```python
{
    "digester_temp_c":    float,   # or None if not available
    "biogas_flow_nm3h":   float,   # or None if not available
    "ch4_pct":            float,   # or None if not available
    "vs_kg_per_day":      float,   # 1.28
    "olr_kg_vs_m3_day":   float,
    "digester_volume_m3": float,
    "hrt_days":           float,
    "n_rows_clean":       int,
}
```

#### Raises

`RuntimeError` — if `buf.get_model_dataframe()` is empty (no valid rows after CUSUM filtering).

---

### `seed_mcp_db(mcp_db, seed, dry_run=False)`

```python
def seed_mcp_db(mcp_db: Path, seed: dict, dry_run: bool = False)
```

Write seed values into the MCP server's SQLite database (`plant_state` table). Updates existing rows or inserts new ones.

#### Algorithm

1. Open `mcp_db` as a SQLite connection.
2. Verify the `plant_state` table exists.
3. For each key in `("digester_temp_c", "biogas_flow_nm3h", "ch4_pct")`:
   - If `seed[key]` is not `None`, `UPDATE plant_state SET value=?, updated_at=? WHERE key=?`.
   - If `UPDATE` matches 0 rows, `INSERT INTO plant_state (key, value, updated_at) VALUES (?, ?, ?)`.
4. Create `seed_log` table if not present, and insert a seeding audit record with:
   - `seeded_at`, `reactor` (`"R1-FLEX"`), `period`, `n_rows`, `olr`, `notes` (volume + HRT).

#### Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `mcp_db` | `Path` | — | Path to MCP server SQLite database (`biomethane.db`) |
| `seed` | `dict` | — | Seed values dict (from `compute_seed_values()`) |
| `dry_run` | `bool` | `False` | Print values but do not write to DB |

#### Side effects

- Writes to `plant_state` table: `digester_temp_c`, `biogas_flow_nm3h`, `ch4_pct`.
- Creates/updates `seed_log` table with an audit trail of seeding operations.

#### Important

The MCP server must have been started at least once before seeding — `biomethane.db` with the `plant_state` table is created on first launch. If the DB does not exist, the script exits with an error.

---

## CLI entry point `main()`

```python
python src/seed_plant_state.py [options]
```

### Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--csv` | `Path` | `DEFAULT_CSV` | Path to input CSV file |
| `--buffer-db` | `Path` | `DEFAULT_BUFFER_DB` | Path for StateBuffer SQLite database |
| `--mcp-db` | `Path` | `DEFAULT_MCP_DB` | Path to MCP server SQLite database |
| `--period` | `str` | `"last30"` | Period: `"last30"`, `"annual"`, `"peak"`, or `"winter"` |
| `--digester-volume` | `float` | `DIGESTER_VOLUME_DEFAULT` | Digester volume in m³ (from `site_config.json` or `6.0`) |
| `--hrt-days` | `float` | `HRT_DAYS_DEFAULT` | Hydraulic retention time in days (from `site_config.json` or `17.0`) |
| `--dry-run` | flag | `False` | Print computed values without writing to MCP DB |

### Behaviour

1. Validate CSV path existence.
2. Create buffer DB parent directory if needed.
3. **Step 1** — Call `ingest_csv_to_buffer()`:
   - Print SCADA column mapping.
   - Print vendor, mapped variables, period date range.
   - Print CUSUM filtering results (accepted/skipped rows).
4. **Step 2** — Call `compute_seed_values()`:
   - Print seed values (temperature, biogas, CH₄, OLR, volume, HRT, clean row count).
5. **Step 3** — Call `seed_mcp_db()`:
   - Print target DB path.
   - If `--dry-run`, skip writes.
   - Write values to `plant_state` table.
   - Log seeding audit in `seed_log` table.
6. Close StateBuffer.
7. Print completion summary.

---

## Usage examples

```bash
# Default: last 30 days, site_config geometry
python src/seed_plant_state.py

# Full-year averages
python src/seed_plant_state.py --period annual

# Peak season (Oct-Dec)
python src/seed_plant_state.py --period peak

# Winter baseline (Jan-Mar)
python src/seed_plant_state.py --period winter

# Custom digester geometry with dry run
python src/seed_plant_state.py --digester-volume 1.2 --hrt-days 20 --dry-run

# Custom CSV and buffer DB paths
python src/seed_plant_state.py --csv sample_data/ri_flex.csv --buffer-db data/my_buffer.sqlite
```

## Seed log table

Each seeding operation creates an entry in the `seed_log` table:

```sql
CREATE TABLE IF NOT EXISTS seed_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seeded_at TEXT,
    reactor TEXT,
    period TEXT,
    n_rows INTEGER,
    olr REAL,
    notes TEXT
);
```

Query the log to see seeding history:

```bash
sqlite3 data/biomethane.db "SELECT * FROM seed_log ORDER BY id DESC;"
```
