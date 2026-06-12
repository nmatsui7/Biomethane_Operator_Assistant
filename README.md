# Biomethane Operator Assistant

MCP (Model Context Protocol) server and CLI for anaerobic digestion plant operations. The project addresses the practical issue that sensors are expensive, so many plants operate with only a limited set of measurements. It combines a physics-based AD4 simulator, Ensemble Kalman Filter state estimation, SCADA ingestion pipeline, and an LLM-powered chat interface to create a modest digital-twin-style assistant for plant monitoring and optimization.

## Why This Project Matters

Biomethane and anaerobic digestion facilities rely on continuous monitoring of process variables, feedstock inputs, gas production, and operational alerts. In practice, however, installing and maintaining sensors for every useful variable can be costly. As a result, operators often need to make decisions with incomplete measurements.

This project explores a practical approach to that limited-sensor problem. It uses process simulation and state estimation to infer unmeasured or difficult-to-measure conditions, then makes those insights accessible through a natural-language chatbot. The goal is not to build a full industrial digital twin, but to provide a modest, operator-facing version that helps plant staff monitor performance, investigate anomalies, compare operating options, and optimize plant behavior using familiar language.

## Features

- **MCP Server** — 25+ tools accessible via stdio or HTTP (`src/bio_methane_operations_mcp_server_v5.py`)
  - Live plant state monitoring with CUSUM anomaly detection
  - Rule-based threshold alerts (lab-scale and industrial profiles)
  - Feedstock blending with BMP, C/N, and OLR calculations
  - KPI rollups (daily/weekly/monthly production summaries)
- **AD4 Simulator** — 4-state AM2 anaerobic digestion ODE model (Bernard 2001, Benyahia 2012)
- **EnKF State Estimator** — Ensemble Kalman filter for estimating hidden or sparsely measured process states such as S2 and X2
- **CLI** (`bio_cli.py`) — All core operations as standalone commands
- **Chat UI** (`biomethane_chat.py`) — Gradio interface with intent routing for natural-language operator interaction
- **SCADA Ingestion** — Auto-detect vendor, fuzzy column mapping, CUSUM filtering, and plant-specific data-format adaptation
- **RAG Pipeline** — Retrieval-augmented generation over process documentation

## Example Use Cases

- Check current plant state and KPI summary
- Monitor plant behavior when only a limited number of sensors are available
- Detect unusual process behavior using CUSUM anomaly detection
- Estimate hidden process states using Ensemble Kalman filtering
- Ask operational questions through a RAG-enabled chat interface
- Compare feedstock blending options using BMP, C/N, and OLR calculations
- Use natural language to explore operating conditions, investigate alerts, and support optimization decisions
- Adapt ingestion and column mappings to match local plant data exports with a coding agent


## Adapting to Your Plant Data

Plant data formats vary widely across facilities, SCADA vendors, historians, spreadsheets, and lab reporting workflows. This repository includes ingestion and fuzzy column-mapping utilities, but users should expect to adapt the code to their own plant data format before using it in a real setting.

A coding agent such as Antigravity, Codex, Claude Code, or OpenCode can be used to adjust the ingestion pipeline, column mappings, units, timestamp handling, and plant-specific assumptions. Typical changes may include mapping local sensor names to the expected variables, converting units, handling missing values, and aligning historical data exports with the simulator and state estimator.

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

## Screenshot

![Chat Interface](screenshots/chatscreen.jpg)

## License

MIT
