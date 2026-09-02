"""Shared fixtures for tapestry.adapters tests.

Each adapter touches persistence through TWO separately bound names:
`tapestry.core.events.get_connection` (imported at module load time by
`core/events.py`, used for the event log) and the adapter's own
`get_connection` (imported the same way by its `bot.py`, used for direct
`conversations` table access — `tapestry.adapters.discord_adapter.bot.
get_connection` for Discord, `tapestry.adapters.telegram_adapter.bot.
get_connection` for Telegram). All three bindings must be monkeypatched to
the SAME isolated in-memory connection for a test to be hermetic AND
internally consistent — patching only some of them would leave the rest
still pointed at the real on-disk default path.

`storage.db.init_schema` is run once per test up front so both `events`
and `conversations` exist — `core.events._ensure_schema` only ever
creates `events` (that module doesn't own `conversations`), so relying on
it alone would leave `conversations` missing and every
`ensure_conversation_row` call failing.
"""

from __future__ import annotations

import sqlite3

import pytest

from tapestry.adapters.discord_adapter import bot as discord_bot_module
from tapestry.adapters.telegram_adapter import bot as telegram_bot_module
from tapestry.core import events as events_module
from tapestry.storage.db import init_schema


@pytest.fixture(autouse=True)
def db_connection(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    init_schema(conn)
    monkeypatch.setattr(events_module, "get_connection", lambda: conn)
    monkeypatch.setattr(discord_bot_module, "get_connection", lambda: conn)
    monkeypatch.setattr(telegram_bot_module, "get_connection", lambda: conn)
    yield conn
    conn.close()
