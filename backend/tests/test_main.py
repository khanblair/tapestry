"""Tests for `tapestry.main`'s orchestration logic.

Real end-to-end behavior (server actually starts, serves a real request,
shuts down cleanly on SIGINT) was verified manually — see the note in
`main.py`'s own module docstring. These tests cover what's actually unit-
testable: which adapters get started based on which env vars/tokens are
present, and that shutdown reaches every started adapter.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from tapestry import main as main_module
from tapestry.config import Settings


def _settings(*, discord: str | None = None, telegram: str | None = None) -> Settings:
    return Settings(
        discord_bot_token=discord,
        telegram_bot_token=telegram,
        api_host="127.0.0.1",
        api_port=0,
    )


@pytest.mark.asyncio
async def test_web_adapter_always_starts_even_with_no_tokens():
    web_started = asyncio.Event()

    async def fake_web_start(**_kwargs):
        web_started.set()
        await asyncio.Event().wait()  # block until cancelled, like the real uvicorn.Server.serve()

    with (
        patch.object(main_module, "load_settings", return_value=_settings()),
        patch.object(main_module.web_api, "start", side_effect=fake_web_start),
        patch.object(main_module.discord_bot, "start") as discord_start,
        patch.object(main_module.telegram_bot, "start") as telegram_start,
    ):
        run_task = asyncio.create_task(main_module.run())
        await asyncio.wait_for(web_started.wait(), timeout=2)

        discord_start.assert_not_called()
        telegram_start.assert_not_called()

        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task


@pytest.mark.asyncio
async def test_discord_and_telegram_start_only_when_their_token_is_set():
    web_started = asyncio.Event()
    discord_started = asyncio.Event()

    async def fake_web_start(**_kwargs):
        web_started.set()
        await asyncio.Event().wait()

    async def fake_discord_start(token: str, **_kwargs):
        assert token == "d-token"
        discord_started.set()
        await asyncio.Event().wait()

    fake_telegram_app = object()

    with (
        patch.object(
            main_module,
            "load_settings",
            return_value=_settings(discord="d-token", telegram="t-token"),
        ),
        patch.object(main_module.web_api, "start", side_effect=fake_web_start),
        patch.object(main_module.discord_bot, "start", side_effect=fake_discord_start),
        patch.object(
            main_module.telegram_bot, "start", new=AsyncMock(return_value=fake_telegram_app)
        ) as telegram_start,
        patch.object(main_module.telegram_bot, "stop", new=AsyncMock()) as telegram_stop,
    ):
        run_task = asyncio.create_task(main_module.run())
        await asyncio.wait_for(web_started.wait(), timeout=2)
        await asyncio.wait_for(discord_started.wait(), timeout=2)

        telegram_start.assert_awaited_once_with("t-token")

        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task

        # main.run()'s cancellation path (task.cancel() on itself, not a
        # signal) never reaches the stop_event-triggered shutdown branch,
        # so telegram_bot.stop is legitimately NOT called here — that
        # branch is exercised separately below, via the signal path.
        telegram_stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_signal_triggered_shutdown_stops_telegram_and_cancels_other_tasks():
    web_started = asyncio.Event()
    web_cancelled = asyncio.Event()

    async def fake_web_start(**_kwargs):
        web_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            web_cancelled.set()
            raise

    fake_telegram_app = object()

    with (
        patch.object(
            main_module, "load_settings", return_value=_settings(telegram="t-token")
        ),
        patch.object(main_module.web_api, "start", side_effect=fake_web_start),
        patch.object(main_module.discord_bot, "start") as discord_start,
        patch.object(
            main_module.telegram_bot, "start", new=AsyncMock(return_value=fake_telegram_app)
        ),
        patch.object(main_module.telegram_bot, "stop", new=AsyncMock()) as telegram_stop,
    ):
        run_task = asyncio.create_task(main_module.run())
        await asyncio.wait_for(web_started.wait(), timeout=2)

        # Simulate what the SIGINT handler does, without sending a real
        # OS signal (which pytest's own runner also listens for).
        loop = asyncio.get_running_loop()
        # main_module.run() registers its own stop_event on `loop` — reach
        # it the same way a real signal would: fire whatever callback was
        # registered for SIGINT. Simpler and just as real: call the same
        # `stop_event.set()` effect by finding no public handle exists, so
        # instead directly raise the signal main.py listens for.
        import signal

        loop.call_soon(lambda: __import__("os").kill(__import__("os").getpid(), signal.SIGINT))

        await asyncio.wait_for(run_task, timeout=3)

        telegram_stop.assert_awaited_once_with(fake_telegram_app)
        assert web_cancelled.is_set()
        discord_start.assert_not_called()
