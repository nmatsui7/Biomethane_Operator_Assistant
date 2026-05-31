#!/bin/bash
#
# Run gemma-4-E4B with llama-server (latest build with MCP support)
# No LM Studio required - uses llama.cpp directly
#
# Usage:
#   ./run_gemma.sh
#
# Configuration: Use .env or env_config.py
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Load environment configuration
source "$PROJECT_ROOT/.env" 2>/dev/null || true

# Set defaults
MODEL_PATH="${MODEL_PATH:-$HOME/.lmstudio/models/lmstudio-community/gemma-4-E4B-it-GGUF/gemma-4-E4B-it-Q8_0.gguf}"
PORT="${PORT:-8082}"
CTX_SIZE="${CTX_SIZE:-8192}"
LLAMA_SERVER="${LLAMA_SERVER:-/usr/local/bin/llama-server-mcp}"

echo "Starting gemma-4-E4B-it with llama-server..."
echo "Model: $MODEL_PATH"
echo "URL: http://localhost:$PORT"
echo ""

# ============================================================
# Check and stop existing llama.cpp processes
# ============================================================

echo "Checking for existing llama.cpp processes..."

# 1. Find processes by name (llama-server, llama-server-mcp)
LLAMA_PIDS=$(pgrep -f "llama-server" 2>/dev/null || true)

if [ -n "$LLAMA_PIDS" ]; then
    echo "Found llama-server processes: $LLAMA_PIDS"
    kill $LLAMA_PIDS 2>/dev/null
    sleep 1
    
    # Force kill if still running
    REMAINING=$(pgrep -f "llama-server" 2>/dev/null || true)
    if [ -n "$REMAINING" ]; then
        echo "Force-killing remaining processes..."
        kill -9 $REMAINING 2>/dev/null
    fi
    echo "Stopped existing llama-server processes."
else
    echo "No llama-server processes found."
fi

# 2. Find processes using our port (in case another server type is using it)
PORT_PIDS=$(lsof -ti :$PORT 2>/dev/null || true)
if [ -n "$PORT_PIDS" ]; then
    echo "Found processes using port $PORT: $PORT_PIDS"
    kill $PORT_PIDS 2>/dev/null
    sleep 1
    
    REMAINING_PORT=$(lsof -ti :$PORT 2>/dev/null || true)
    if [ -n "$REMAINING_PORT" ]; then
        echo "Force-killing port processes..."
        kill -9 $REMAINING_PORT 2>/dev/null
    fi
fi

# 3. Final check - ensure port is free
sleep 0.5
if lsof -ti :$PORT >/dev/null 2>&1; then
    echo "WARNING: Port $PORT still in use after cleanup!"
else
    echo "Port $PORT is free."
fi

echo ""
# ============================================================

LOG_FILE="$PROJECT_ROOT/logs/gemma4-2026-04-29.log"
mkdir -p "$(dirname "$LOG_FILE")"
echo "Logging to: $LOG_FILE"
echo ""

echo "Starting llama-server on port $PORT..."
echo "(Press Ctrl+C to stop)"
echo ""

$LLAMA_SERVER \
    --model "$MODEL_PATH" \
    --port $PORT \
    --host 127.0.0.1 \
    -c $CTX_SIZE \
    --threads 6 \
    -ngl 0 \
    --chat-template-file "$SCRIPT_DIR/gemma4_official.jinja" \
    --webui \
    --webui-mcp-proxy >> "$LOG_FILE" 2>&1
