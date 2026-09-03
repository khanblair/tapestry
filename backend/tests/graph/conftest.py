"""Shared fixtures for tapestry.graph tests.

Mirrors `tests/core/conftest.py`'s approach exactly: every module under
`tapestry.core` that graph/ code calls (`events`, `ask`, `delegation`, ...)
funnels persistence through `tapestry.core.events.get_connection`
(imported from `tapestry.storage.db`). Swapping that one function out for
an isolated in-memory SQLite connection, autouse, keeps every test in this
package hermetic — no disk writes to the real `storage/db.py` default path,
no state shared across tests — while still exercising the REAL event-log
mechanics (append_event/read_events, turn/start-turn/end bracketing,
ask/requested-ask/answered), not a mock of them.

Graph tests additionally use REAL `AsyncSqliteSaver` checkpointers backed
by `tmp_path` files (see test_checkpointer.py / test_build.py) — those are
deliberately NOT covered by this fixture, since the whole point of using
LangGraph's checkpointer is to prove real interrupt/resume/checkpoint
persistence, not an in-memory stand-in for it.

One deliberate deviation from `tests/core/conftest.py`'s otherwise-identical
fixture: `check_same_thread=False`. `graph.build.approval_node` is a plain
sync function (it must be, to call `interrupt()` directly), so LangGraph
dispatches it via a thread-pool executor rather than running it on the
event-loop thread the way async nodes run — see `_internal/_runnable.py`'s
`coerce_to_runnable`. A same-thread-only in-memory connection created in
the test's main thread would then raise `sqlite3.ProgrammingError` the
moment `approval_node` calls `events.append_event` from a worker thread.
The real `storage/db.py` already opens its connection with
`check_same_thread=False` for exactly this kind of cross-thread access —
this fixture matches that, rather than papering over a fixture-only
mismatch with the real app's own connection configuration.
"""

from __future__ import annotations

import sqlite3

import pytest

from tapestry.core import events as events_module
from tapestry.graph import build


@pytest.fixture(autouse=True)
def db_connection(monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    monkeypatch.setattr(events_module, "get_connection", lambda: conn)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def no_reply_breathing_pause(monkeypatch):
    """`persona_node`'s plain-reply path now sleeps for a few real seconds
    of simulated "typing time" before landing (see `build._breathing_pause`
    / `build._reply_delay_seconds`) — a deliberate UX pacing choice, not
    something any test here should actually have to wait through. Patched
    to an instant no-op for every test in this package; the delay
    calculation itself is covered by its own direct unit tests instead.
    """

    async def _instant(seconds: float) -> None:
        return None

    monkeypatch.setattr(build, "_breathing_pause", _instant)
