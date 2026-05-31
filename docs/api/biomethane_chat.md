# Biomethane Chat UI — API Reference

**Module**: `src/biomethane_chat.py`

A Gradio-based interactive chat interface that connects a local `llama-server` (Gemma 4) to the MCP server, providing real-time plant data access, feedstock planning, AD4 simulation, and Kalman filter state estimation through natural language conversation.

---

## Usage

```bash
# Default — reads LLAMA_SERVER_URL, MODEL_NAME, VENV_PYTHON, MCP_SERVER_SCRIPT from .env
python src/biomethane_chat.py

# Custom Gradio port
python src/biomethane_chat.py --port 7861

# Public tunnel (Gradio share link)
python src/biomethane_chat.py --share

# Skip MCP server launch (UI only, no tool calls)
python src/biomethane_chat.py --no-mcp

# Kill stale processes on startup
python src/biomethane_chat.py --kill-previous
```

Requires `gradio`, `openai`, and `python-dotenv`. The `llama-server` and MCP server scripts must be reachable (see Configuration constants below).

---

## Configuration Constants

Defined at module level (`biomethane_chat.py:107–141`).

| Constant | Source | Default | Description |
|---|---|---|---|
| `PROJECT_ROOT` | `_find_project_root()` | auto-detected | Root directory anchored on `pyproject.toml`, `setup.py`, `.git`, etc. |
| `LLAMA_SERVER_URL` | `.env` / env var | `http://localhost:8082/v1` | OpenAI-compatible endpoint for `llama-server` |
| `MODEL_NAME` | `.env` / env var | `gemma-4-E4B-it-Q8_0.gguf` | Model identifier passed in chat completions |
| `VENV_PYTHON` | `.env` / env var | `.venv/bin/python` | Python interpreter for the MCP subprocess; falls back to `sys.executable` |
| `MCP_SERVER_SCRIPT` | `.env` / env var | `src/bio_methane_operations_mcp_server_v5.py` | Path to the MCP stdio server script |

### `SYSTEM_PROMPT`

```python
# biomethane_chat.py:129–141
SYSTEM_PROMPT = """You are a biomethane plant operations assistant with access to live plant data and simulation tools.

Use the available MCP tools to answer questions accurately:
- get_plant_state / update_plant_state / check_alerts — live plant readings
- blend_feedstocks / list_feedstocks / olr_from_recipe — feedstock planning
- get_kpi_summary — production KPIs (daily / weekly / monthly)
- get_operational_reference — operational guidance and troubleshooting
- buswell_bmp / buswell_bmp_by_class — theoretical BMP calculations
- cn_ratio_from_composition / biodegradability_coefficient — calibration
- ad4_simulate / ad4_critical_dilution_rate / ad4_perturbation_test — AD4 digestion modelling
- enkf_initialise / enkf_update / enkf_status — Kalman filter state estimation

Always call a tool before giving numerical answers. Summarise results with units."""
```

---

## Utility Functions

### `is_port_available(port, host="127.0.0.1") -> bool`

`biomethane_chat.py:45`

Opens a temporary socket to check whether `host:port` is free. Returns `True` if the bind succeeds, `False` if the port is already in use.

### `kill_previous_processes(script_name, also_kill=None) -> list[int]`

`biomethane_chat.py:55`

Runs `ps aux` and kills any process whose command line contains any of the given patterns (excluding `grep` and the current PID). Returns a list of killed PIDs.

| Parameter | Type | Description |
|---|---|---|
| `script_name` | `str` | Base pattern to match (e.g., `"biomethane_chat.py"`) |
| `also_kill` | `list[str] \| None` | Additional patterns to match |

---

## Class `MCPClient`

`biomethane_chat.py:145–263`

Thin stdio JSON-RPC client that launches the MCP server as a subprocess, communicates via stdin/stdout, and exposes tools for LLM function calling.

### `__init__(self)`

`biomethane_chat.py:148–155`

Initialises internal state:

| Attribute | Type | Description |
|---|---|---|
| `proc` | `subprocess.Popen \| None` | MCP subprocess handle |
| `reader` | `Thread \| None` | Background thread that reads stdout lines into `r_queue` |
| `stderr_t` | `Thread \| None` | Background thread that drains stderr (prevents pipe blockage) |
| `r_queue` | `queue.Queue` | Thread-safe queue for incoming JSON-RPC responses |
| `_lock` | `threading.Lock` | Mutex protecting stdin writes |
| `_req_id` | `int` | Monotonically increasing JSON-RPC request ID |
| `tools` | `list[dict]` | OpenAI-format tool descriptors (populated by `start()`) |

### `start() -> str`

`biomethane_chat.py:157–203`

Launches the MCP stdio subprocess in three phases:

1. **Subprocess** — `subprocess.Popen([VENV_PYTHON, MCP_SERVER_SCRIPT], ...)` with `PYTHONPATH` extended to include the venv `site-packages`.
2. **Initialise** — sends `{"method": "initialize", "params": {"protocolVersion": "2024-11-05", ...}}` and awaits a response (5 s timeout).
3. **Discover tools** — sends `{"method": "tools/list"}` and converts the result into the OpenAI tool-calling schema:
   ```python
   {"type": "function", "function": {"name": t["name"], "description": ..., "parameters": {"type": "object", "properties": ...}}}
   ```

Returns a status string:
- `"✓ MCP server ready — {N} tools loaded"` on success.
- `"✗ MCP server failed to start: {error}"` on exception.

### `stop()`

`biomethane_chat.py:205–213`

Calls `proc.terminate()` with a 5-second `wait()` timeout; if the process hasn't exited, escalates to `proc.kill()` with a 3-second timeout. Sets `self.proc = None`.

### `call_tool(name, arguments) -> str`

`biomethane_chat.py:215–229`

Sends a `tools/call` JSON-RPC request via `_send_recv` and extracts text content from the response.

| Parameter | Type | Description |
|---|---|---|
| `name` | `str` | Tool name (e.g. `"get_plant_state"`) |
| `arguments` | `dict` | Tool-specific parameters |

Returns a JSON string from the tool's `content` array (text items joined by `\n`), or `{"error": "No response from MCP server"}` if the response times out.

### Internal helpers

| Method | Line | Description |
|---|---|---|
| `_next_id() -> int` | 232 | Atomically increments and returns `_req_id` |
| `_stderr_reader()` | 236 | Drains `proc.stderr` line by line to prevent pipe deadlock |
| `_read_loop()` | 240 | Reads `proc.stdout` line by line, parses JSON, and pushes to `r_queue` |
| `_send(payload)` | 249 | Serialises `payload` as JSON + newline and writes to `proc.stdin` (thread-safe via `_lock`) |
| `_recv(timeout=10) -> dict \| None` | 254 | Blocks on `r_queue.get(timeout)`; returns `None` if empty |
| `_send_recv(payload, timeout=30) -> dict \| None` | 260 | `_send` then `_recv` — the primary RPC call pattern |

---

## Function `chat_turn`

`biomethane_chat.py:266–353`

```python
chat_turn(user_message, history, mcp) -> tuple[str, list, str]
```

One full conversation turn: builds messages from history, invokes the LLM (optionally with tool calls), and appends the result.

### Parameters

| Parameter | Type | Description |
|---|---|---|
| `user_message` | `str` | The user's latest input |
| `history` | `list` | Flattened list of alternating user/assistant strings from Gradio state |
| `mcp` | `MCPClient` | Initialised client with populated `tools` list |

### Returns

| Element | Type | Description |
|---|---|---|
| `final_text` | `str` | Final assistant response (after all tool loops) |
| `new_history` | `list` | `history + [[user_message, final_text]]` |
| `trace_md` | `str` | Markdown-formatted tool call trace |

### Algorithm

1. **Build messages** — prepend `SYSTEM_PROMPT`, iterate over flattened history, append user message.
2. **Routing layer** — if `biomethane_router` is available and tools exist, calls `_router_route(user_message, mcp)` to classify intent and inject deterministic tool results as a system message.
3. **LLM loop** (up to 8 rounds):
   - Calls `client.chat.completions.create(model=MODEL_NAME, tools=mcp.tools, tool_choice="auto", temperature=0.1, max_tokens=2048)`.
   - If the response has no `tool_calls`, sets `final_text` and exits.
   - Otherwise appends the assistant message, iterates tool calls, invokes each via `mcp.call_tool()`, and appends `{"role": "tool", "tool_call_id": ..., "content": ...}` messages.
4. **Format trace** — each tool invocation is rendered as a markdown block with the function name, arguments as JSON, and the result (truncated to 800 chars).

---

## Function `build_ui`

`biomethane_chat.py:357–493`

```python
build_ui(mcp, mcp_status) -> gr.Blocks
```

Constructs and returns the Gradio Blocks UI.

### Parameters

| Parameter | Type | Description |
|---|---|---|
| `mcp` | `MCPClient` | The MCP client instance (used inside closure handlers) |
| `mcp_status` | `str` | Status string from `mcp.start()` to display in the UI |

### UI Layout

```
┌──────────────────────────────────────────────────────┐
│  ⬡ BIOMETHANE PLANT ASSISTANT                       │
│  Gemma 4 · http://localhost:8082/v1 · MCP stdio     │
│  ✓ MCP server ready — 26 tools loaded               │
├────────────────────────┬─────────────────────────────┤
│                        │                             │
│   Conversation         │   Tool calls                │
│   [Chatbot component]  │   ┌─────────────────────┐  │
│                        │   │ *Tool calls will     │  │
│                        │   │ appear here after    │  │
│                        │   │ each message.*       │  │
│                        │   └─────────────────────┘  │
│                        │                             │
│   [🌱 Plant status]    │                             │
│   [⚠️ Check alerts]    │                             │
│   [📊 Today's KPIs]    │                             │
│                        │                             │
│   [🧪 Blend recipe]    │                             │
│   [⚡ D_crit check]     │                             │
│   [🔬 AD4 sim]         │                             │
│                        │                             │
│   [ Input text box                  ] [Send ↵]      │
│                                      [Clear]        │
├────────────────────────┴─────────────────────────────┤
│  Cleared.                                            │
└──────────────────────────────────────────────────────┘
```

- **Theme**: Gradio Base theme, `emerald` primary / `zinc` neutral, IBM Plex Mono font.
- **Left column** (scale 3): Chatbot, suggestion buttons, text input + send/clear.
- **Right column** (scale 2): Tool trace markdown panel.
- **Suggestion buttons** fill the textbox with predefined prompts (plant status, alerts, KPIs, blend recipe, critical dilution rate, AD4 simulation).

### Event Handlers

| Event | Handler | Description |
|---|---|---|
| Send button click | `respond()` | Calls `chat_turn()`, updates chatbot display, history state, and tool trace |
| Textbox Enter | `respond()` | Same handler as Send button |
| Suggestion buttons | `lambda` | Fill textbox with prompt text (no submission) |
| Clear button | `clear_all()` | Resets history state, tool trace, and chatbot to empty |

The internal `respond()` closure (`biomethane_chat.py:421–444`):
- Returns immediately for empty/whitespace input.
- Rejects image/file inputs via a simple string check.
- Sanitises history by filtering to string entries only.
- On success, formats `new_history` into Gradio's dict-based Chatbot format (`{"role": "user", "content": ...}` / `{"role": "assistant", "content": ...}`).
- On exception, returns an error message in the tool trace.

### `clear_all()`

`biomethane_chat.py:484–486`

```python
def clear_all():
    return [], "*Cleared.*", []
```

Resets `history_state`, `tool_trace`, and `chatbot` to empty.

---

## Function `main`

`biomethane_chat.py:497–543`

CLI entry point. Parses arguments, optionally kills previous process instances, checks port availability, starts the MCP subprocess, launches the Gradio UI, and ensures `mcp.stop()` is called in the `finally` block.

### CLI Arguments

| Argument | Type | Default | Description |
|---|---|---|---|
| `--port` | `int` | `7860` | Gradio server port |
| `--share` | flag | `False` | Create a public Gradio tunnel link |
| `--no-mcp` | flag | `False` | Skip MCP server launch (UI only) |
| `--kill-previous` | flag | `False` | Kill stale processes of the same script on startup |

### Startup flow

1. If `--kill-previous` is set or the requested port is occupied, run `kill_previous_processes()`.
2. Verify port availability; exit with error if still blocked.
3. Instantiate `MCPClient`; if `--no-mcp` is not set, call `mcp.start()`.
4. Call `build_ui(mcp, mcp_status)`.
5. Launch with `demo.launch(server_port=args.port, share=args.share, inbrowser=True, show_error=True)`.
6. On shutdown (incl. Ctrl+C / exception), `mcp.stop()` is called in the `finally` block.

---

## Dependencies

| Package | Purpose |
|---|---|
| `gradio` | Web UI framework |
| `openai` | OpenAI-compatible LLM client for `llama-server` |
| `python-dotenv` | `.env` file loading |
| `json`, `socket`, `subprocess`, `threading`, `queue`, `signal`, `pathlib` | Standard library |

Optional (graceful fallback if missing):
- `biomethane_router` — deterministic intent classification and tool chaining.

---

## Remarks

- The Gradio UI uses `gr.State` to track conversation history as a flat list `[user1, asst1, user2, asst2, ...]`. The chatbot display uses the newer dict-based format (`{"role": "user", "content": ...}`).
- Tool results in the trace panel are truncated to 800 characters to keep the UI responsive.
- The LLM loop has a hard limit of 8 rounds to prevent runaway tool calling.
- The MCP subprocess is started once and reused for the entire session; it is terminated when the Gradio server shuts down.
