# RAG, Chatbot & Judge — Quick Starter Guide

## 1. RAG Setup

### 1.1 Install dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install chromadb sentence-transformers langchain langchain-text-splitters python-dotenv
```

### 1.2 Ensure source docs are in place
```bash
ls RAG/docs/
# Should see: doc1_ad4_model_card.md doc2_bernard2001_equations.md
#              doc3_ad4_api_reference.md doc4_failure_modes_lookup.md doc5_faq.md
```

All five must be present. If any are missing, re-generate or obtain the file from the upstream source.

### 1.3 Ingest documents into ChromaDB
```bash
python RAG/scripts_for_rag/ingest_all_docs.py
```

This creates `RAG/chroma_db/` with a `biomethane_knowledge` collection. The script:
- Splits each markdown file into semantic chunks (MarkdownHeader splitter)
- Detects FAQ format and preserves Q&A pairs as single chunks
- Deletes stale chunks when files are re-ingested (no orphaned vectors)
- Prints a per-file chunk count summary

### 1.4 Query the vector store
Interactive mode:
```bash
python RAG/scripts_for_rag/query_rag.py --interactive
```

Single query:
```bash
python RAG/scripts_for_rag/query_rag.py "What does the EnKF predict?" -n 3
```

Filter by source file:
```bash
python RAG/scripts_for_rag/query_rag.py "souring threshold" --filter doc4_failure_modes_lookup.md
```

### 1.5 Re-ingest after updating docs
Just re-run the ingest script — it deletes stale chunks and upserts new ones:
```bash
python RAG/scripts_for_rag/ingest_all_docs.py
```

---

## 2. Running the Chatbot

The chatbot (`biomethane_chat.py`) needs **three** processes running: the LLM server, the MCP server, and the Gradio UI itself. The chatbot auto-starts the MCP server as a subprocess, so you only need to start the LLM manually.

### 2.1 Download the Gemma 4 model

```bash
# One-time download from Hugging Face:
huggingface-cli download lmstudio-community/gemma-4-E4B-it-GGUF \
  gemma-4-E4B-it-Q8_0.gguf --local-dir models/gemma-4
```

Update `MODEL_PATH` in `.env` to point to the downloaded file:
```dotenv
MODEL_PATH=$PWD/models/gemma-4/gemma-4-E4B-it-Q8_0.gguf
```

If you prefer a smaller model, use the Q4_K_M variant instead:
```bash
huggingface-cli download lmstudio-community/gemma-4-E4B-it-GGUF \
  gemma-4-E4B-it-Q4_K_M.gguf --local-dir models/gemma-4
```

### 2.2 Start the LLM server (Gemma 4 via llama.cpp)

```bash
# Install llama-server-mcp (one-time)
brew install llama.cpp  # or build from source

# Download the chat template (one-time)
curl -o scripts/gemma4_official.jinja \
  https://raw.githubusercontent.com/ggerganov/llama.cpp/master/gguf-py/gguf/chat_templates/gemma4_official.jinja

# Edit .env with your paths
cp .env.example .env

# Start Gemma 4
./scripts/run_gemma.sh
```
This starts `llama-server-mcp` on `localhost:8082` with the model from `$MODEL_PATH` in your `.env`.

**Verify:**
```bash
curl http://localhost:8082/v1/models
```

### 2.2 Start the chat UI
```bash
source .venv/bin/activate
python src/biomethane_chat.py
```

This launches a Gradio UI at `http://localhost:7860`. The script:
1. Starts the MCP server (`bio_methane_operations_mcp_server_v5.py`) as a subprocess
2. Discovers all MCP tools
3. Opens a browser tab with the chat interface
4. Each response shows a tool trace panel

**Options:**
| Flag | Purpose |
|------|---------|
| `--port 7860` | Set Gradio port |
| `--share` | Create a public Gradio tunnel (ngrok) |
| `--no-mcp` | Launch UI only, skip MCP server (no tool calls) |
| `--kill-previous` | Kill previous instances on startup |

### 2.3 Manual MCP server (optional)
If you want the MCP server running independently:
```bash
source .venv/bin/activate
python src/bio_methane_operations_mcp_server_v5.py --http --port 3000
```
Then launch the chatbot with `--no-mcp` and configure `MCP_SERVER_URL=http://localhost:3000/mcp` in `.env`.

---

## 3. Running Judge Scripts

There are three "judge" scripts in this project, serving different purposes.

### 3.1 RAG Judge — `RAG/scripts_for_rag/judge_rag.py`
Evaluates RAG retrieval quality using Gemma 4 4B:
```bash
source .venv/bin/activate

# Ensure Gemma 4 is running (port 8080)
./scripts/run_gemma.sh

# Run the judge
python RAG/scripts_for_rag/judge_rag.py
```

What it does:
- Runs 15 test questions against the RAG ChromaDB
- For each question, retrieves the top 3 chunks
- Sends the question + retrieved context to Gemma 4 with a structured prompt
- Gemma scores relevance (0-10) and provides an explanation
- Saves results to `RAG/rag_evaluation_results.json`
- Prints a summary with average scores

**Requirements:**
- ChromaDB must have ingested docs (run `ingest_all_docs.py` first)
- Gemma 4 4B must be running on `http://localhost:8080` (judge uses port **8080**, not 8082)

### 3.2 MCP Test Runner — `src/run_mcp_tests.py`
End-to-end tests that call MCP tools via an LLM backend:
```bash
source .venv/bin/activate
python src/run_mcp_tests.py \
  --backend llama-cpp \
  --model "gemma-4-E4B-it-Q8_0.gguf" \
  --test A03 \
  --output reports/mcp_test_report
```

Key flags:
| Flag | Default | Purpose |
|------|---------|---------|
| `--backend` | `llama-cpp` | `llama-cpp`, `lmstudio`, or `anthropic` |
| `--model` | (auto-detect) | Model name |
| `--test` | (all) | Single test ID, e.g. `A03` |
| `--server-script` | `bio_methane_operations_mcp_server.v5.py` | MCP server path |
| `--http` | off | Use HTTP transport for MCP |
| `--output` | `../reports/mcp_test_report` | Report file prefix |
| `--dry-run` | off | Skip real MCP calls (stub mode) |

### 3.3 MCP Test Runner v2 (RAG-aware) — `src/run_mcp_tests_rag_v2.py`
Extended test runner that includes RAG-retrieval test cases:
```bash
source .venv/bin/activate
python src/run_mcp_tests_rag_v2.py \
  --backend llama-cpp \
  --model "gemma-4-E4B-it-Q8_0.gguf" \
  --test A03
```

Same flags as v1, with slightly different defaults:
| Flag | Default v2 |
|------|------------|
| `--model` | `gemma-4-E4B-it-Q8_0.gguf` |
| `--test-file` | `docs/biomethane_mcp_tests.md` |
| `--server-script` | `src/bio_methane_operations_mcp_server_v5.py` |
| `--context-length` | 8192 |

---

## 4. `site_config.json`

All system scripts read `site_config.json` (auto-discovered at `PROJECT_ROOT/site_config.json` or `PROJECT_ROOT/src/site_config.json`) for digester geometry, alert thresholds, CUSUM filter tuning, and the `lab_scale` toggle. See `docs/api/site_config.md` for the full schema reference.

---

## 5. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `chromadb` not found | `pip install chromadb sentence-transformers` |
| No documents in ChromaDB | Run `python RAG/scripts_for_rag/ingest_all_docs.py` |
| `biomethane_chat.py` port in use | `python src/biomethane_chat.py --kill-previous` or `--port 7861` |
| No model at MODEL_PATH | Download a Gemma 4 GGUF from Hugging Face and update `.env` |
