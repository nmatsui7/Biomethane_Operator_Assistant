# Setup Guide

## Prerequisites

- Python 3.10+
- [llama.cpp](https://github.com/ggerganov/llama.cpp) with MCP support (`llama-server-mcp`)
- A Gemma 4 GGUF model (or any OpenAI-compatible LLM backend)

## Quick Start

### 1. Clone and install

```bash
git clone <repo-url>
cd Biomethane_Operator_Assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install mcp  # MCP SDK
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your paths (MODEL_PATH, LLAMA_SERVER, etc.)
```

### 3. Download the Gemma 4 chat template

The `run_gemma.sh` script requires `gemma4_official.jinja` for the llama-server chat template:

```bash
# Download from the llama.cpp repository:
curl -o scripts/gemma4_official.jinja \
  https://raw.githubusercontent.com/ggerganov/llama.cpp/master/gguf-py/gguf/chat_templates/gemma4_official.jinja
```

### 4. Download a Gemma 4 GGUF model

```bash
# Example using Hugging Face:
huggingface-cli download lmstudio-community/gemma-4-E4B-it-GGUF \
  gemma-4-E4B-it-Q8_0.gguf --local-dir models/gemma-4
```

### 5. Run the LLM server

```bash
./scripts/run_gemma.sh
```

### 6. Run the MCP server

```bash
# stdio mode (default):
python src/bio_methane_operations_mcp_server_v5.py

# HTTP mode:
python src/bio_methane_operations_mcp_server_v5.py --http
```

### 7. Start the chat UI

```bash
python src/biomethane_chat.py
```

## CLI Usage

```bash
# Check plant state
python src/bio_cli.py get-state

# Check alerts
python src/bio_cli.py check-alerts

# Blend feedstocks
python src/bio_cli.py blend "Cattle slurry:30" "Maize silage:15"

# Run AD4 simulation
python src/bio_cli.py simulate run --D 0.05 --S1 25

# Find critical dilution rate
python src/bio_cli.py simulate critical-d --S1 25

# Calibration commands
python src/bio_cli.py calibrate buswell --c 6 --h 12 --o 6
python src/bio_cli.py calibrate cn --carbon 44.0 --nitrogen 1.8
```

## Sample Data

Some tools (`replay_dataset.py`, `scada_mapper.py`, `seed_plant_state.py`) expect CSV
datasets in `sample_data/`. These are public datasets that must be downloaded separately.

### ri_flex.csv — Lab-scale R1-FLEX digestion dataset

Used for replay, seeding, and calibration. From a public anaerobic digestion research
dataset (Mendeley Data or similar). Place it at `sample_data/ri_flex.csv`.

Expected columns: a date column, `DigesterTemp`, `pH`, `Daily Biogas (mL)`, `CH4`,
`VFA (mg/L)`, `TAN (mg N/L)`, and optionally a `Comment` column.

### Indian Biogas Production Dataset

Industrial-scale biogas plant data from India. Available on Kaggle / UCI:

```bash
# Option A — Manual download from Kaggle
# 1. Visit https://www.kaggle.com/datasets/ucimachinelearning/indian-biogas-production-dataset
# 2. Download and unzip to:
mkdir -p sample_data/Indian-Biogas-Production-Dataset
# 3. Place biogas_dataset.csv in that directory

# Option B — via kagglehub (Python SDK)
pip install kagglehub
python -c "
import kagglehub
path = kagglehub.dataset_download('ucimachinelearning/indian-biogas-production-dataset')
import shutil, os
shutil.copytree(path, 'sample_data/Indian-Biogas-Production-Dataset', dirs_exist_ok=True)
"
```

Expected columns: `Year`, `Month`, `Day`, `biogas_production`, `Digester Temp (°C)`,
`pH`, `CH4(%)`, `VFA`, `TAN`, etc.

## Lab Scale Mode

For lab-scale digesters (~1 m³, 20-30°C):

```bash
python src/bio_methane_operations_mcp_server_v5.py --lab-scale
python src/bio_cli.py get-state --lab-scale
```

## Site Configuration

Copy and edit `site_config.json` to match your digester geometry, CUSUM
parameters, and alert thresholds.

## RAG (Retrieval-Augmented Generation)

See `RAG/scripts_for_rag/setup_rag.sh` for RAG pipeline setup.

## File Layout

```
├── data/                    # SQLite databases (created at runtime)
├── scripts/
│   └── run_gemma.sh         # LLM server launcher
├── src/                     # Python source
│   ├── bio_cli.py           # CLI tool
│   ├── bio_methane_operations_mcp_server_v5.py  # MCP server
│   ├── biomethane_chat.py   # Gradio chat UI
│   ├── ad4_simulator.py     # AD4 digestion simulator
│   ├── ad4_enkf.py          # Ensemble Kalman filter
│   ├── StateBuffer.py       # CUSUM-filtered SQLite buffer
│   ├── scada_mapper.py      # SCADA column mapping
│   └── ...
├── RAG/                     # RAG pipeline (docs + scripts)
│   ├── docs/                # Reference documents
│   └── scripts_for_rag/     # Ingest, query, rerank scripts
├── .env.example             # Environment template
├── site_config.json         # Site configuration
├── pyproject.toml
└── requirements.txt
```
