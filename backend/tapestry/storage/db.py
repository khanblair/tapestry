"""SQLite connection + schema management for Tapestry.

`get_connection` is a load-bearing part of the cross-package interface
contract: `core/events.py` imports it directly
(`from tapestry.storage.db import get_connection`) to get at the append-only
event log. Keep its name and signature stable.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_DEFAULT_DB_PATH = "./tapestry.sqlite"

# Module-level cache, keyed by resolved path. In the app's real usage pattern
# there is only ever one path (TAPESTRY_DB_PATH or the default), so this
# behaves as the single shared connection the async app wants -- one SQLite
# connection, reused across coroutines on the one event-loop thread, which is
# exactly why it's opened with check_same_thread=False. Keying by path
# (rather than one bare global) is a deliberate judgment call beyond what was
# specified: it costs nothing for the single-path real-app case and makes the
# cache safe to exercise from tests that each want their own isolated
# tmp_path database, without needing a manual reset hook between tests.
_connections: dict[str, sqlite3.Connection] = {}


def get_connection(path: str | None = None) -> sqlite3.Connection:
    """Return the cached SQLite connection for `path`.

    Resolution order: explicit `path` argument > `TAPESTRY_DB_PATH` env var >
    `./tapestry.sqlite`. The first time a given resolved path is opened, the
    schema is created automatically (idempotently) via `init_schema` before
    the connection is cached and returned.
    """
    resolved_path = path or os.environ.get("TAPESTRY_DB_PATH") or _DEFAULT_DB_PATH

    cached = _connections.get(resolved_path)
    if cached is not None:
        return cached

    conn = sqlite3.connect(resolved_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_schema(conn)

    _connections[resolved_path] = conn
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Run `schema.sql` against `conn`. Idempotent: safe to call repeatedly
    (the schema is entirely `CREATE ... IF NOT EXISTS`)."""
    schema_sql = _SCHEMA_PATH.read_text()
    conn.executescript(schema_sql)
    conn.commit()
