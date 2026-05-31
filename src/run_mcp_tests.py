"""
run_mcp_tests.py — Biomethane MCP Server LLM Test Runner
=========================================================
Reads biomethane_mcp_tests.md, sends each prompt to an LLM
(LM Studio local OR Anthropic remote), captures tool calls and
responses, and writes a structured report.

Usage
-----
# Against LM Studio (default):
python run_mcp_tests.py

# Against Anthropic (Claude):
python run_mcp_tests.py --backend anthropic

# Run a single test by id:
python run_mcp_tests.py --test A03

# Custom LM Studio base URL or model:
python run_mcp_tests.py --base-url http://localhost:1234/v1 --model gemma-3-12b

Requirements
------------
pip install openai anthropic rich

Environment variables (only needed for Anthropic backend):
  ANTHROPIC_API_KEY=sk-ant-...

The MCP server must already be running:
  python src/bio_methane_operations_mcp_server_v5.py
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# Load environment configuration
try:
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.env_config import VENV_PYTHON, MCP_SERVER_SCRIPT
except ImportError:
    VENV_PYTHON = ".venv/bin/python"
    MCP_SERVER_SCRIPT = "src/bio_methane_operations_mcp_server_v5.py"

# ── optional rich for coloured terminal output ─────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich import print as rprint

    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    console = None

# ── MCP tool definitions sent to the LLM ──────────────────────────────────────
# Mirrors the tools exposed by bio_methane_operations_mcp_server_v5.py so the LLM knows
# what it can call.  In a real integration the MCP client would auto-discover
# these; here we declare them inline so the runner is self-contained.

# MCP tool definitions matching bio_methane_operations_mcp_server_v5.py
MCP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_plant_state",
            "description": "Returns current plant operating state (all sensor readings).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_plant_state",
            "description": "Update plant state values. Takes a dict of parameter names to numeric values.",
            "parameters": {
                "type": "object",
                "properties": {
                    "updates": {
                        "type": "object",
                        "description": "Dict of parameter names to numeric values.",
                    }
                },
                "required": ["updates"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_alerts",
            "description": "Check all parameters against thresholds and return active alerts.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "blend_feedstocks",
            "description": "Calculate blend of feedstocks to achieve target OLR.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipe": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "wet_tonnes": {"type": "number"},
                            },
                            "required": ["name", "wet_tonnes"],
                        },
                        "description": "List of feedstock name + wet_tonnes pairs.",
                    },
                    "target_olr": {
                        "type": "number",
                        "description": "Target organic loading rate (default 2.5)",
                    },
                },
                "required": ["recipe"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_operational_reference",
            "description": "Look up operational guidance by topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "Topic key (e.g., fos_tac, temperature, olr, cn_ratio).",
                    }
                },
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_kpi_summary",
            "description": "Get KPI summary: daily, weekly, or monthly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Period: daily, weekly, or monthly (default daily).",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_feedstocks",
            "description": "List available feedstocks with BMP, C/N, DM, and VS/DM values.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_alert_history",
            "description": "Get recent alert history log.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of alerts to return (default 10)",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buswell_bmp",
            "description": "Calculate theoretical BMP from elemental composition using Buswell equation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "c": {"type": "number", "description": "Carbon moles (e.g. 6)"},
                    "h": {"type": "number", "description": "Hydrogen moles (e.g. 12)"},
                    "o": {"type": "number", "description": "Oxygen moles (e.g. 6)"},
                    "n": {"type": "number", "description": "Nitrogen moles (default 0)"},
                    "s": {"type": "number", "description": "Sulfur moles (default 0)"},
                },
                "required": ["c", "h", "o"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buswell_bmp_by_class",
            "description": "Calculate Buswell BMP for named substrate class.",
            "parameters": {
                "type": "object",
                "properties": {
                    "substrate_class": {
                        "type": "string",
                        "description": "Class: carbohydrate_cellulose, carbohydrate_glucose, protein_generic, lipid_tripalmitin, lipid_triolein",
                    }
                },
                "required": ["substrate_class"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_energy_conversion_factor",
            "description": "Derive kWh/Nm3 from CH4 fraction using LHV.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ch4_fraction": {
                        "type": "number",
                        "description": "CH4 mole fraction (default 0.974)",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cn_ratio_from_composition",
            "description": "Calculate C/N ratio from elemental analysis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "carbon_pct_of_vs": {"type": "number", "description": "% carbon by mass of VS"},
                    "nitrogen_pct_of_vs": {"type": "number", "description": "% nitrogen by mass of VS"},
                },
                "required": ["carbon_pct_of_vs", "nitrogen_pct_of_vs"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "olr_from_recipe",
            "description": "Calculate OLR from feedstock recipe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipe": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "wet_tonnes": {"type": "number"},
                            },
                            "required": ["name", "wet_tonnes"],
                        },
                    },
                    "digester_volume_m3": {"type": "number", "description": "Working volume (m3)"},
                },
                "required": ["recipe", "digester_volume_m3"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "biodegradability_coefficient",
            "description": "Calculate biodegradability = empirical / theoretical.",
            "parameters": {
                "type": "object",
                "properties": {
                    "substrate_class": {"type": "string"},
                    "empirical_bmp_nl_per_kg_vs": {"type": "number"},
                },
                "required": ["substrate_class", "empirical_bmp_nl_per_kg_vs"],
            },
        },
    },
]

SYSTEM_PROMPT = """You are a biomethane plant operations assistant.

CRITICAL: You MUST use the MCP tools provided below for ALL calculations. NEVER calculate manually.

Available tools - you MUST call these EXACTLY:
- get_plant_state: Returns current plant operating state
- update_plant_state(updates={}): Update plant state values
- check_alerts(): Check alert thresholds
- blend_feedstocks(recipe=[], target_olr=2.5): Calculate feedstock blend
- get_operational_reference(topic=''): Look up operational guidance
- get_kpi_summary(period='daily'): Get KPI summary
- list_feedstocks(): List feedstocks
- get_alert_history(limit=10): Get alert history
- buswell_bmp(c=0, h=0, o=0, n=0, s=0): Buswell BMP from elemental composition
- buswell_bmp_by_class(substrate_class=''): BMP for named class
- calculate_energy_conversion_factor(ch4_fraction=0.974): kWh/Nm3 from CH4%
- cn_ratio_from_composition(carbon_pct_of_vs=0, nitrogen_pct_of_vs=0): C/N ratio from elemental %
- olr_from_recipe(recipe=[], digester_volume_m3=0): OLR from recipe
- biodegradability_coefficient(substrate_class='', empirical_bmp_nl_per_kg_vs=0): empirical/theoretical

RULES:
1. ALWAYS call a tool, NEVER calculate manually even simple math
2. Use exact tool name with parameters as shown above
3. For any question requiring calculation, call the appropriate tool first
4. Return the tool's exact output, do not rephrase numbers"""

# ── markdown test file parser ──────────────────────────────────────────────────


def parse_test_file(path: Path) -> list[dict]:
    """
    Parses biomethane_mcp_tests.md into a list of test dicts:
      {id, section, prompt, expect}
    """
    text = path.read_text(encoding="utf-8")
    tests = []
    # Split on ## TEST: blocks
    blocks = re.split(r"^## TEST:\s*", text, flags=re.MULTILINE)
    for block in blocks[1:]:  # skip preamble
        lines = block.strip().splitlines()
        test_id = lines[0].strip()
        fields = {"id": test_id, "section": "", "prompt": "", "expect": ""}
        for line in lines[1:]:
            for key in ("section", "prompt", "expect"):
                prefix = f"**{key}:**"
                if line.strip().lower().startswith(prefix):
                    fields[key] = line.strip()[len(prefix) :].strip()
        tests.append(fields)
    return tests


# ── MCP tool dispatcher ────────────────────────────────────────────────────────


def dispatch_tool(name: str, arguments: dict, mcp_client) -> str:
    """
    Calls the real MCP server tool via the mcp_client and returns the
    result as a JSON string.

    mcp_client is expected to have a .call_tool(name, arguments) method.
    If None is passed (dry-run mode), returns a stub response.
    """
    if mcp_client is None:
        return json.dumps({"dry_run": True, "tool": name, "arguments": arguments})

    # Map LLM parameter names to server parameter names
    param_mapping = {
        "update_plant_state": {"readings": "updates"},
        "check_all_alerts": {},  # alias for check_alerts
    }

    # Apply parameter mapping if needed
    if name in param_mapping:
        mapped_args = {}
        for key, value in arguments.items():
            mapped_key = param_mapping[name].get(key, key)
            mapped_args[mapped_key] = value
        arguments = mapped_args

    try:
        result = mcp_client.call_tool(name, arguments)
        # Handle different result formats
        if hasattr(result, "content"):
            # MCP SDK result object
            texts = [c.text for c in result.content if hasattr(c, "text")]
            return "\n".join(texts)
        elif isinstance(result, str):
            # Direct string result from custom client
            return result
        else:
            return json.dumps(result) if result else "No response"
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ── LLM backends ──────────────────────────────────────────────────────────────


def run_with_openai_compat(
    prompt: str,
    base_url: str,
    model: str,
    mcp_client,
    max_rounds: int = 6,
    context_length: int = 4096,
) -> dict:
    """
    Runs one test prompt against an OpenAI-compatible endpoint (LM Studio).
    Handles multi-turn tool call loops.
    Returns {response_text, tool_calls, raw_messages, elapsed_s}.
    """
    from openai import OpenAI
    import gc

    client = OpenAI(base_url=base_url, api_key="lm-studio")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    tool_calls_log = []
    t0 = time.time()

    for round_num in range(max_rounds):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=MCP_TOOLS,
            tool_choice="auto",
            temperature=0.1,
            max_tokens=context_length // 2,  # Limit response tokens
        )
        msg = response.choices[0].message

        # No tool calls → final answer
        if not msg.tool_calls:
            gc.collect()  # Clean up after each round
            return {
                "response_text": msg.content or "",
                "tool_calls": tool_calls_log,
                "elapsed_s": round(time.time() - t0, 2),
            }

        # Append assistant message with tool_calls
        messages.append(msg)

        # Dispatch each tool call
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            result_str = dispatch_tool(tc.function.name, args, mcp_client)
            tool_calls_log.append(
                {
                    "tool": tc.function.name,
                    "arguments": args,
                    "result_preview": result_str[:300],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                }
            )

    # Hit max rounds without a final text response
    gc.collect()  # Clean up at end
    return {
        "response_text": "[max tool rounds reached]",
        "tool_calls": tool_calls_log,
        "elapsed_s": round(time.time() - t0, 2),
    }


def run_with_anthropic(
    prompt: str, model: str, mcp_client, max_rounds: int = 6
) -> dict:
    """
    Runs one test prompt against the Anthropic API (Claude).
    Handles multi-turn tool use loops.
    """
    import anthropic

    # Convert OpenAI-style tool defs to Anthropic format
    def _to_anthropic_tool(t: dict) -> dict:
        fn = t["function"]
        return {
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        }

    tools = [_to_anthropic_tool(t) for t in MCP_TOOLS]
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": prompt}]
    tool_calls_log = []
    t0 = time.time()

    for _ in range(max_rounds):
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        # Collect text and tool_use blocks
        text_parts = []
        tool_uses = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(block)

        # Handle Qwen3 thinking mode - reasoning_content becomes text when content is empty
        if not text_parts and hasattr(response, "reasoning_content"):
            text_parts.append(response.reasoning_content)

        if response.stop_reason == "end_turn" or not tool_uses:
            return {
                "response_text": "\n".join(text_parts),
                "tool_calls": tool_calls_log,
                "elapsed_s": round(time.time() - t0, 2),
            }

        # Append assistant turn - handle Qwen3 thinking mode (outputs to reasoning_content)
        assistant_content = response.content or getattr(response, "reasoning_content", "")
        messages.append({"role": "assistant", "content": assistant_content})

        # Dispatch tool calls and build tool_result turn
        tool_results = []
        for tu in tool_uses:
            result_str = dispatch_tool(tu.name, tu.input, mcp_client)
            tool_calls_log.append(
                {
                    "tool": tu.name,
                    "arguments": tu.input,
                    "result_preview": result_str[:300],
                }
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": result_str,
                }
            )

        messages.append({"role": "user", "content": tool_results})

    return {
        "response_text": "[max tool rounds reached]",
        "tool_calls": tool_calls_log,
        "elapsed_s": round(time.time() - t0, 2),
    }


# ── result evaluation ──────────────────────────────────────────────────────────


def evaluate_result(test: dict, result: dict) -> str:
    """
    Simple heuristic pass/fail:
    - FAIL if no tool was called (LLM ignored the tools)
    - FAIL if response is empty or indicates an error
    - WARN if tool was called but response is very short (<30 chars)
    - PASS otherwise
    Human review is still needed for semantic correctness.
    """
    if not result["tool_calls"]:
        return "FAIL — no tool called"
    if not result["response_text"].strip():
        return "FAIL — empty response"
    if len(result["response_text"].strip()) < 30:
        return "WARN — very short response"
    if "error" in result["response_text"].lower()[:80]:
        # Error responses from the server are valid for negative tests (A05, A06, B03, B04, D07)
        negative_tests = {"A05", "A06", "B03", "B04", "D07"}
        if test["id"] in negative_tests:
            return "PASS — expected error returned"
        return "WARN — possible error (check manually)"
    return "PASS"


# ── report writer ──────────────────────────────────────────────────────────────


def write_report(results: list[dict], output_path: Path, backend: str, model: str):
    """Writes a JSON report and a human-readable markdown summary."""
    json_path = output_path.with_suffix(".json")
    md_path = output_path.with_suffix(".md")

    # JSON
    json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))

    # Markdown summary
    passed = sum(1 for r in results if r["verdict"].startswith("PASS"))
    warned = sum(1 for r in results if r["verdict"].startswith("WARN"))
    failed = sum(1 for r in results if r["verdict"].startswith("FAIL"))
    total = len(results)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# Biomethane MCP Test Report",
        f"",
        f"**Run at:** {now}  ",
        f"**Backend:** {backend} / `{model}`  ",
        f"**Results:** {passed}/{total} PASS — {warned} WARN — {failed} FAIL",
        f"",
        f"| ID | Section | Verdict | Tools Called | Time (s) |",
        f"|---|---|---|---|---|",
    ]
    for r in results:
        tools = ", ".join(tc["tool"] for tc in r["tool_calls"]) or "—"
        lines.append(
            f"| {r['id']} | {r['section']} | {r['verdict']} | {tools} | {r['elapsed_s']} |"
        )

    lines += ["", "---", "", "## Detailed Results", ""]
    for r in results:
        lines += [
            f"### {r['id']} — {r['section']}",
            f"**Prompt:** {r['prompt']}  ",
            f"**Expected:** {r['expect']}  ",
            f"**Verdict:** {r['verdict']}  ",
            f"**Elapsed:** {r['elapsed_s']}s  ",
            "",
        ]
        if r["tool_calls"]:
            lines.append("**Tool calls:**")
            for tc in r["tool_calls"]:
                lines.append(f"- `{tc['tool']}` → {tc['result_preview'][:120]}...")
            lines.append("")
        lines += [
            "**LLM response:**",
            f"> {r['response_text'][:600]}",
            "",
        ]

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


# ── MCP client setup ───────────────────────────────────────────────────────────


def build_mcp_client(server_script: str, use_http: bool = False, http_port: int = 3000):
    """
    Launches the MCP server as a subprocess and communicates via JSON-RPC.
    Falls back to dry-run mode if unavailable.

    Args:
        server_script: Path to the MCP server script
        use_http: Use HTTP transport instead of stdio
        http_port: Port for HTTP transport
    """
    import subprocess
    import json
    import threading
    import queue

    venv_python = VENV_PYTHON
    server_script = server_script or MCP_SERVER_SCRIPT

    # Build command with optional HTTP arguments
    cmd = [venv_python, server_script]
    if use_http:
        cmd.extend(["--http", "--port", str(http_port)])
        print(f"  MCP: HTTP transport on port {http_port}")
    else:
        print(f"  MCP: stdio transport")

    try:
        # Start the MCP server process
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        # Wait briefly for server to start
        import time

        time.sleep(2)

        # Check if process is still running
        if proc.poll() is not None:
            stderr = proc.stderr.read() if proc.stderr else ""
            raise Exception(f"MCP server exited: {stderr}")

        # Message queue for responses
        response_queue = queue.Queue()
        request_id = [0]
        lock = threading.Lock()

        def send_request(method, params=None):
            """Send JSON-RPC request and get response."""
            with lock:
                request_id[0] += 1
                req_id = request_id[0]

            request = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {},
            }

            try:
                proc.stdin.write(json.dumps(request) + "\n")
                proc.stdin.flush()

                # Read response
                line = proc.stdout.readline()
                if line:
                    return json.loads(line)
                return None
            except Exception as e:
                print(f"Request error: {e}")
                return None

        # Initialize the session
        init_result = send_request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test-runner", "version": "1.0"},
            },
        )

        if not init_result or "error" in init_result:
            raise Exception(f"Failed to initialize MCP: {init_result}")

        # Test with tools/list
        tools_result = send_request("tools/list")

        class MCPClient:
            def __init__(self, proc, send_request):
                self._proc = proc
                self._send_request = send_request
                self._closed = False

            def call_tool(self, name, arguments):
                if self._closed:
                    return None
                try:
                    result = self._send_request(
                        "tools/call", {"name": name, "arguments": arguments}
                    )
                    if result and "result" in result:
                        content = result["result"].get("content", [])
                        if content:
                            return content[0].get("text", "")
                    return str(result) if result else "No response"
                except Exception as e:
                    self._closed = True
                    return f"Error: {str(e)}"

        return MCPClient(proc, send_request)

    except Exception as exc:
        print(f"[warn] Could not connect to MCP server: {exc}")
        print("[warn] Running in dry-run mode — tool results will be stubs.")
        return None


# ── main ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Biomethane MCP LLM Test Runner")
    parser.add_argument(
        "--backend",
        choices=["llama-cpp", "lmstudio", "anthropic"],
        default="llama-cpp",
        help="LLM backend to use",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8082/v1",
        help="LM Studio base URL (lmstudio backend only)",
    )
    parser.add_argument("--model", default="", help="Model name override")
    parser.add_argument("--test", default="", help="Run a single test by ID, e.g. A03")
    parser.add_argument(
        "--test-file",
        default="docs/biomethane_mcp_tests.md",
        help="Path to the test markdown file",
    )
    parser.add_argument(
        "--server-script",
        default="bio_methane_operations_mcp_server.v5.py",
        help="Path to the MCP server script",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Run MCP server with HTTP transport (requires uvicorn)",
    )
    parser.add_argument(
        "--mcp-port",
        type=int,
        default=3000,
        help="Port for MCP HTTP transport",
    )
    parser.add_argument(
        "--context-length",
        type=int,
        default=4096,
        help="Context length for LLM",
    )
    parser.add_argument(
        "--output",
        default="../reports/mcp_test_report",
        help="Output file base name (no extension)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip real MCP calls — use stub tool results",
    )
    args = parser.parse_args()

    # ── resolve model defaults ─────────────────────────────────────────────────
    if not args.model:
        if args.backend == "anthropic":
            args.model = "claude-sonnet-4-20250514"
        elif args.backend in ("llama-cpp", "lmstudio"):
            args.model = "Qwen3-4B-Thinking-2507-MLX-4bit-Q4_K_L"  # adjust to your llama.cpp loaded model

    # ── load tests ─────────────────────────────────────────────────────────────
    test_file = Path(args.test_file)
    if not test_file.exists():
        sys.exit(f"Test file not found: {test_file}")
    tests = parse_test_file(test_file)
    if args.test:
        tests = [t for t in tests if t["id"] == args.test.upper()]
        if not tests:
            sys.exit(f"Test ID '{args.test}' not found in {test_file}")

    print(f"\nBiomethane MCP Test Runner")
    print(f"  Backend  : {args.backend} / {args.model}")
    print(f"  Tests    : {len(tests)}")
    print(f"  Test file: {test_file}")
    print()

    # ── connect to MCP server ──────────────────────────────────────────────────
    mcp_client = (
        None
        if args.dry_run
        else build_mcp_client(
            args.server_script,
            use_http=args.http,
            http_port=args.mcp_port,
        )
    )

    # ── run tests ─────────────────────────────────────────────────────────────
    results = []
    for i, test in enumerate(tests, 1):
        label = f"[{i}/{len(tests)}] {test['id']} — {test['section']}"
        print(f"{label} ...", end=" ", flush=True)

        try:
            if args.backend == "anthropic":
                result = run_with_anthropic(test["prompt"], args.model, mcp_client)
            else:
                result = run_with_openai_compat(
                    test["prompt"],
                    args.base_url,
                    args.model,
                    mcp_client,
                    context_length=args.context_length,
                )
        except Exception as exc:
            result = {
                "response_text": f"RUNNER ERROR: {exc}",
                "tool_calls": [],
                "elapsed_s": 0,
            }

        verdict = evaluate_result(test, result)
        print(verdict)

        results.append(
            {
                "id": test["id"],
                "section": test["section"],
                "prompt": test["prompt"],
                "expect": test["expect"],
                "verdict": verdict,
                **result,
            }
        )

    # ── write report ───────────────────────────────────────────────────────────
    output_path = Path(args.output)
    json_path, md_path = write_report(results, output_path, args.backend, args.model)

    passed = sum(1 for r in results if r["verdict"].startswith("PASS"))
    warned = sum(1 for r in results if r["verdict"].startswith("WARN"))
    failed = sum(1 for r in results if r["verdict"].startswith("FAIL"))

    print(f"\n{'─' * 50}")
    print(f"  PASS {passed}  WARN {warned}  FAIL {failed}  /  {len(results)} tests")
    print(f"  JSON : {json_path}")
    print(f"  MD   : {md_path}")
    print()


if __name__ == "__main__":
    main()
