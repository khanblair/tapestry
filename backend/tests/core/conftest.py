"""Shared fixtures for tapestry.core tests.

`core/`'s modules all funnel their persistence through
`tapestry.core.events.get_connection` (imported from `tapestry.storage.db`).
Rather than depending on the real storage/db.py (owned by a sibling agent,
and pointed at a real file by default), every test in this package gets an
isolated in-memory SQLite connection per test via autouse monkeypatching, so
tests never share state and never touch disk.
"""

from __future__ import annotations

import sqlite3

import pytest

from tapestry.core import events as events_module


@pytest.fixture(autouse=True)
def db_connection(monkeypatch):
    conn = sqlite3.connect(":memory:")
    monkeypatch.setattr(events_module, "get_connection", lambda: conn)
    yield conn
    conn.close()
