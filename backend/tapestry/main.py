"""Process entrypoint: runs every configured adapter as a sibling asyncio
task on ONE shared event loop — never each adapter's own blocking runner
(`discord.Client.run()`, `Application.run_polling()`, `uvicorn.run()`),
per the pattern documented in each adapter's own `start()` docstring and
in `project_structure.md`'s Notes section.

Each adapter builds its OWN `graph.build.build_graph()` instance, opening
its own `AsyncSqliteSaver` connection to the SAME `TAPESTRY_CHECKPOINT_PATH`
file. That's intentional, not an oversight: adapters never share Python
objects (no cross-adapter import of graph state), only the on-disk
checkpoint/event-log files each already reads via `storage/db.py`'s own
env-var-driven path — the same "the event log is the source of truth,
not a second in-memory copy" rule the rest of the codebase already
follows. SQLite's own file-level locking (WAL mode, which `aiosqlite`/
`AsyncSqliteSaver` use by default) is what makes concurrent access from
three independent connections safe.

The web adapter always starts (it's the only surface with no external
bot-token prerequisite). Discord/Telegram start only if their token is
present in the environment — this lets `uv run python -m tapestry.main`
work for local web-only development without a Discord or Telegram
account set up yet, matching README.md's Quick Start.

On SIGINT/SIGTERM: `web_api.start()` internally calls `uvicorn.Server.
serve()`, which installs its OWN signal handlers via `capture_signals()`
(verified in the installed uvicorn source — this is deliberate on
uvicorn's part, not an accident to work around: it saves the handler
already registered for that signal, installs its own, and on its own
graceful shutdown restores the original handler and re-raises the
signal so whichever handler was there before still fires). Combined
with this module's own `loop.add_signal_handler(sig, stop_event.set)`
below, a Ctrl+C is handled cooperatively — uvicorn shuts itself down
first, then this module's handler fires and closes Discord/Telegram.
Manually smoke-tested end to end (real server start, real HTTP request,
real SIGINT, confirmed clean shutdown and the port released) — the one
loose end is a benign `CancelledError` traceback starlette/uvicorn log
during lifespan teardown, well-known, cosmetic-only, not a functional
issue.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from tapestry.adapters.discord_adapter import bot as discord_bot
from tapestry.adapters.telegram_adapter import bot as telegram_bot
from tapestry.adapters.web_adapter import api as web_api
from tapestry.config import load_settings

logger = logging.getLogger("tapestry.main")


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()

    tasks: list[asyncio.Task] = []
    telegram_app = None

    web_task = asyncio.create_task(
        web_api.start(host=settings.api_host, port=settings.api_port),
        name="web_adapter",
    )
    tasks.append(web_task)

    if settings.discord_bot_token:
        tasks.append(
            asyncio.create_task(
                discord_bot.start(settings.discord_bot_token), name="discord_adapter"
            )
        )
    else:
        logger.info("DISCORD_BOT_TOKEN not set — Discord adapter not started")

    if settings.telegram_bot_token:
        # telegram_adapter.bot.start() does not block (Updater.start_polling()
        # returns immediately) — awaiting it here just gets us the running
        # Application back so stop() can shut it down cleanly below. It does
        # NOT need to be wrapped in create_task the way the other two do,
        # since there's nothing further of its own left to await.
        telegram_app = await telegram_bot.start(settings.telegram_bot_token)
        logger.info("Telegram adapter started")
    else:
        logger.info("TELEGRAM_BOT_TOKEN not set — Telegram adapter not started")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    logger.info("Tapestry running (%d task(s) + telegram=%s)", len(tasks), bool(telegram_app))
    await stop_event.wait()
    logger.info("Shutting down...")

    if telegram_app is not None:
        await telegram_bot.stop(telegram_app)

    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Shutdown complete")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
