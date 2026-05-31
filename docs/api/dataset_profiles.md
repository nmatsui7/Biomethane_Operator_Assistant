# Utility Modules — API Reference

**Modules**: `src/dataset_profiles.py`, `src/env_config.py`

---

## Part A — `dataset_profiles.py`

### Overview

Dataset configuration profiles for different CSV formats used by the biomethane pipeline. Profiles define dataset-specific parameters such as units, temperature ranges, biogas conversion factors, outlier thresholds, CUSUM profiles, EnKF reference temperatures, and digester sizing estimates.

The profile system allows a single pipeline to handle multiple datasets (lab-scale and industrial) without hardcoding format-specific logic.

```python
from dataset_profiles import get_profile, detect_dataset_profile, list_profiles, apply_profile_to_args
```

---

### Constants

#### `DATASET_PROFILES`

```python
DATASET_PROFILES: Dict[str, Dict[str, Any]]
```

A dictionary of dataset configuration profiles. Each value is a dict with the fields documented below.

##### Profile: `"ri_flex"` — R1-FLEX Lab Scale

| Field | Type | Value | Description |
|-------|------|-------|-------------|
| `name` | `str` | `"R1-FLEX Lab Scale"` | Human-readable label |
| `lab_scale` | `bool` | `True` | Marked as lab-scale dataset |
| `temp_range` | `list[float]` | `[20, 30]` | Expected operating temperature range (°C) |
| `biogas_unit` | `str` | `"mL/day"` | Unit of biogas production |
| `biogas_conversion` | `int` | `24_000_000` | mL/day → Nm³/h conversion factor |
| `date_columns` | `None` | `None` | Single date column exists (auto-detect) |
| `date_format` | `None` | `None` | Auto-detect date format |
| `outlier_thresholds` | `dict` | See below | Thresholds for outlier filtering |
| `cusum_profile` | `str` | `"lab_scale"` | CUSUM monitoring profile |
| `enkf_t_ref` | `float` | `21.0` | EnKF reference temperature (°C) |
| `default_ch4_pct` | `None` | `None` | Use actual CH₄ if available |
| `aggregate_per_day` | `bool` | `False` | No aggregation needed |
| `digester_volume_est` | `float` | `1.0` | Estimated digester volume (m³) |
| `hrt_days_est` | `float` | `17.0` | Estimated HRT (days) |

**`outlier_thresholds` for ri_flex:**

| Key | Value | Description |
|-----|-------|-------------|
| `biogas_min` | `0` | Minimum biogas (mL/day) |
| `biogas_max` | `5000` | Maximum biogas (mL/day) |
| `temp_min` | `5` | Minimum temperature (°C) |
| `temp_max` | `50` | Maximum temperature (°C) |

##### Profile: `"indian"` — Indian Biogas Plant (Industrial)

| Field | Type | Value | Description |
|-------|------|-------|-------------|
| `name` | `str` | `"Indian Biogas Plant (Industrial)"` | Human-readable label |
| `lab_scale` | `bool` | `False` | Real-world industrial plant |
| `temp_range` | `list[float]` | `[35, 40]` | Expected operating temperature range (°C) |
| `biogas_unit` | `str` | `"m3/day"` | Unit of biogas production |
| `biogas_conversion` | `int` | `24` | m³/day → Nm³/h conversion factor |
| `date_columns` | `list[str]` | `["Year", "Month", "Day"]` | Multi-column date components |
| `date_format` | `str` | `"{Year}-{Month:02d}-{Day:02d}"` | Date assembly format string |
| `outlier_thresholds` | `dict` | See below | Thresholds for outlier filtering |
| `cusum_profile` | `str` | `"industrial"` | CUSUM monitoring profile |
| `enkf_t_ref` | `float` | `35.0` | EnKF reference temperature (°C) |
| `default_ch4_pct` | `float` | `58.0` | Fixed CH₄ estimate for Indian dataset |
| `aggregate_per_day` | `bool` | `True` | ~2.8 readings/day → aggregate |
| `digester_volume_est` | `float` | `100.0` | Estimated digester volume (m³) |
| `hrt_days_est` | `float` | `20.0` | Estimated HRT (days) |

**`outlier_thresholds` for indian:**

| Key | Value | Description |
|-----|-------|-------------|
| `biogas_min` | `0` | Minimum biogas (m³/day) |
| `biogas_max` | `500` | Maximum biogas (m³/day) |
| `temp_min` | `25` | Minimum temperature (°C) |
| `temp_max` | `50` | Maximum temperature (°C) |

##### All profile fields reference

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Human-readable profile label |
| `lab_scale` | `bool` | Whether this is a lab-scale dataset |
| `temp_range` | `list[float]` | Expected operating temperature range [min, max] (°C) |
| `biogas_unit` | `str` | Unit of biogas measurement (e.g. `"mL/day"`, `"m3/day"`) |
| `biogas_conversion` | `float` | Conversion factor to Nm³/h |
| `date_columns` | `list[str]` or `None` | Column names for date components (`None` = single date column) |
| `date_format` | `str` or `None` | Date format string for assembling date columns |
| `outlier_thresholds` | `dict` | `biogas_min`, `biogas_max`, `temp_min`, `temp_max` for filtering |
| `cusum_profile` | `str` | CUSUM monitoring profile key (`"lab_scale"` or `"industrial"`) |
| `enkf_t_ref` | `float` | Reference temperature for EnKF (°C) |
| `default_ch4_pct` | `float` or `None` | Default CH₄ % when measured value unavailable |
| `aggregate_per_day` | `bool` | Whether to aggregate multiple readings per day |
| `digester_volume_est` | `float` | Estimated digester working volume (m³) |
| `hrt_days_est` | `float` | Estimated hydraulic retention time (days) |

---

### Functions

#### `get_profile`

```python
def get_profile(profile_name: str) -> Optional[Dict[str, Any]]
```

Retrieve a dataset profile by name.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `profile_name` | `str` | Profile key: `"ri_flex"` or `"indian"` |

**Returns:** The profile dict, or `None` if not found.

**Example:**

```python
profile = get_profile("indian")
profile["biogas_conversion"]  # 24
```

---

#### `list_profiles`

```python
def list_profiles() -> list
```

Return a list of available profile names.

**Returns:** `["ri_flex", "indian"]`

---

#### `detect_dataset_profile`

```python
def detect_dataset_profile(csv_path: Path) -> Optional[str]
```

Auto-detect which dataset profile applies to a CSV file by inspecting column names.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `csv_path` | `Path` | Path to the CSV file |

**Detection logic:**

| Dataset | Signature |
|---------|-----------|
| `"indian"` | CSV has columns `Year`, `Month`, `Day` AND `biogas_production` |
| `"ri_flex"` | CSV has column containing `"daily biogas"` OR `"digestertemp"` (case-insensitive) |

**Returns:** Profile name string or `None` if unknown.

**Example:**

```python
profile = detect_dataset_profile(Path("data.csv"))
if profile:
    cfg = get_profile(profile)
```

---

#### `apply_profile_to_args`

```python
def apply_profile_to_args(args, profile: Dict[str, Any]) -> None
```

Apply profile defaults to an `argparse.Namespace` object. Only overrides values if they match the default sentinel values (indicating the user did not explicitly set them).

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `args` | `argparse.Namespace` | Parsed CLI arguments |
| `profile` | `Dict[str, Any]` | Profile dict from `get_profile()` |

**Fields applied:**

| Field | Sentinel value | Profile key used |
|-------|----------------|------------------|
| `args.digester_volume` | `1.0` | `digester_volume_est` |
| `args.hrt_days` | `17.0` | `hrt_days_est` |
| `args.lab_scale` | (any) | `lab_scale` (always set) |

---

## Part B — `env_config.py`

### Overview

Centralised environment configuration loader. Loads `.env` from the project root using `python-dotenv` and exposes configuration values as module-level constants. All scripts should import from here rather than reading `os.environ` directly.

```python
from env_config import PROJECT_ROOT, MODEL_PATH, MCP_SERVER_SCRIPT, VENV_PYTHON, CTX_SIZE, PORT, ...
```

---

### Constants

All constants are read from `os.environ` with sensible defaults. The `.env` file is loaded from the project root directory (parent of `src/`).

| Constant | Type | Default | Environment Variable | Description |
|----------|------|---------|---------------------|-------------|
| `PROJECT_ROOT` | `str` | Parent directory of `src/` | `PROJECT_ROOT` | Absolute path to project root |
| `MODEL_PATH` | `str` | `~/.lmstudio/models/.../Qwen3-4B-Q4_K_M.gguf` | `MODEL_PATH` | Path to LLM model file (tilde expanded) |
| `LLAMA_SERVER_URL` | `str` | `http://localhost:8082/v1` | `LLAMA_SERVER_URL` | llama.cpp server base URL |
| `MCP_SERVER_URL` | `str` | `http://localhost:3000/sse` | `MCP_SERVER_URL` | MCP HTTP SSE endpoint URL |
| `MCP_SERVER_SCRIPT` | `str` | Resolved via `_resolve_path(...)` | `MCP_SERVER_SCRIPT` | Path to MCP server Python script |
| `VENV_PYTHON` | `str` | Resolved via `_resolve_path(...)` | `VENV_PYTHON` | Path to virtual environment Python binary |
| `CTX_SIZE` | `int` | `4096` | `CTX_SIZE` | LLM context window size (tokens) |
| `PORT` | `int` | `8082` | `PORT` | LLM server port |
| `LLAMA_SERVER` | `str` | `/usr/local/bin/llama-server-mcp` | `LLAMA_SERVER` | Path to llama-server-mcp binary |
| `MODEL_NAME` | `str` | `"Qwen3-4B-Q4_K_M.gguf"` | `MODEL_NAME` | Model filename for auto-download |

---

### Private Functions

#### `_resolve_path`

```python
def _resolve_path(path: str, default: str) -> str
```

Resolve a path that may be relative or use `~`. If the path is relative (starts with `.` or does not start with `/`), it is joined with `PROJECT_ROOT`.

**Parameters:**

| Parameter | Description |
|-----------|-------------|
| `path` | The path from environment variable or fallback |
| `default` | Absolute fallback path |

**Returns:** Resolved absolute path with tilde expansion.

---

### `.env` file format

Create a `.env` file in the project root (next to `src/`). All paths can be absolute or relative to `PROJECT_ROOT`.

```bash
# LLM model — absolute path or relative to PROJECT_ROOT
MODEL_PATH=~/.lmstudio/models/lmstudio-community/Qwen3-4B-GGUF/Qwen3-4B-Q4_K_M.gguf

# LLM server URLs
LLAMA_SERVER_URL=http://localhost:8082/v1
MCP_SERVER_URL=http://localhost:3000/sse

# Script paths (relative paths resolved against PROJECT_ROOT)
MCP_SERVER_SCRIPT=src/bio_methane_operations_mcp_server_v5.py
VENV_PYTHON=.venv/bin/python

# Runtime configuration
CTX_SIZE=4096
PORT=8082
LLAMA_SERVER=/usr/local/bin/llama-server-mcp
MODEL_NAME=Qwen3-4B-Q4_K_M.gguf
```

Only keys that differ from defaults need to be set in `.env`.

---

### Usage

```python
from env_config import (
    PROJECT_ROOT,
    MODEL_PATH,
    LLAMA_SERVER_URL,
    MCP_SERVER_URL,
    MCP_SERVER_SCRIPT,
    VENV_PYTHON,
    CTX_SIZE,
    PORT,
)
```

**Usage in scripts launched from any directory:**

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.env_config import VENV_PYTHON, MCP_SERVER_SCRIPT
```

**Note:** `MCP_SERVER_SCRIPT` default in `env_config.py` is `src/bio_methane_operations_mcp_server_v5.py`; `run_mcp_tests.py` provides its own fallback paths.
