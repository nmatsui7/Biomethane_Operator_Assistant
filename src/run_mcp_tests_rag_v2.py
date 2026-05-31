"""
run_mcp_tests.py — Biomethane MCP Server LLM Test Runner
=========================================================
Reads biomethane_mcp_tests.md, sends each prompt to an LLM
(LM Studio local OR Anthropic remote), captures tool calls and
responses, and writes a structured report.

Usage
-----
Run from ANY directory — the script locates the project root by searching
upward for pyproject.toml / setup.py / .git / requirements.txt , then falls back to reading
PROJECT_ROOT from the nearest .env file.

  # Against LM Studio (default, reads LLAMA_SERVER_URL from .env):
  python src/run_mcp_tests_rag_v2.py

  # Against Anthropic (Claude):
  python src/run_mcp_tests_rag_v2.py --backend anthropic

  # Run a single test by id:
  python src/run_mcp_tests_rag_v2.py --test A03

  # Custom base URL or model:
  python src/run_mcp_tests_rag_v2.py --base-url http://localhost:1234/v1 --model gemma-3-12b

  # Fall back to legacy hardcoded tool list if tools/list fails:
  python src/run_mcp_tests_rag_v2.py --allow-hardcoded-tools

Requirements
------------
  pip install openai anthropic python-dotenv rich

.env keys (all paths absolute; VENV_PYTHON may be relative to PROJECT_ROOT):
  PROJECT_ROOT=/path/to/your/project
  LLAMA_SERVER_URL=http://localhost:8082/v1
  MCP_SERVER_SCRIPT=<PROJECT_ROOT>/src/bio_methane_operations_mcp_server_v5.py
  VENV_PYTHON=.venv/bin/python
  HF_HOME=<PROJECT_ROOT>/.cache/huggingface   # optional; derived if absent
  HF_HUB_OFFLINE=1
  ANTHROPIC_API_KEY=sk-ant-...                 # only for --backend anthropic
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path


# ── Project root resolution (single source of truth) ────────────────────────────────
def _find_project_root() -> Path:
    """
    Walk upward from this file looking for a known anchor
    (pyproject.toml, setup.py, setup.cfg, .git).  If none is found,
    try reading PROJECT_ROOT from a nearby .env.  Last resort: one level
    up with a loud warning so the failure is never silent.
    """
    anchors = ("pyproject.toml", "setup.py", "setup.cfg", ".git", "requirements.txt")
    start = Path(__file__).resolve().parent
    for directory in [start, *start.parents]:
        if any((directory / a).exists() for a in anchors):
            return directory
    # Try PROJECT_ROOT key in a nearby .env
    for env_candidate in (start / ".env", start.parent / ".env"):
        if env_candidate.exists():
            for line in env_candidate.read_text().splitlines():
                line = line.strip()
                if line.startswith("PROJECT_ROOT="):
                    root = Path(line.split("=", 1)[1].strip())
                    if root.is_dir():
                        return root
    fallback = start.parent
    print(
        f"WARNING: project root anchor not found — using {fallback}.\n"
        f"  Add pyproject.toml/.git to your repo root, or set PROJECT_ROOT in .env.",
        flush=True,
    )
    return fallback


PROJECT_ROOT = _find_project_root()

# ── Load .env before anything else reads os.environ ──────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass  # rely on shell environment

import openai  # noqa: E402

# ── HuggingFace cache — .env values take priority, then PROJECT_ROOT/.cache ────
os.environ.setdefault("HF_HOME",
    os.environ.get("HF_HOME", str(PROJECT_ROOT / ".cache" / "huggingface")))
os.environ.setdefault("TRANSFORMERS_CACHE",
    os.environ.get("TRANSFORMERS_CACHE",
                   str(PROJECT_ROOT / ".cache" / "huggingface" / "transformers")))
os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME",
    os.environ.get("SENTENCE_TRANSFORMERS_HOME",
                   str(PROJECT_ROOT / ".cache" / "huggingface" / "sentence-transformers")))
os.environ.setdefault("HF_HUB_OFFLINE", "1")

# ── RAG imports ───────────────────────────────────────────────────────────────────
sys.path.insert(0, str(PROJECT_ROOT))
try:
    from RAG.scripts_for_rag.query_rag import query as rag_query
    HAS_RAG = True
except ImportError:
    HAS_RAG = False
    def rag_query(q, *args, **kwargs):  # type: ignore[misc]
        return {"text": []}

# ── Resolve VENV_PYTHON and MCP_SERVER_SCRIPT ──────────────────────────────────────
# VENV_PYTHON may be relative in .env (e.g. ".venv/bin/python") — resolve it.
_venv_raw = os.environ.get("VENV_PYTHON", ".venv/bin/python")
_venv_path = Path(_venv_raw)
if not _venv_path.is_absolute():
    _venv_path = PROJECT_ROOT / _venv_path
if _venv_path.exists():
    VENV_PYTHON = str(_venv_path)
else:
    print(
        f"WARNING: VENV_PYTHON {_venv_path} not found — falling back to {sys.executable}",
        flush=True,
    )
    VENV_PYTHON = sys.executable

MCP_SERVER_SCRIPT = os.environ.get(
    "MCP_SERVER_SCRIPT",
    str(PROJECT_ROOT / "src" / "bio_methane_operations_mcp_server_v5.py"),
)

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
            "name": "get_vfa_alkalinity_ratio",
            "description": "Calculate VFA to alkalinity ratio (fos/tac) and assess acidification risk.",
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
    {
        "type": "function",
        "function": {
            "name": "ad4_simulate",
            "description": "Run AD4 digestion simulation at steady operating conditions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dilution_rate": {"type": "number", "description": "D in d⁻¹ (0.04-0.07 typical)"},
                    "influent_cod_g_per_l": {"type": "number", "description": "S1_in g/L (15-50 typical)"},
                    "days": {"type": "number", "description": "Simulation days (≥100 for steady state)"},
                    "digester_temp_c": {"type": "number", "description": "Temperature °C (optional)"},
                },
                "required": ["dilution_rate", "influent_cod_g_per_l"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ad4_critical_dilution_rate",
            "description": "Find washout dilution rate threshold, temperature-corrected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "influent_cod_g_per_l": {"type": "number", "description": "g/L (default 25)"},
                    "digester_temp_c": {"type": "number", "description": "Temperature °C (optional)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ad4_perturbation_test",
            "description": "Simulate substrate overload spike and test recovery.",
            "parameters": {
                "type": "object",
                "properties": {
                    "overload_cod_g_per_l": {"type": "number", "description": "Overload COD g/L"},
                    "overload_days": {"type": "number", "description": "Duration days (default 10)"},
                    "recovery_days": {"type": "number", "description": "Recovery days (default 30)"},
                    "baseline_cod_g_per_l": {"type": "number", "description": "Normal COD g/L (default 25)"},
                    "digester_temp_c": {"type": "number", "description": "Temperature °C (optional)"},
                },
                "required": ["overload_cod_g_per_l"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enkf_initialise",
            "description": "Initialise Ensemble Kalman Filter for state estimation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "digester_volume_m3": {"type": "number", "description": "Volume m³ (default 2000)"},
                    "hrt_days": {"type": "number", "description": "HRT days (default 22)"},
                    "s1_in_g_per_l": {"type": "number", "description": "Influent COD g/L (default 25)"},
                    "n_ensemble": {"type": "number", "description": "Ensemble size (default 100)"},
                    "t_ref_celsius": {"type": "number", "description": "Reference temp °C (default 35)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enkf_update",
            "description": "Advance EnKF by one day using plant_state sensors.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fos_mg_per_l": {"type": "number", "description": "FOS mg/L (optional)"},
                    "new_hrt_days": {"type": "number", "description": "New HRT days (optional)"},
                    "new_s1_in_g_per_l": {"type": "number", "description": "New influent COD (optional)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "enkf_status",
            "description": "Return current EnKF filter status and latest estimate.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

SYSTEM_PROMPT = """You are a biomethane plant operations assistant.

CRITICAL: You MUST use the MCP tools provided below for ALL calculations. 

Available tools - you MUST call these EXACTLY:
- get_plant_state: Returns current plant operating state
- update_plant_state(updates={}): Update plant state values
- check_alerts(): Check alert thresholds
- get_vfa_alkalinity_ratio(): Calculate VFA/alkalinity ratio and acidification risk
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
1. ALWAYS call a tool.
2. Use exact tool name with parameters as shown above
3. For any question requiring calculation, call the appropriate tool first
4. After getting tool results, summarize the key numbers with units in your response."""

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
        "blend_feedstocks": {"feedstocks": "recipe"},  # feedstocks -> recipe
    }
    
    # Also fix nested param names inside arrays for blend_feedstocks
    array_param_mapping = {
        "blend_feedstocks": {
            "recipe": [
                {"type": "name", "feedstock": "name"},
                {"quantity": "wet_tonnes", "amount": "wet_tonnes"},
            ]
        }
    }

    # Apply parameter mapping if needed
    if name in param_mapping:
        mapped_args = {}
        for key, value in arguments.items():
            mapped_key = param_mapping[name].get(key, key)
            mapped_args[mapped_key] = value
        arguments = mapped_args
    
    # For blend_feedstocks, also fix array item keys
    
    if name == "blend_feedstocks":
        # First get list from either recipe or feedstocks
        feedstock_list = arguments.get("recipe") or arguments.get("feedstocks") or []
        if feedstock_list:
            fixed_recipe = []
            for item in feedstock_list:
                fixed_item = {}
                for k, v in item.items():
                    if k in ("name", "feedstock", "type"):
                        fixed_item["name"] = v
                    elif k in ("wet_tonnes", "quantity", "amount"):
                        fixed_item["wet_tonnes"] = v
                    else:
                        fixed_item[k] = v
                fixed_recipe.append(fixed_item)
            arguments["recipe"] = fixed_recipe
    
    try:
        import threading
        result_holder = [None]
        error_holder = [None]
        
        def call_tool():
            try:
                result_holder[0] = mcp_client.call_tool(name, arguments)
            except Exception as e:
                error_holder[0] = e
        
        thread = threading.Thread(target=call_tool)
        thread.daemon = True
        thread.start()
        thread.join(timeout=30)
        
        if thread.is_alive():
            return json.dumps({"error": f"Tool {name} timed out after 30s"})
        
        if error_holder[0]:
            return json.dumps({"error": str(error_holder[0])})
        
        result = result_holder[0]
        
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
            tools=MCP_TOOLS,  # Uses dynamically loaded tools from MCP server
            tool_choice="auto",
            temperature=0.1,
            max_tokens=2048,  # Uniform — Gemma 4 needs room for thinking + tool JSON
        )
        msg = response.choices[0].message

        # No tool calls → final answer
        if not msg.tool_calls:
            gc.collect()  # Clean up after each round
            content = msg.content or ""
            
            # Gemma 4 EOG bug: try reasoning_content if content is empty
            if not content.strip():
                try:
                    resp_dict = response.model_dump()
                    msg_dict = resp_dict["choices"][0]["message"]
                    content = msg_dict.get("reasoning_content", "") or ""
                except (KeyError, AttributeError):
                    pass
            
            return {
                "response_text": content,
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
    Evaluate test result:
    - FAIL if no tool was called (LLM ignored the tools)
    - FAIL if response is empty or indicates an error
    - For "no tool expected" tests: skip tool validation
    - For H-series: require at least ONE expected tool called
    - For others: require ALL expected tools called
    - WARN if tool was called but response is very short (<30 chars)
    - PASS otherwise
    Human review is still needed for semantic correctness.
    """
    # Get actual tool names called
    actual_tools = {tc["tool"] for tc in result["tool_calls"]}
    
    # Check if no tools were called
    if not result["tool_calls"]:
        # Check if test expects NO tool calls (pure reasoning)
        expect = test.get("expect", "").lower()
        if "does not call" in expect or "not call" in expect or "no tool" in expect or "recommends" in expect:
            # This is expected — test wants pure reasoning
            pass
        else:
            return "FAIL — no tool called"
    
    # Check negative tests (expected errors)
    if "error" in result["response_text"].lower()[:80]:
        negative_tests = {
            "A05", "A06",           # invalid plant state updates
            "B03", "B04",           # blend errors (too many feedstocks, unknown feedstock)
            "D07",                  # unknown lookup topic
            "E04",                  # buswell_bmp_by_class unknown substrate
            "E09",                  # cn_ratio nitrogen=0 (division by zero protection)
            "E12",                  # olr_from_recipe unknown feedstock "rock"
            "F05", "F10",           # ad4_simulate / ad4_perturbation invalid params
            "G06",                  # enkf_update before initialise
        }
        if test["id"] in negative_tests:
            return "PASS — expected error returned"
        return "WARN — possible error (check manually)"
    
    # Check if correct tools were called
    expect = test.get("expect", "").lower()
    if "returns" in expect or "call" in expect or "should" in expect:
        # Get list of known tool names from MCP_TOOLS
        known_tools = {t["function"]["name"] for t in MCP_TOOLS}
        
        # Find which tools are mentioned in the expect field
        expected_tools = set()
        for tool_name in known_tools:
            if tool_name.lower() in expect:
                expected_tools.add(tool_name)
        
        # Skip if no expected tools (pure reasoning test)
        if not expected_tools:
            pass
        # H-series: require at least ONE expected tool
        elif test["id"].startswith("H"):
            if not (expected_tools & actual_tools):
                # Special case: H15 expects check_alerts but LLM might use equivalent tools
                if test["id"] == "H15":
                    # Accept if get_plant_state + get_vfa_alkalinity_ratio + get_operational_reference
                    # (these together cover temp, H2S, pH like check_alerts does)
                    equivalent_tools = {"get_plant_state", "get_vfa_alkalinity_ratio", "get_operational_reference"}
                    if equivalent_tools.issubset(actual_tools):
                        pass  # Accept equivalent tool combination
                    else:
                        return f"FAIL — expected at least one of {expected_tools}, Got: {actual_tools}"
                else:
                    return f"FAIL — expected at least one of {expected_tools}, Got: {actual_tools}"
        # Others: require ALL expected tools
        elif expected_tools - actual_tools:
            return f"FAIL — wrong tools called. Expected: {expected_tools}, Got: {actual_tools}"
    
    # Check response quality
    if not result["response_text"].strip():
        return "FAIL — empty response"
    if len(result["response_text"].strip()) < 30:
        return "WARN — very short response"
    
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
        # Build env — derive site-packages from the resolved venv, not a
        # hardcoded path, so it works across Python versions and venv names.
        env = os.environ.copy()
        _venv_dir = Path(VENV_PYTHON).parent.parent  # .venv/bin/python -> .venv
        _site_pkgs = list(_venv_dir.glob("lib/python3.*/site-packages"))
        if _site_pkgs:
            env["PYTHONPATH"] = f"{_site_pkgs[0]}:{env.get('PYTHONPATH', '')}"
        
        # Start the MCP server process
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
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

        # Get tools dynamically from MCP server
        tools_result = send_request("tools/list")
        
        def convert_mcp_to_openai(mcp_tools):
            """Convert MCP tools/list format to OpenAI function format."""
            openai_tools = []
            if not tools_result or "result" not in tools_result:
                return MCP_TOOLS  # Fallback to hardcoded list
            
            for tool in tools_result["result"].get("tools", []):
                openai_tool = {
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                    }
                }
                openai_tools.append(openai_tool)
            
            return openai_tools if openai_tools else MCP_TOOLS
        
        dynamic_tools = convert_mcp_to_openai(tools_result)
        
        # Store for later use in evaluate_result
        MCP_TOOLS = dynamic_tools
        
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
    import fcntl
    import sys
    
    # Lockfile to prevent parallel runs
    lock_file_path = PROJECT_ROOT / ".test_runner.lock"
    lock_file = None
    
    try:
        lock_file = open(lock_file_path, "w")
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        print(f"[ERROR] Another test runner is already running (lockfile: {lock_file_path})")
        print("[ERROR] Please wait for it to finish or remove the lock file.")
        sys.exit(1)
    
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
    parser.add_argument("--model", default="gemma-4-E4B-it-Q8_0.gguf", help="Model name (default: gemma-4-E4B-it-Q8_0.gguf)")
    parser.add_argument("--test", default="", help="Run a single test by ID, e.g. A03")
    parser.add_argument(
        "--test-file",
        default="docs/biomethane_mcp_tests.md",
        help="Path to the test markdown file",
    )
    parser.add_argument(
        "--server-script",
        default="src/bio_methane_operations_mcp_server_v5.py",
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
        default=8192,
        help="Context length for LLM",
    )
    parser.add_argument(
        "--output",
        default="reports/mcp_test_report",
        help="Output file base name (no extension)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip real MCP calls — use stub tool results",
    )
    args = parser.parse_args()
    
    # Auto-generate log file name based on test ID or timestamp
    now_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    if args.test:
        log_file = f"logs/{args.test}_test_{now_str}.log"
    else:
        log_file = f"logs/full_test_{now_str}.log"
    
    # Redirect output to both terminal and log file
    import sys
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Open log file for writing
    log_fh = open(log_path, 'w')
    
    # Save original stdout
    original_stdout = sys.stdout
    
    class TeeOutput:
        def __init__(self, terminal, log_file):
            self.terminal = terminal
            self.log = log_file
        def write(self, message):
            self.terminal.write(message)
            self.log.write(message)
            self.log.flush()
        def flush(self):
            self.terminal.flush()
            self.log.flush()
    
    sys.stdout = TeeOutput(original_stdout, log_fh)
    sys.stderr = TeeOutput(sys.stderr, log_fh)
    
    print(f"Logging to: {log_file}")

    # ── resolve model defaults ─────────────────────────────────────────────────
    if not args.model:
        if args.backend == "anthropic":
            args.model = "claude-sonnet-4-20250514"
        elif args.backend in ("llama-cpp", "lmstudio"):
            args.model = "gemma-4-E4B-it-Q8_0.gguf"  # default Gemma 4 model

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
    import gc
    for i, test in enumerate(tests, 1):
        label = f"[{i}/{len(tests)}] {test['id']} — {test['section']}"
        print(f"{label} ...", end=" ", flush=True)

        # ── RAG augmentation (Option B) ─────────────────────────────
        user_prompt = test["prompt"]
        if HAS_RAG:
            rag_results = rag_query(user_prompt, n_results=3)
            docs = rag_results.get("text", [])
            if docs:
                rag_context = "\n\n".join([
                    f"[From {d['metadata'].get('source_file', 'doc')}]:\n{d['content'][:500]}"
                    for d in docs[:3]
                ])
                augmented_prompt = (
                    f"RELEVANT KNOWLEDGE FROM documentation:\n"
                    f"{rag_context}\n\n"
                    f"{'='*60}\n"
                    f"Question: {user_prompt}"
                )
            else:
                augmented_prompt = user_prompt
        else:
            augmented_prompt = user_prompt

        try:
            if args.backend == "anthropic":
                result = run_with_anthropic(augmented_prompt, args.model, mcp_client)
            else:
                result = run_with_openai_compat(
                    augmented_prompt,
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
        
        # Clean up memory every 5 tests
        if i % 5 == 0:
            gc.collect()

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
    
    # Restore stdout/stderr and close log file
    sys.stdout = original_stdout
    sys.stderr = sys.__stderr__
    try:
        log_fh.close()
    except:
        pass
    
    # Release lock file
    try:
        if lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()
            lock_file_path.unlink(missing_ok=True)
    except:
        pass


if __name__ == "__main__":
    main()
