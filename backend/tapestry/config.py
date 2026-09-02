"""Central env loading for `main.py`.

Every other module in this codebase reads its own env vars directly at
call time (see e.g. `storage/db.py`'s `TAPESTRY_DB_PATH`, `graph/build.py`'s
`TAPESTRY_WORKSPACE_ROOT`) — that pattern is left alone here, since each of
those already has its own documented default and this file would just be
a second, driftable copy of the same defaults. What genuinely has no home
yet is the handful of values `main.py` itself needs directly (the bot
tokens `discord_adapter.bot.start`/`telegram_adapter.bot.start` take as
plain string arguments, plus the web adapter's host/port) — this module
loads `.env` once via `python-dotenv` and exposes those as a typed
`Settings` object, so `main.py` has one place to read from instead of
scattering `os.environ.get(...)` calls at the composition root.

Full env var reference (all optional except the model provider keys,
where at least one is required, and DISCORD_BOT_TOKEN/TELEGRAM_BOT_TOKEN,
required only if that adapter is started — see `main.py`):

    ANTHROPIC_API_KEY, DEEPSEEK_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY
        Read directly by litellm itself, not by this module or Tapestry's
        own code — litellm picks the right one per persona.model's provider
        prefix. Nothing to centralize.
    DISCORD_BOT_TOKEN, TELEGRAM_BOT_TOKEN
        Read here; passed to the adapters' start() functions.
    METAMCP_URL, METAMCP_API_KEY               (tools/mcp_client.py)
    TAPESTRY_DB_PATH                           (storage/db.py)
    TAPESTRY_CHECKPOINT_PATH                   (graph/checkpointer.py)
    TAPESTRY_WORKSPACE_ROOT                    (graph/build.py)
    TAPESTRY_ALLOWED_EDIT_PATHS                (graph/build.py)
    TAPESTRY_GIT_MCP_TOOL_PREFIX               (graph/build.py)
    TAPESTRY_DEPLOY_MCP_TOOL_PREFIX            (graph/build.py)
    TAPESTRY_PERSONAS_DIR                      (adapters/web_adapter/api.py)
    TAPESTRY_WEB_ORIGINS                       (adapters/web_adapter/api.py)
    TAPESTRY_API_HOST, TAPESTRY_API_PORT       (this file, for main.py)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Idempotent — safe to import this module more than once (e.g. from both
# main.py and a test). Does not override a variable already set in the
# real environment, so `docker-compose.yml`'s env_file mechanism (which
# sets real env vars before the process even starts) always wins over a
# stale .env file that happened to also be present.
load_dotenv()


@dataclass(frozen=True)
class Settings:
    discord_bot_token: str | None
    telegram_bot_token: str | None
    api_host: str
    api_port: int


def load_settings() -> Settings:
    return Settings(
        discord_bot_token=os.environ.get("DISCORD_BOT_TOKEN") or None,
        telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        api_host=os.environ.get("TAPESTRY_API_HOST", "0.0.0.0"),
        api_port=int(os.environ.get("TAPESTRY_API_PORT", "8000")),
    )
