"""The durable, local-first checkpointer — `AsyncSqliteSaver`, no Postgres.

Per `docs/vendor-research/ANALYSIS-langgraph.md` §2: `AsyncSqliteSaver`
(`langgraph.checkpoint.sqlite.aio`) is the real, tested, file-based backend
for a single-process, local-first v1 — unlike `InMemorySaver` (docstring:
"debugging or testing purposes" only) it survives process restarts, and
unlike `SqliteSaver` it's actually async/safe for our stack.

Deviation from the task's literal `def get_checkpointer(...) -> AsyncSqliteSaver`
signature, worth flagging explicitly for whoever wires this into `main.py`:
this function is `async def`, not a plain sync one. It has to be — building
an `AsyncSqliteSaver` requires an open `aiosqlite.Connection` (an async
operation: `await aiosqlite.connect(...)`) AND `AsyncSqliteSaver.__init__`
itself calls `asyncio.get_running_loop()`, which raises outside a running
event loop. So `get_checkpointer()` must be awaited from inside an already-
running event loop (e.g. from `build_graph()` or an adapter's own async
startup path) — there is no way to make it a truly synchronous factory
without either blocking on `asyncio.run()` (which breaks if called from
inside an existing loop, e.g. from a test) or eagerly connecting at import
time (which breaks test isolation and multi-conversation path overrides).

We deliberately do NOT use `AsyncSqliteSaver.from_conn_string(...)` (the
`async with`-only classmethod shown in the ANALYSIS sketch) here, because
its context-manager form closes the underlying connection on `__aexit__` —
fine for a short-lived script, wrong for a checkpointer meant to live for
the whole process. Instead we open the `aiosqlite.Connection` directly and
hand it to `AsyncSqliteSaver`'s plain constructor, exactly like
`from_conn_string` does internally, minus the auto-close.

Schema setup: `AsyncSqliteSaver.setup()` is idempotent and already called
internally by every public method (`aget_tuple`, `aput`, ...) before it
touches the tables — see the installed source. We don't call it here; its
own docstring says as much ("should not be called directly by the user").
"""

from __future__ import annotations

import os

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

# Matches the task's specified default exactly.
DEFAULT_CHECKPOINT_PATH = "./tapestry_checkpoints.sqlite"

# Read at call time (not import time) so tests can monkeypatch/set the env
# var per-test without import-order games.
CHECKPOINT_PATH_ENV_VAR = "TAPESTRY_CHECKPOINT_PATH"


async def get_checkpointer(path: str | None = None) -> AsyncSqliteSaver:
    """Return a live `AsyncSqliteSaver` backed by a SQLite file at `path`.

    Resolution order: explicit `path` argument > `TAPESTRY_CHECKPOINT_PATH`
    env var > `DEFAULT_CHECKPOINT_PATH`. Every call opens a fresh
    `aiosqlite.Connection` — callers that want one shared checkpointer for
    the whole process (the normal case) should call this once and hold onto
    the result, e.g. inside `build.build_graph()`.

    The returned saver's underlying connection is never closed by this
    function. Callers that need to tear it down explicitly can do so via
    `saver.conn.close()` (an `AsyncSqliteSaver` exposes its `aiosqlite`
    connection as `.conn`).
    """
    resolved_path = path or os.environ.get(CHECKPOINT_PATH_ENV_VAR) or DEFAULT_CHECKPOINT_PATH

    # `aiosqlite.connect(...)` returns a `Connection` proxy synchronously;
    # `await`-ing it (via its `__await__`) is what actually starts the
    # background worker thread and opens the real sqlite3 connection — see
    # `aiosqlite/core.py`'s `Connection.__await__` -> `_connect()`.
    conn = await aiosqlite.connect(resolved_path)
    return AsyncSqliteSaver(conn)
