#!/usr/bin/env bash

# setup_rag.sh
# Sets up the Python virtual environment and installs dependencies
# for the Biomethane MCP Server RAG pipeline.

echo "=========================================="
echo "Setting up Biomethane RAG Pipeline"
echo "=========================================="

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    echo "[1/3] Activating virtual environment..."
    source .venv/bin/activate
else
    echo "⚠ Virtual environment not found. Run: python3 -m venv .venv"
    exit 1
fi

# Upgrade pip and install requirements
echo "[2/3] Installing dependencies..."
pip install --upgrade pip

# Install RAG dependencies
echo "[3/3] Installing RAG dependencies..."
pip install chromadb sentence-transformers langchain langchain-text-splitters python-dotenv tqdm beautifulsoup4

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "To ingest documentation, run:"
echo "  python RAG/scripts_for_rag/ingest_all_docs.py"
echo ""
echo "To query the RAG system, run:"
echo "  python RAG/scripts_for_rag/query_rag.py --interactive"