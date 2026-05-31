"""
env_config.py — Shared environment configuration loader for Bio-methane-mcp_server_demo project.

Usage in any script:
    from env_config import PROJECT_ROOT, MODEL_PATH, MCP_SERVER_URL, LLAMA_SERVER_URL
"""

import os
from pathlib import Path
from dotenv import load_dotenv

_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

PROJECT_ROOT = os.environ.get("PROJECT_ROOT", str(Path(__file__).parent.parent))

_model_path = os.environ.get(
    "MODEL_PATH",
    "~/.lmstudio/models/lmstudio-community/Qwen3-4B-GGUF/Qwen3-4B-Q4_K_M.gguf",
)
MODEL_PATH = os.path.expanduser(_model_path)

LLAMA_SERVER_URL = os.environ.get("LLAMA_SERVER_URL", "http://localhost:8082/v1")

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:3000/sse")


def _resolve_path(path: str, default: str) -> str:
    """Resolve a path that might be relative or use ~."""
    if path.startswith(".") or not path.startswith("/"):
        return os.path.expanduser(os.path.join(PROJECT_ROOT, path))
    return os.path.expanduser(path)


MCP_SERVER_SCRIPT = _resolve_path(
    os.environ.get("MCP_SERVER_SCRIPT", "src/bio_methane_operations_mcp_server_v5.py"),
    os.path.join(PROJECT_ROOT, "src", "bio_methane_operations_mcp_server_v5.py"),
)

VENV_PYTHON = _resolve_path(
    os.environ.get("VENV_PYTHON", ".venv/bin/python"),
    os.path.join(PROJECT_ROOT, ".venv", "bin", "python"),
)

CTX_SIZE = int(os.environ.get("CTX_SIZE", "4096"))

PORT = int(os.environ.get("PORT", "8082"))

LLAMA_SERVER = os.environ.get("LLAMA_SERVER", "/usr/local/bin/llama-server-mcp")

MODEL_NAME = os.environ.get("MODEL_NAME", "Qwen3-4B-Q4_K_M.gguf")
