# Biomethane Operator Assistant

MCP (Model Context Protocol) server and CLI for anaerobic digestion plant operations, featuring a physics-based AD4 simulator, Ensemble Kalman Filter state estimation, SCADA ingestion pipeline, and an LLM-powered chat interface.

## Features

- **MCP Server** — 25+ tools accessible via stdio or HTTP (`src/bio_methane_operations_mcp_server_v5.py`)
  - Live plant state monitoring with CUSUM anomaly detection
  - Rule-based threshold alerts (lab-scale and industrial profiles)
  - Feedstock blending with BMP, C/N, and OLR calculations
  - KPI rollups (daily/weekly/monthly production summaries)
- **AD4 Simulator** — 4-state AM2 anaerobic digestion ODE model (Bernard 2001, Benyahia 2012)
- **EnKF State Estimator** — Ensemble Kalman filter for hidden state (S2, X2) estimation
- **CLI** (`bio_cli.py`) — All core operations as standalone commands
- **Chat UI** (`biomethane_chat.py`) — Gradio interface with intent routing
- **SCADA Ingestion** — Auto-detect vendor, fuzzy column mapping, CUSUM filtering
- **RAG Pipeline** — Retrieval-augmented generation over process documentation

## Quick Start

```bash
pip install -r requirements.txt
python src/bio_methane_operations_mcp_server_v5.py
```

See [SETUP_GUIDE.md](SETUP_GUIDE.md) for full installation and configuration.

## Requirements

- Python 3.10+
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (`mcp>=1.0.0`)
- llama.cpp with MCP support (for local LLM inference)
- Gemma 4 or any OpenAI-compatible model

## License

MIT
