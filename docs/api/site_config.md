# `site_config.json` Reference

## Overview

`site_config.json` is the central configuration file for the Biomethane Operator Assistant. It controls:

- Digester geometry for simulation and routing
- Temperature profile (lab-scale vs industrial)
- Alert thresholds per profile
- CUSUM filter parameters per sensor
- Daily background assessment triggers
- SCADA column mappings
- File watcher settings

**Discovery order** (searched at startup):

1. `{PROJECT_ROOT}/site_config.json`
2. `{PROJECT_ROOT}/src/site_config.json`

The `--site` CLI flag (available on `bio_cli.py`, `bio_methane_operations_mcp_server_v5.py`, `scada_mapper.py`) overrides this path.

---

## Full schema

```json
{
  "db_path": "data/plant_state.sqlite",
  "retention_hours": 48,
  "lab_scale": true,
  "estimated_geometry": {
    "digester_volume_m3": 1.0,
    "hrt_days": 17,
    "feed_kg_vs_day": 1.28,
    "s1_in_g_per_l": 18.0
  },
  "alert_thresholds": {
    "lab_scale": {
      "digester_temp_c": {"low": 20, "high": 30},
      "vfa_mmol_l": {"medium": 30, "high": 80},
      "nh4_mg_l": {"medium": 300, "high": 800},
      "h2s_ppm": {"medium": 200, "high": 500},
      "digester_ph": {"low": 6.8, "high": 7.8},
      "biomethane_purity_pct": {"min": 95},
      "o2_ppm": {"max": 500}
    },
    "industrial": {
      "digester_temp_c": {"low": 35, "high": 40},
      "vfa_mmol_l": {"medium": 8, "high": 15},
      "nh4_mg_l": {"medium": 300, "high": 800},
      "h2s_ppm": {"medium": 200, "high": 500},
      "digester_ph": {"low": 6.8, "high": 7.8},
      "biomethane_purity_pct": {"min": 95},
      "o2_ppm": {"max": 500}
    }
  },
  "cusum_params": {
    "digester_temp_c": {"K": 0.5, "H": 2.0},
    "biogas_flow_nm3h": {"K": 0.00001, "H": 0.00005},
    "ch4_pct": {"K": 1.0, "H": 5.0},
    "digester_ph": {"K": 0.1, "H": 0.5}
  },
  "daily_assessment": {
    "souring_probability_watch": 0.10,
    "souring_probability_action": 0.30,
    "s2_rising_days": 2,
    "run_hour_utc": 6
  },
  "ingest_daemon": {
    "watch_dir": "/Volumes/SCADA_Share/exports",
    "file_pattern": "*.csv",
    "lookback_minutes": 15,
    "poll_subfolders": false,
    "biogas_scale_factor": 1.0
  },
  "tag_mapping": {
    "digester_temp_c": "TIC_101",
    "biogas_flow_nm3h": "FT_201",
    "ch4_pct": "QT_301"
  }
}
```

---

## Section reference

### `db_path`

| Type | Default | Used by |
|------|---------|---------|
| `str` | `data/plant_state.sqlite` | StateBuffer |

Path (relative to project root) to the StateBuffer SQLite database.

---

### `retention_hours`

| Type | Default | Used by |
|------|---------|---------|
| `int` | `48` | StateBuffer |

Number of hours of sensor readings to retain. Older records are purged on each insert.

---

### `lab_scale`

| Type | Default | Used by |
|------|---------|---------|
| `bool` | `false` | MCP server, bio_cli, StateBuffer |

When `true`, the system uses lab-scale temperature thresholds (20–30 °C) instead of industrial digester ranges (35–40 °C). The `--lab-scale` CLI flag overrides this.

---

### `estimated_geometry`

| Type | Used by |
|------|---------|
| `object` | MCP server (EnKF auto-init), biomethane_router, bio_cli, seed_plant_state |

Physical parameters of the digester.

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `digester_volume_m3` | `float` | Yes | Working volume of the digester in cubic metres |
| `hrt_days` | `float` | No | Hydraulic retention time in days |
| `feed_kg_vs_day` | `float` | No | Daily volatile solids feed in kg VS/day |
| `s1_in_g_per_l` | `float` | Yes | Baseline influent substrate COD in g COD/L |

Used by:
- **EnKF auto-initialisation** at server startup (`enkf_initialise()`)
- **biomethane_router** to compute `_DIGESTER_VOLUME_M3` and `_S1_IN_G_PER_L`
- **seed_plant_state.py** for geometry-based bootstrapping defaults

---

### `alert_thresholds`

| Type | Used by |
|------|---------|
| `object` | MCP server, bio_cli |

Two profiles: `lab_scale` and `industrial`. Each contains per-sensor thresholds.

| Sensor key | Type | Meaning |
|------------|------|---------|
| `digester_temp_c` | `{low, high}` | Temperature range (low/high alert) |
| `vfa_mmol_l` | `{medium, high}` | VFA concentration (medium = advisory, high = critical) |
| `nh4_mg_l` | `{medium, high}` | Ammonium threshold |
| `h2s_ppm` | `{medium, high}` | Hydrogen sulphide threshold |
| `digester_ph` | `{low, high}` | pH range |
| `biomethane_purity_pct` | `{min}` | Minimum CH₄ purity |
| `o2_ppm` | `{max}` | Maximum oxygen (air ingress detection) |

Any key not present in the config falls back to hardcoded defaults.

---

### `cusum_params`

| Type | Used by |
|------|---------|
| `object` | StateBuffer |

CUSUM (Cumulative Sum) control chart parameters per sensor. Each entry maps a sensor name to:

| Key | Description |
|-----|-------------|
| `K` | Allowable slack — the magnitude of natural noise the filter ignores |
| `H` | Decision interval — the cumulative deviation above K that triggers a spike rejection (value → `NULL`) |

Sensors without a CUSUM entry are written unfiltered.

---

### `daily_assessment`

| Type | Used by |
|------|---------|
| `object` | MCP server (background daily thread) |

| Key | Default | Description |
|-----|---------|-------------|
| `souring_probability_watch` | `0.10` | EnKF souring probability at which an advisory is logged |
| `souring_probability_action` | `0.30` | Triggers `ad4_perturbation_test()` to quantify headroom |
| `s2_rising_days` | `2` | Consecutive daily S₂ increases before the trend is flagged |
| `run_hour_utc` | `6` | UTC hour when the background assessment thread executes |

---

### `ingest_daemon`

| Type | Used by |
|------|---------|
| `object` | ingest_daemon.py |

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `watch_dir` | `str` | `{PROJECT_ROOT}/scada_exports` | Absolute path to the SCADA export directory |
| `file_pattern` | `str` | `*.csv` | Glob pattern for file matching |
| `lookback_minutes` | `int` | `15` | Scan window for recently modified files |
| `poll_subfolders` | `bool` | `false` | Recursive subdirectory scanning |
| `biogas_scale_factor` | `float` | `1.0` | Multiplier for `biogas_flow_nm3h` (e.g. `1/24000000` for mL/day → Nm³/h) |

---

### `tag_mapping`

| Type | Used by |
|------|---------|
| `object` | scada_mapper.py (optional) |

Static SCADA column-to-internal-variable mapping. When present, `scada_mapper` returns this mapping directly instead of auto-detecting the vendor format.

```json
{
  "tag_mapping": {
    "digester_temp_c": "TIC_101",
    "biogas_flow_nm3h": "FT_201",
    "ch4_pct": "QT_301"
  }
}
```

Useful when your SCADA tags are stable and you want to skip auto-detection entirely.

---

## Which scripts read `site_config.json`

| Script | Keys used |
|--------|-----------|
| `bio_methane_operations_mcp_server_v5.py` | `lab_scale`, `estimated_geometry`, `alert_thresholds`, `daily_assessment` |
| `StateBuffer.py` | `db_path`, `retention_hours`, `lab_scale`, `cusum_params` |
| `biomethane_router.py` | `estimated_geometry` (digester_volume_m3, s1_in_g_per_l) |
| `bio_cli.py` | `lab_scale`, `alert_thresholds`, `estimated_geometry` |
| `scada_mapper.py` | `tag_mapping` (optional) |
| `ingest_daemon.py` | `ingest_daemon` block, `db_path`, `retention_hours`, `cusum_params` |
| `seed_plant_state.py` | `estimated_geometry` |
