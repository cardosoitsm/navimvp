# Navi — Autonomous Multi-Agent Pipeline

Runs a fully autonomous **PM → Dev → QA** pipeline using the Anthropic SDK.
One command, one requirement — agents chain automatically with no manual triggers.

## Architecture

```
run_pipeline("requirement")
       │
       ▼
  PMAgent.run()          → creates GitHub issue, returns handoff
       │
       ▼
  DevAgent.run()         → implements issue, opens PR, returns handoff
       │
       ▼
  QAAgent.run()          → reviews PR, runs tests, merges if approved
```

Each agent runs its own agentic loop internally (tool calls ↔ Anthropic API)
until it reaches `end_turn`. The orchestrator wires the handoffs between them.

## Setup

### 1. Install dependencies

```bash
cd navimvp
pip install -r agents/sdk/requirements.txt
```

### 2. Set environment variables

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GITHUB_TOKEN="ghp_..."                        # needs repo scope
export NAVI_PRODUCTION_URL="https://your-vm-url"     # optional, for post-deploy check
```

### 3. Run

```bash
# From the navimvp/ directory:
python -m agents.sdk.orchestrator "Add support for recurring monthly expenses"
```

Or from Python:

```python
from agents.sdk import run_pipeline

result = run_pipeline("Add support for recurring monthly expenses")
print(result)
```

## What each agent does

| Agent | Input | Output |
|---|---|---|
| **PMAgent** | Plain-language requirement | GitHub issue + handoff dict |
| **DevAgent** | PM handoff (issue number) | Feature branch + PR + handoff dict |
| **QAAgent** | Dev handoff (PR number) | Review + merge decision + result dict |

## Files

```
agents/sdk/
  __init__.py         — package exports
  base_agent.py       — shared Anthropic agentic loop
  tools.py            — tool implementations + Anthropic schemas
  pm_agent.py         — PM Agent (creates issues)
  dev_agent.py        — Dev Agent (implements + opens PR)
  qa_agent.py         — QA Agent (reviews + merges)
  orchestrator.py     — chains the three agents autonomously
  requirements.txt    — anthropic, requests
  README.md           — this file
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Anthropic API key |
| `GITHUB_TOKEN` | ✅ | GitHub PAT with `repo` scope |
| `NAVI_PRODUCTION_URL` | Optional | Production base URL for post-deploy health check |

## Notes

- The pipeline uses `claude-opus-4-6` for all agents (best reasoning for autonomous work).
- Each agent has a safety cap on tool-call iterations (`MAX_ITERATIONS`).
- All GitHub operations target `cardosoitsm/navimvp`.
- LGPD and security gates are enforced at the QA stage — no merge without passing them.
