# AI Factory

AI Factory is a pipeline orchestration layer that connects existing AI tools to automate the software development lifecycle — from requirements analysis through to deployment.

**Philosophy**: Integrate, don't rebuild. Each phase of the SDLC already has mature tools. AI Factory provides the workflow engine, context bus, and human-in-the-loop checkpoints that tie them together.

## Pipeline

```
需求分析 → [Gate: PRD Approval] → 产品设计 → [Gate: Tech Spec Approval]
    → 开发 → 测试 → [Gate: Test Report] → 部署 → [Gate: Deploy Approval]
```

## Quickstart

```bash
# Install
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Run a pipeline
factory run --prompt "Build a TODO app with React frontend and Express backend"

# Approve a stage gate (in another terminal while pipeline is running)
factory approve <run-id> requirements

# Check status
factory status
factory list
```

## Project Structure

```
ai-factory/
  factory/              # Python orchestration layer
    pipeline/            # Pipeline engine (state machine, config, runner)
    bus/                 # Context bus (artifact store, schemas, manifest)
    gates/               # Human-in-the-loop approval gates
    agents/              # Agent config & prompt rendering
    tools/               # Claude Code subprocess wrapper
    cli.py               # CLI entry point
  configs/               # Pipeline & agent YAML configs
    pipelines/            # Pipeline stage definitions
    agents/               # Agent system prompts & behavior
  templates/prompts/     # Jinja2 prompt templates per stage
  schemas/               # JSON Schema for all artifact types
  tests/                 # Unit tests
  workspace/             # Runtime artifacts (gitignored)
```

## Architecture

Four subsystems:

- **Pipeline Engine** — Finite state machine that drives stage transitions, persisted in SQLite
- **Context Bus** — Versioned, directory-based artifact store. All stage outputs are structured JSON + human-readable Markdown
- **Agent System** — Each agent is a YAML config + Jinja2 prompt template. Claude Code CLI is the sole AI execution runtime
- **Human Gates** — Filesystem-based approval checkpoints. Pipeline pauses, human reviews, then continues

Everything is a file. Debug with `cat` and `git diff`. No black boxes.

## Requirements

- Python 3.12+
- Claude Code CLI (`claude`)
- Docker (for deployment stage)
