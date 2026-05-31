# MCP Test Runner Modules — API Reference

**Modules**: `src/run_mcp_tests.py`, `src/run_mcp_tests_rag_v2.py`

---

## Part A — `run_mcp_tests.py` (v1)

### Overview

Automated MCP integration test runner for the Biomethane MCP Server. Reads test cases from `biomethane_mcp_tests.md`, sends each prompt to an LLM backend (OpenAI-compatible or Anthropic), captures tool call invocations and responses, and writes structured JSON + Markdown reports.

```python
python src/run_mcp_tests.py
# or:
python src/run_mcp_tests.py --backend anthropic
python src/run_mcp_tests.py --test A03 --dry-run
```

**Requirements:** `pip install openai anthropic rich`

---

### Constants

#### `MCP_TOOLS`

```python
MCP_TOOLS: list[dict]
```

14 OpenAI-style function tool definitions mirroring the tools exposed by `bio_methane_operations_mcp_server_v5.py`. Each entry follows the `{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}` schema.

| # | Tool Name | Description |
|---|-----------|-------------|
| 1 | `get_plant_state` | Returns current plant operating state (all sensor readings) |
| 2 | `update_plant_state` | Update plant state values from a dict of parameter → numeric value |
| 3 | `check_alerts` | Check all parameters against thresholds and return active alerts |
| 4 | `blend_feedstocks` | Calculate blend of feedstocks to achieve target OLR |
| 5 | `get_operational_reference` | Look up operational guidance by topic key |
| 6 | `get_kpi_summary` | Get KPI summary: daily, weekly, or monthly |
| 7 | `list_feedstocks` | List available feedstocks with BMP, C/N, DM, and VS/DM values |
| 8 | `get_alert_history` | Get recent alert history log (optional `limit`) |
| 9 | `buswell_bmp` | Calculate theoretical BMP from elemental composition (Buswell equation) |
| 10 | `buswell_bmp_by_class` | Calculate Buswell BMP for named substrate class |
| 11 | `calculate_energy_conversion_factor` | Derive kWh/Nm³ from CH₄ fraction using LHV |
| 12 | `cn_ratio_from_composition` | Calculate C/N ratio from elemental analysis |
| 13 | `olr_from_recipe` | Calculate OLR from feedstock recipe |
| 14 | `biodegradability_coefficient` | Calculate η = empirical / theoretical |

#### `SYSTEM_PROMPT`

```python
SYSTEM_PROMPT: str
```

System prompt instructing the LLM to:
- Use MCP tools for ALL calculations (never calculate manually)
- Call exact tool names with parameters as shown
- Return the tool's exact output without rephrasing numbers

---

### Functions

#### `parse_test_file`

```python
def parse_test_file(path: Path) -> list[dict]
```

Parse the `biomethane_mcp_tests.md` test specification file into a list of test dicts.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | `Path` | Path to the markdown test file |

**Returns:** List of dicts, each with keys: `id`, `section`, `prompt`, `expect`.

**Parsing logic:**

1. Split on `## TEST:` markers (regex with `re.MULTILINE`).
2. First line after the marker → `id`.
3. Subsequent lines match `**section:**`, `**prompt:**`, `**expect:**` prefixes (case-insensitive, any whitespace before colon).

---

#### `dispatch_tool`

```python
def dispatch_tool(name: str, arguments: dict, mcp_client) -> str
```

Call an MCP server tool via the client and return the result as a string.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Tool name to call |
| `arguments` | `dict` | Tool arguments |
| `mcp_client` | `object` or `None` | MCP client with `.call_tool(name, arguments)` method; `None` returns stub |

**Parameter mapping:** Renames mismatched parameter keys (e.g., `readings` → `updates` for `update_plant_state`, `check_all_alerts` → `check_alerts`).

**Return handling:** Extracts `.content[].text` from MCP SDK result objects, passes through strings, or JSON-serialises dicts.

**Error handling:** Returns `{"error": ...}` JSON on exception.

---

#### `run_with_openai_compat`

```python
def run_with_openai_compat(
    prompt: str,
    base_url: str,
    model: str,
    mcp_client,
    max_rounds: int = 6,
    context_length: int = 4096,
) -> dict
```

Run a single test prompt against an OpenAI-compatible endpoint (e.g. LM Studio, llama.cpp). Handles multi-turn tool call loops.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt` | `str` | — | User prompt from test case |
| `base_url` | `str` | — | OpenAI-compatible server base URL (e.g. `http://localhost:8082/v1`) |
| `model` | `str` | — | Model name string |
| `mcp_client` | `object` or `None` | — | MCP client for tool dispatch |
| `max_rounds` | `int` | `6` | Maximum tool-call rounds before giving up |
| `context_length` | `int` | `4096` | Context length — response `max_tokens` is set to `context_length // 2` |

**Algorithm:**

1. Send system prompt + user prompt to the chat endpoint with `MCP_TOOLS` and `tool_choice="auto"`.
2. If no tool calls in response → final answer (return `response_text`, `tool_calls`, `elapsed_s`).
3. Otherwise, append assistant message, dispatch each tool call via `dispatch_tool`, append tool results, and loop.
4. If `max_rounds` exhausted → return `"[max tool rounds reached]"`.

**Returns:**

| Key | Type | Description |
|-----|------|-------------|
| `response_text` | `str` | Final LLM response text |
| `tool_calls` | `list[dict]` | Each entry: `tool`, `arguments`, `result_preview[:300]` |
| `elapsed_s` | `float` | Wall-clock seconds |

---

#### `run_with_anthropic`

```python
def run_with_anthropic(
    prompt: str, model: str, mcp_client, max_rounds: int = 6
) -> dict
```

Run a single test prompt against the Anthropic API (Claude). Converts OpenAI-style tool defs to Anthropic format internally.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt` | `str` | — | User prompt from test case |
| `model` | `str` | — | Anthropic model name (e.g. `"claude-sonnet-4-20250514"`) |
| `mcp_client` | `object` or `None` | — | MCP client for tool dispatch |
| `max_rounds` | `int` | `6` | Maximum tool-use rounds before giving up |

**Algorithm:**

1. Convert `MCP_TOOLS` to Anthropic format (`name`, `description`, `input_schema`).
2. Send user message with tools.
3. Collect `text` and `tool_use` blocks from response.
4. If `stop_reason == "end_turn"` or no tool uses → final answer.
5. Otherwise, append assistant turn, dispatch tool calls, append `tool_result` blocks, and loop.

**Returns:** Same schema as `run_with_openai_compat`.

---

#### `evaluate_result`

```python
def evaluate_result(test: dict, result: dict) -> str
```

Simple heuristic PASS/FAIL evaluation of a test result.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `test` | `dict` | Test case (from `parse_test_file`) |
| `result` | `dict` | Run result (from `run_with_*`) |

**Evaluation rules:**

| Condition | Verdict |
|-----------|---------|
| No tool calls | `FAIL — no tool called` |
| Empty response text | `FAIL — empty response` |
| Response < 30 characters | `WARN — very short response` |
| "error" in first 80 chars AND test ID in negative_tests set | `PASS — expected error returned` |
| "error" in first 80 chars | `WARN — possible error (check manually)` |
| Otherwise | `PASS` |

**Negative test IDs** (expected error responses):
`A05`, `A06`, `B03`, `B04`, `D07`

---

#### `write_report`

```python
def write_report(results: list[dict], output_path: Path, backend: str, model: str) -> tuple[Path, Path]
```

Write test results to JSON and Markdown report files.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `results` | `list[dict]` | List of result dicts (each with `id`, `section`, `verdict`, `tool_calls`, `prompt`, `expect`, `response_text`, `elapsed_s`) |
| `output_path` | `Path` | Base path (no extension) — `.json` and `.md` are appended |
| `backend` | `str` | Backend name for report header |
| `model` | `str` | Model name for report header |

**Returns:** `(json_path, md_path)` — paths to the written files.

**JSON report:** Full serialised results list with `indent=2`.

**Markdown report:**

```
# Biomethane MCP Test Report

**Run at:** 2026-05-31 12:00
**Backend:** llama-cpp / `Qwen3-4B...`
**Results:** 12/15 PASS — 2 WARN — 1 FAIL

| ID | Section | Verdict | Tools Called | Time (s) |
|---|---|---|---|---|
| A01 | Plant State & Alerts | PASS | get_plant_state | 4.23 |
...

## Detailed Results

### A01 — Plant State & Alerts
**Prompt:** What is the current plant status summary?
**Expected:** Returns overall_status...
**Verdict:** PASS
**Elapsed:** 4.23s

**Tool calls:**
- `get_plant_state` → {"overall_status": "NORMAL", ...}

**LLM response:**
> The current plant status is NORMAL...
```

---

#### `build_mcp_client`

```python
def build_mcp_client(server_script: str, use_http: bool = False, http_port: int = 3000) -> object | None
```

Launch the MCP server as a subprocess and communicate via JSON-RPC over stdio (or HTTP).

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `server_script` | `str` | — | Path to the MCP server Python script |
| `use_http` | `bool` | `False` | Use HTTP transport (requires uvicorn) |
| `http_port` | `int` | `3000` | Port for HTTP transport |

**Returns:** An `MCPClient` object with a `.call_tool(name, arguments)` method, or `None` (dry-run mode).

**MCPClient interface:**

```python
class MCPClient:
    def call_tool(self, name: str, arguments: dict) -> str | None
```

**Protocol:**

1. Spawns `[venv_python, server_script]` as subprocess (with `--http --port` if HTTP transport).
2. Waits 2 seconds for server initialisation.
3. Sends JSON-RPC `initialize` request with protocol version `2025-06-18`.
4. Sends `tools/list` for verification (not used in v1).
5. `call_tool` sends `tools/call` JSON-RPC and extracts `result.content[0].text`.

**Error handling:** On connection failure, prints a warning and returns `None` for dry-run mode.

---

### CLI (`main`)

```bash
python src/run_mcp_tests.py [options]
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--backend` | `str` | `"llama-cpp"` | One of `llama-cpp`, `lmstudio`, `anthropic` |
| `--base-url` | `str` | `http://localhost:8082/v1` | Base URL for OpenAI-compatible backend |
| `--model` | `str` | (auto) | Model name override; defaults: `claude-sonnet-4-20250514` for anthropic, `Qwen3-4B-Thinking-...` otherwise |
| `--test` | `str` | `""` | Run a single test by ID (e.g. `A03`) |
| `--test-file` | `str` | `"biomethane_mcp_tests.md"` | Path to the test markdown file |
| `--server-script` | `str` | `"bio_methane_operations_mcp_server.v5.py"` | Path to MCP server script |
| `--http` | `flag` | `False` | Use HTTP transport for MCP |
| `--mcp-port` | `int` | `3000` | Port for MCP HTTP transport |
| `--context-length` | `int` | `4096` | Context length for LLM |
| `--output` | `str` | `"../reports/mcp_test_report"` | Output file base name (no extension) |
| `--dry-run` | `flag` | `False` | Skip real MCP calls, use stub tool results |

**CLI examples:**

```bash
# Run all tests against LM Studio
python src/run_mcp_tests.py

# Run all tests against Anthropic
python src/run_mcp_tests.py --backend anthropic

# Run a single test
python src/run_mcp_tests.py --test A03

# Custom server URL and model
python src/run_mcp_tests.py --base-url http://localhost:1234/v1 --model gemma-3-12b

# Dry-run with stub tool results
python src/run_mcp_tests.py --dry-run
```

**Model defaults (when `--model` is empty):**

| Backend | Default model |
|---------|---------------|
| `anthropic` | `claude-sonnet-4-20250514` |
| `llama-cpp` / `lmstudio` | `Qwen3-4B-Thinking-2507-MLX-4bit-Q4_K_L` |

---

### Test file format

The test file (`biomethane_mcp_tests.md`) uses `## TEST:` section markers with three mandatory fields per test:

```markdown
## TEST: A01
**section:** Plant State & Alerts
**prompt:** What is the current plant status summary?
**expect:** Returns overall_status, key_readings including temp, pH, CH4, H2S, purity, and a one_line_summary string.
```

Sections are split by `^## TEST:` (multiline regex). The line immediately following the header is the test ID. Fields are matched by case-insensitive `**key:**` prefix.

Tests are grouped into lettered sections (A through H) covering:
- **A:** Plant State & Alerts (7 tests)
- **B:** Feedstock Blending (6 tests)
- **C:** KPI Rollups (3 tests)
- **D:** Lookup & Advisory (7 tests)
- **E:** Calibration — Buswell, Energy, C/N, OLR, Biodegradability (15 tests)
- **F:** AD4 Simulation — Steady State, Temperature, Washout, Perturbation (10 tests)
- **G:** EnKF State Estimation — Initialisation, Update, Status (7 tests)
- **H:** RAG Out-of-Distribution Stress Tests (15 tests)

---

## Part B — `run_mcp_tests_rag_v2.py`

### Overview

Enhanced MCP+RAG integration test runner (v2). Extends v1 with:
- RAG query augmentation (retrieves relevant documentation chunks and prepends them to prompts)
- Dynamic tool discovery from the MCP server (via `tools/list` JSON-RPC) instead of hardcoded tool definitions
- Additional AD4+EnKF tools (20+ tools total)
- Lockfile-based parallel run prevention
- Automatic timestamped log files under `logs/`
- Improved `evaluate_result` with tool-name matching against expected tools
- Project root auto-detection by searching upward for `pyproject.toml`, `setup.py`, `.git`, etc.

```python
python src/run_mcp_tests_rag_v2.py
# or:
python src/run_mcp_tests_rag_v2.py --backend anthropic --test H03
```

**Requirements:** `pip install openai anthropic python-dotenv rich`; the `RAG` package must be on `PYTHONPATH`.

---

### Constants

#### `PROJECT_ROOT`

```python
PROJECT_ROOT: Path
```

Auto-detected by `_find_project_root()` which walks upward from the script location looking for anchor files (`pyproject.toml`, `setup.py`, `setup.cfg`, `.git`, `requirements.txt`). Falls back to reading `PROJECT_ROOT` from a nearby `.env`, then to the parent directory with a warning.

#### `HAS_RAG`

```python
HAS_RAG: bool
```

`True` if `RAG.scripts_for_rag.query_rag.query` was imported successfully, `False` otherwise. When `False`, `rag_query()` becomes a no-op returning `{"text": []}`.

#### `VENV_PYTHON` | `MCP_SERVER_SCRIPT`

Resolved from environment (`.env`) with fallback logic. `VENV_PYTHON` may be relative (e.g. `.venv/bin/python`) — resolved against `PROJECT_ROOT`. Falls back to `sys.executable` if the venv python is not found.

HuggingFace cache environment variables are also set:

```python
os.environ.setdefault("HF_HOME", ...)
os.environ.setdefault("TRANSFORMERS_CACHE", ...)
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", ...)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
```

#### `MCP_TOOLS`

```python
MCP_TOOLS: list[dict]
```

20+ tool definitions (versus 14 in v1). Adds:

| Tool | Description |
|------|-------------|
| `get_vfa_alkalinity_ratio` | Calculate VFA/alkalinity ratio (fos/tac) and acidification risk |
| `ad4_simulate` | Run AD4 digestion simulation at steady operating conditions |
| `ad4_critical_dilution_rate` | Find washout dilution rate threshold (temperature-corrected) |
| `ad4_perturbation_test` | Simulate substrate overload spike and test recovery |
| `enkf_initialise` | Initialise Ensemble Kalman Filter for state estimation |
| `enkf_update` | Advance EnKF by one day using plant_state sensors |
| `enkf_status` | Return current EnKF filter status and latest estimate |

**Dynamic tool override:** If `build_mcp_client` successfully calls `tools/list`, the returned tools replace the hardcoded `MCP_TOOLS` for that session via `convert_mcp_to_openai()`.

#### `SYSTEM_PROMPT`

Similar to v1 but includes `get_vfa_alkalinity_ratio` in the available tools list and uses slightly different wording ("ALWAYS call a tool" instead of "CRITICAL...NEVER calculate manually").

---

### Functions

All functions from v1 (`parse_test_file`, `dispatch_tool`, `run_with_openai_compat`, `run_with_anthropic`, `evaluate_result`, `write_report`, `build_mcp_client`) are re-implemented with enhancements.

#### `parse_test_file`

Identical to v1 — splits `## TEST:` markers, extracts `id`, `section`, `prompt`, `expect`.

#### `dispatch_tool` (enhanced)

Additional parameter mappings compared to v1:

| Mapping | v1 | v2 |
|---------|----|----|
| `update_plant_state` | `readings` → `updates` | Same |
| `check_all_alerts` | → `check_alerts` | Same |
| `blend_feedstocks` | — | `feedstocks` → `recipe`, plus nested array item key mapping (`feedstock` → `name`, `quantity`/`amount` → `wet_tonnes`) |

**Thread safety:** v2 wraps the MCP call in a `threading.Thread` with a 30-second timeout, returning `{"error": "Tool {name} timed out after 30s"}` if the tool hangs.

#### `run_with_openai_compat` (enhanced)

Differences from v1:
- Uses `max_tokens=2048` uniformly (not `context_length // 2`)
- Handles Gemma 4 EOG bug: falls back to `reasoning_content` if `content` is empty

#### `evaluate_result` (enhanced)

Significantly improved evaluation logic versus v1:

| Feature | v1 | v2 |
|---------|----|----|
| Tool validation | None (only checks "was any tool called?") | Matches expected tool names from `expect` field against `MCP_TOOLS` |
| "No tool expected" tests | Unhandled | Detects `"does not call"`, `"not call"`, `"no tool"`, `"recommends"` in `expect` field |
| H-series tests | Same as others | Requires at least ONE expected tool (not all) |
| H15 special case | — | Accepts equivalent tool combination (`get_plant_state` + `get_vfa_alkalinity_ratio` + `get_operational_reference` as alternative to `check_alerts`) |
| Negative test IDs | `A05`, `A06`, `B03`, `B04`, `D07` | Extended: `E04`, `E09`, `E12`, `F05`, `F10`, `G06` |
| Expected tool extraction | — | Intersects `expect` field text with known tool names from `MCP_TOOLS` |

#### `write_report`

Identical to v1 — writes JSON and markdown reports.

#### `build_mcp_client` (enhanced)

Differences from v1:
- Resolves `PYTHONPATH` dynamically from the venv's `site-packages` directory instead of hardcoded paths
- Calls `tools/list` and converts results via `convert_mcp_to_openai()` to dynamically set `MCP_TOOLS`
- Falls back to hardcoded `MCP_TOOLS` if the dynamic discovery fails

**`convert_mcp_to_openai`:**

```python
def convert_mcp_to_openai(mcp_tools: list[dict]) -> list[dict]
```

Converts MCP `tools/list` format to OpenAI function-calling format. Falls back to `MCP_TOOLS` (hardcoded list) if the result is empty.

---

### CLI (`main`)

```bash
python src/run_mcp_tests_rag_v2.py [options]
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--backend` | `str` | `"llama-cpp"` | One of `llama-cpp`, `lmstudio`, `anthropic` |
| `--base-url` | `str` | `http://localhost:8082/v1` | Base URL for OpenAI-compatible backend |
| `--model` | `str` | `"gemma-4-E4B-it-Q8_0.gguf"` | Model name (different default from v1) |
| `--test` | `str` | `""` | Run a single test by ID (e.g. `A03`) |
| `--test-file` | `str` | `"docs/biomethane_mcp_tests.md"` | Path to test file (different default from v1) |
| `--server-script` | `str` | `"src/bio_methane_operations_mcp_server_v5.py"` | Path to MCP server script (same default as v1) |
| `--http` | `flag` | `False` | Use HTTP transport |
| `--mcp-port` | `int` | `3000` | Port for MCP HTTP transport |
| `--context-length` | `int` | `8192` | Context length (higher default than v1) |
| `--output` | `str` | `"reports/mcp_test_report"` | Output base path (different default from v1) |
| `--dry-run` | `flag` | `False` | Skip real MCP calls |

#### Lockfile-based parallel run prevention

On startup, `main()` acquires an exclusive lock on `.test_runner.lock` (in `PROJECT_ROOT`):

```python
lock_file_path = PROJECT_ROOT / ".test_runner.lock"
fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
```

If another runner is already running, exits with `ERROR: Another test runner is already running`.

#### Timestamped log files

Standard output and error are duplicated to timestamped log files under `logs/`:

| Scenario | Log file pattern |
|----------|-----------------|
| Single test (`--test A03`) | `logs/A03_test_2026-05-31_12-00-00.log` |
| Full test run | `logs/full_test_2026-05-31_12-00-00.log` |

Uses a `TeeOutput` class that writes to both terminal and log file simultaneously.

#### RAG query augmentation

Before each test, if `HAS_RAG` is `True`:

```python
rag_results = rag_query(user_prompt, n_results=3)
docs = rag_results.get("text", [])
```

If documents are returned, the prompt is augmented:

```
RELEVANT KNOWLEDGE FROM documentation:
[From source_file]:
{doc_content[:500]}

...

============================================================
Question: {user_prompt}
```

If no documents are returned, the original prompt is used unchanged.

#### Cleanup

On completion:
1. Restores `sys.stdout` and `sys.stderr`.
2. Closes the log file.
3. Releases the lockfile (via `flock LOCK_UN`) and removes `.test_runner.lock`.

---

### Key differences: v1 vs v2

| Aspect | `run_mcp_tests.py` (v1) | `run_mcp_tests_rag_v2.py` (v2) |
|--------|------------------------|--------------------------------|
| **Project root** | From `env_config.py` or hardcoded fallback | Auto-detects by searching upward for anchor files |
| **RAG support** | None | Queries `RAG.scripts_for_rag.query_rag` to augment prompts |
| **MCP tool discovery** | Hardcoded `MCP_TOOLS` (14 tools) | Hardcoded (20+ tools) + dynamic from `tools/list` |
| **AD4 tools** | No | `ad4_simulate`, `ad4_critical_dilution_rate`, `ad4_perturbation_test` |
| **EnKF tools** | No | `enkf_initialise`, `enkf_update`, `enkf_status` |
| **`get_vfa_alkalinity_ratio`** | No | Yes |
| **Tool dispatch** | Simple call | Thread with 30s timeout, nested array param mapping |
| **`evaluate_result`** | Basic heuristic | Tool-name matching, expected tool validation |
| **Parallel protection** | None | `fcntl` lockfile |
| **Logging** | Terminal only | Terminal + timestamped file in `logs/` |
| **Default model** | `Qwen3-4B-Thinking-2507-MLX-4bit-Q4_K_L` | `gemma-4-E4B-it-Q8_0.gguf` |
| **Default test file** | `biomethane_mcp_tests.md` (in `src/`) | `docs/biomethane_mcp_tests.md` |
| **Default server script** | `bio_methane_operations_mcp_server.v5.py` | `src/bio_methane_operations_mcp_server_v5.py` |
| **Default output** | `../reports/mcp_test_report` | `reports/mcp_test_report` |
| **Default context length** | `4096` | `8192` |
| **Gemma 4 fallback** | No | Yes (`reasoning_content` EOG bug) |
| **GC** | After each round + end | Every 5 tests + after each round |
