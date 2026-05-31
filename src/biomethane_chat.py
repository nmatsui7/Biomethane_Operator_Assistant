"""
biomethane_chat.py — Interactive Chat Interface for Biomethane MCP Server
=============================================================================
A Gradio-based chat UI that connects to your local llama-server (Gemma 4)
and MCP server, with full tool-call transparency.

Usage
-----
  python biomethane_chat.py                  # uses .env defaults
  python biomethane_chat.py --port 7860      # custom Gradio port
  python biomethane_chat.py --share          # create a public tunnel link

Requirements
------------
  pip install gradio openai python-dotenv

The llama-server and MCP server must already be running:
  ./run_gemma.sh                             # starts llama-server on :8082
  python src/biomethane_operations_mcp_server_v5.py   # starts MCP server
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import queue
import signal
from pathlib import Path

import gradio as gr

# Router import — graceful fallback if module not found
try:
    from biomethane_router import route as _router_route
    _ROUTER_AVAILABLE = True
except ImportError:
    _ROUTER_AVAILABLE = False


# ── Utility functions ──────────────────────────────────────────────────────────
def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a port is available for use."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
            return True
    except OSError:
        return False


def kill_previous_processes(script_name: str, also_kill: list[str] = None) -> list[int]:
    """
    Kill previous processes running the script and optionally other patterns.
    Returns list of PIDs that were killed.
    """
    killed_pids = []
    patterns = [script_name]
    if also_kill:
        patterns.extend(also_kill)
    
    try:
        result = subprocess.run(
            ["ps", "aux"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            should_kill = any(p in line for p in patterns)
            if should_kill and "grep" not in line:
                parts = line.split()
                if len(parts) > 1:
                    try:
                        pid = int(parts[1])
                        if pid != os.getpid():
                            os.kill(pid, signal.SIGTERM)
                            killed_pids.append(pid)
                    except (ValueError, PermissionError):
                        pass
    except Exception as e:
        print(f"Warning: Could not kill previous processes: {e}")
    return killed_pids


# ── Project root (same anchor logic as the test runner) ───────────────────────
def _find_project_root() -> Path:
    anchors = ("pyproject.toml", "setup.py", "setup.cfg", ".git", "requirements.txt")
    start = Path(__file__).resolve().parent
    for directory in [start, *start.parents]:
        if any((directory / a).exists() for a in anchors):
            return directory
    for env_candidate in (start / ".env", start.parent / ".env"):
        if env_candidate.exists():
            for line in env_candidate.read_text().splitlines():
                line = line.strip()
                if line.startswith("PROJECT_ROOT="):
                    root = Path(line.split("=", 1)[1].strip())
                    if root.is_dir():
                        return root
    return start.parent


PROJECT_ROOT = _find_project_root()

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

# ── Config from .env ──────────────────────────────────────────────────────────
LLAMA_SERVER_URL = os.environ.get("LLAMA_SERVER_URL", "http://localhost:8082/v1")
MODEL_NAME       = os.environ.get("MODEL_NAME", "gemma-4-E4B-it-Q8_0.gguf")

_venv_raw  = os.environ.get("VENV_PYTHON", ".venv/bin/python")
_venv_path = Path(_venv_raw) if Path(_venv_raw).is_absolute() else PROJECT_ROOT / _venv_raw
VENV_PYTHON = str(_venv_path) if _venv_path.exists() else sys.executable

MCP_SERVER_SCRIPT = os.environ.get(
    "MCP_SERVER_SCRIPT",
    str(PROJECT_ROOT / "src" / "bio_methane_operations_mcp_server_v5.py"),
)

# ── System prompt (mirrors the test runner) ───────────────────────────────────
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


# ── MCP client (stdio subprocess) ─────────────────────────────────────────────
class MCPClient:
    """Thin stdio JSON-RPC client for the MCP server subprocess."""

    def __init__(self):
        self.proc     = None
        self.reader   = None
        self.stderr_t = None
        self.r_queue  = queue.Queue()
        self._lock    = threading.Lock()
        self._req_id  = 0
        self.tools    = []

    def start(self) -> str:
        """Launch MCP server subprocess. Returns status string."""
        try:
            env = os.environ.copy()
            _venv_dir   = Path(VENV_PYTHON).parent.parent
            _site_pkgs  = list(_venv_dir.glob("lib/python3.*/site-packages"))
            if _site_pkgs:
                env["PYTHONPATH"] = f"{_site_pkgs[0]}:{env.get('PYTHONPATH', '')}"

            self.proc = subprocess.Popen(
                [VENV_PYTHON, MCP_SERVER_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
            # Background reader threads
            self.stderr_t = threading.Thread(target=self._stderr_reader, daemon=True)
            self.stderr_t.start()
            self.reader = threading.Thread(target=self._read_loop, daemon=True)
            self.reader.start()
            time.sleep(1.5)  # give server time to initialise

            # Initialise + discover tools
            self._send({"jsonrpc": "2.0", "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05",
                                   "capabilities": {},
                                   "clientInfo": {"name": "biomethane-chat", "version": "1.0"}},
                        "id": self._next_id()})
            self._recv(timeout=5)

            tools_resp = self._send_recv({"jsonrpc": "2.0", "method": "tools/list",
                                          "params": {}, "id": self._next_id()})
            raw_tools = (tools_resp or {}).get("result", {}).get("tools", [])
            self.tools = [
                {"type": "function", "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
                }}
                for t in raw_tools
            ]
            return f"✓ MCP server ready — {len(self.tools)} tools loaded"
        except Exception as e:
            return f"✗ MCP server failed to start: {e}"

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
            self.proc = None

    def call_tool(self, name: str, arguments: dict) -> str:
        resp = self._send_recv({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
            "id": self._next_id(),
        })
        if not resp:
            return json.dumps({"error": "No response from MCP server"})
        result = resp.get("result", {})
        if "error" in resp:
            return json.dumps(resp["error"])
        content = result.get("content", [])
        texts = [c.get("text", "") for c in content if isinstance(c, dict)]
        return "\n".join(texts) or json.dumps(result)

    # ── Internal helpers ───────────────────────────────────────────────────────
    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _stderr_reader(self):
        for _ in self.proc.stderr:
            pass

    def _read_loop(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                try:
                    self.r_queue.put(json.loads(line))
                except json.JSONDecodeError:
                    pass

    def _send(self, payload: dict):
        with self._lock:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()

    def _recv(self, timeout=10) -> dict | None:
        try:
            return self.r_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _send_recv(self, payload: dict, timeout=30) -> dict | None:
        self._send(payload)
        return self._recv(timeout=timeout)


# ── LLM chat with tool loop ────────────────────────────────────────────────────
def chat_turn(user_message: str, history: list, mcp: MCPClient) -> tuple[str, list, str]:
    """
    One conversation turn.
    Returns (assistant_text, updated_history, tool_trace_markdown).
    """
    from openai import OpenAI
    client = OpenAI(base_url=LLAMA_SERVER_URL, api_key="lm-studio")

    # Build messages from Gradio history format [[user, assistant], ...]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for i in range(0, len(history), 2):
        if i < len(history) and isinstance(history[i], str):
            messages.append({"role": "user",    "content": history[i]})
        if i + 1 < len(history) and isinstance(history[i + 1], str):
            messages.append({"role": "assistant", "content": history[i + 1]})
    messages.append({"role": "user", "content": user_message})

    tool_trace = []
    final_text = ""

    # ── Deterministic routing layer ───────────────────────────────────────────
    # Classify intent, run tool chain, inject structured context before LLM.
    # UNKNOWN intent falls through unchanged — zero regression on unmatched queries.
    router_intent  = "UNKNOWN"
    router_tools   = []
    if _ROUTER_AVAILABLE and mcp.tools:
        router_context, router_intent, router_tools = _router_route(user_message, mcp)
        if router_context:
            messages.append({"role": "system", "content": router_context})
            # Record router activity in the tool trace panel
            tool_trace.append(
                f"**`[ROUTER: {router_intent}]`**\n"
                f"*Tools invoked deterministically:* "
                f"`{'`, `'.join(router_tools) if router_tools else 'none'}`"
            )

    for round_num in range(8):
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            tools=mcp.tools or [],
            tool_choice="auto",
            temperature=0.1,
            max_tokens=2048,
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            final_text = msg.content or ""
            # Gemma 4 EOG fallback
            if not final_text.strip():
                try:
                    d = response.model_dump()
                    final_text = (d["choices"][0]["message"]
                                  .get("reasoning_content", "")) or ""
                except Exception:
                    pass
            break

        # Append assistant tool-call turn
        messages.append(msg)

        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            result_str = mcp.call_tool(tc.function.name, args)

            # Pretty-format for trace panel
            try:
                result_pretty = json.dumps(json.loads(result_str), indent=2)
            except Exception:
                result_pretty = result_str

            tool_trace.append(
                f"**`{tc.function.name}`**\n"
                f"```json\n{json.dumps(args, indent=2)}\n```\n"
                f"*Result:*\n```json\n{result_pretty[:800]}"
                f"{'...' if len(result_pretty) > 800 else ''}\n```"
            )

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_str,
            })

    new_history = history + [[user_message, final_text]]
    trace_md = "\n\n---\n\n".join(tool_trace) if tool_trace else "*No tools called*"
    return final_text, new_history, trace_md


# ── Gradio UI ─────────────────────────────────────────────────────────────────
def build_ui(mcp: MCPClient, mcp_status: str):
    # Theme for Gradio 6.x
    theme = gr.themes.Base(
        primary_hue="emerald",
        neutral_hue="zinc",
        font=[gr.themes.GoogleFont("IBM Plex Mono")],
    )
    
    with gr.Blocks(theme=theme) as demo:

        # ── State ─────────────────────────────────────────────────────────────
        history_state = gr.State([])

        # ── Header ────────────────────────────────────────────────────────────
        with gr.Row(elem_id="header"):
            gr.HTML(
                f"<h1>⬡ BIOMETHANE PLANT ASSISTANT</h1>"
                f"<p>Gemma 4 · {LLAMA_SERVER_URL} · MCP stdio</p>"
            )

        gr.HTML(f'<div id="status-bar">{mcp_status}</div>')

        # ── Main layout ───────────────────────────────────────────────────────
        with gr.Row():
            # Left: chat
            with gr.Column(scale=3):
                chatbot = gr.Chatbot(
                    label="Conversation",
                    height=480,
                    show_label=False,
                )

                # Suggestion chips
                with gr.Row():
                    btn_plant_status = gr.Button("🌱 Plant status", size="sm", min_width=120)
                    btn_check_alerts = gr.Button("⚠️ Check alerts", size="sm", min_width=120)
                    btn_kpis = gr.Button("📊 Today's KPIs", size="sm", min_width=120)

                with gr.Row():
                    btn_blend = gr.Button("🧪 Blend recipe", size="sm", min_width=120)
                    btn_dcrit = gr.Button("⚡ D_crit check", size="sm", min_width=120)
                    btn_ad4sim = gr.Button("🔬 AD4 sim", size="sm", min_width=120)

                with gr.Row(elem_id="input-row"):
                    user_input = gr.Textbox(
                        placeholder="Ask about plant status, feedstock blending, KPIs, simulations…",
                        show_label=False,
                        lines=2,
                        elem_id="user-input",
                        scale=5,
                    )
                    with gr.Column(scale=1, min_width=160):
                        send_btn  = gr.Button("Send ↵",  variant="primary", elem_id="send-btn")
                        clear_btn = gr.Button("Clear",               elem_id="clear-btn")

            # Right: tool trace
            with gr.Column(scale=2):
                gr.Markdown("**Tool calls**", elem_id="tool-panel-label")
                tool_trace = gr.Markdown(
                    value="*Tool calls will appear here after each message.*",
                    elem_id="tool-panel",
                )

        # ── Event handlers ────────────────────────────────────────────────────
        def respond(user_msg, history):
            if not user_msg or not isinstance(user_msg, str) or not user_msg.strip():
                return "", history, "*No input.*", []
            
            # Reject image/file inputs and sanitize
            if "ERROR:" in str(user_msg) or "image" in str(user_msg).lower():
                return "", history, "*Image/file inputs are not supported. Please type a text question.*", []
            
            # Sanitize history - remove non-string entries
            sanitized_history = [h for h in history if isinstance(h, str)]
            
            try:
                answer, new_history, trace = chat_turn(user_msg, sanitized_history, mcp)
                # Format history for chatbot
                formatted = []
                for i in range(0, len(new_history), 2):
                    if i < len(new_history):
                        formatted.append({"role": "user", "content": new_history[i]})
                    if i + 1 < len(new_history):
                        formatted.append({"role": "assistant", "content": new_history[i + 1]})
                return "", new_history, trace, formatted
            except Exception as e:
                error_msg = f"*Error: {str(e)}*"
                return "", history, error_msg, []
         
        send_btn.click(
            fn=respond,
            inputs=[user_input, history_state],
            outputs=[user_input, history_state, tool_trace, chatbot],
        )

        user_input.submit(
            fn=respond,
            inputs=[user_input, history_state],
            outputs=[user_input, history_state, tool_trace, chatbot],
        )

        # Suggestion button handlers - fill textbox with predefined prompts
        btn_plant_status.click(
            fn=lambda: "What is the current plant state?",
            outputs=[user_input],
        )
        btn_check_alerts.click(
            fn=lambda: "Check all alert thresholds",
            outputs=[user_input],
        )
        btn_kpis.click(
            fn=lambda: "Give me today's KPIs",
            outputs=[user_input],
        )
        btn_blend.click(
            fn=lambda: "Blend 30t Cattle slurry, 15t Maize silage, 5t Food waste — daily biomethane yield?",
            outputs=[user_input],
        )
        btn_dcrit.click(
            fn=lambda: "What is the critical dilution rate at current temperature?",
            outputs=[user_input],
        )
        btn_ad4sim.click(
            fn=lambda: "Run AD4 simulation at D=0.05, COD=25 g/L",
            outputs=[user_input],
        )

        def clear_all():
            """Properly reset all state including the chatbot display."""
            return [], "*Cleared.*", []
        
        clear_btn.click(
            fn=clear_all,
            outputs=[history_state, tool_trace, chatbot],
        )

    return demo


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Biomethane MCP Chat UI")
    parser.add_argument("--port",  type=int, default=7860, help="Gradio port (default 7860)")
    parser.add_argument("--share", action="store_true",   help="Create public Gradio tunnel")
    parser.add_argument("--no-mcp", action="store_true",  help="Skip MCP server (UI only)")
    parser.add_argument("--kill-previous", action="store_true", help="Kill previous processes on startup")
    args = parser.parse_args()

    # Kill previous processes if requested or if port is in use
    script_name = Path(__file__).name
    if args.kill_previous or not is_port_available(args.port):
        print(f"Checking for previous processes of {script_name}...", flush=True)
        killed = kill_previous_processes(script_name)
        if killed:
            print(f"  Killed previous processes: PIDs {killed}", flush=True)
            time.sleep(1)  # Give processes time to die

    # Check port availability
    if not is_port_available(args.port):
        print(f"ERROR: Port {args.port} is still in use.", flush=True)
        print(f"  Use --kill-previous to force kill, or --port to use a different port.", flush=True)
        sys.exit(1)

    mcp = MCPClient()
    if args.no_mcp:
        mcp_status = "⚠ MCP server disabled (--no-mcp)"
    else:
        print("Starting MCP server…", flush=True)
        mcp_status = mcp.start()
        print(mcp_status, flush=True)

    try:
        demo = build_ui(mcp, mcp_status)
        print(f"\nChat UI → http://localhost:{args.port}\n", flush=True)
        demo.launch(
            server_port=args.port,
            share=args.share,
            show_error=True,
            inbrowser=True,
            theme=gr.themes.Base(
                primary_hue="emerald",
                neutral_hue="zinc",
                font=gr.themes.GoogleFont("IBM Plex Mono"),
            ),
        )
    finally:
        mcp.stop()


if __name__ == "__main__":
    main()
