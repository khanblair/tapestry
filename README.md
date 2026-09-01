# Tapestry

A self-hosted, multi-agent workspace: named AI personas that a human can talk to one-on-one or in group
conversations, coordinating with each other by delegation, working on real code with real tool access,
across Discord, Telegram, and a web app.

Full design decisions and reasoning live in [`tapestry_scoped_spec.md`](tapestry_scoped_spec.md) and
[`project_structure.md`](project_structure.md). Every third-party piece this project builds on was verified
against real source before being adopted — see [`docs/vendor-research/`](docs/vendor-research/).

## Quick start

```bash
# 1. copy env templates and fill in real values
cp backend/.env.example backend/.env
cp web/.env.example web/.env.local

# 2. backend
cd backend
uv sync
uv run python -m tapestry.main

# 3. web (separate terminal)
cd web
pnpm install
pnpm dev
```

Or, once both `Dockerfile`s exist: `docker compose -f docker/docker-compose.yml up`.

## Prerequisites

- A Discord bot (Message Content Intent enabled) and/or a Telegram bot (`/setprivacy` disabled) — see
  `tapestry_scoped_spec.md` for what each surface needs.
- At least one model provider API key (Anthropic, DeepSeek, Gemini, or OpenRouter).
- A running [metamcp](https://github.com/metatool-ai/metamcp) instance with a namespace/endpoint scoped to
  Tapestry — see `docs/vendor-research/ANALYSIS-metamcp.md`.
