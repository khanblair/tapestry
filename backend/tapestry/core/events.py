"""The append-only event log — the actual source of truth for everything.

Every fact about a conversation (a human's message, a persona's reply, a
delegation, a tool call, a turn boundary, an ask/answer exchange) is recorded
here as a `TapestryEvent` row and never mutated or deleted afterward.
Everything else in `core/` (conversation history, delegation round counts,
ask/answer resolution) is a *projection* computed by reading this log, never
a second stored representation.

Crash recovery — turn/start & turn/end pairing
------------------------------------------------
A "turn" is bracketed by a `turn/start` event and a matching `turn/end`
event. Matching is by id: a `turn/end` event's `payload["turn_id"]` equals
the `id` of the `turn/start` event it closes. This mirrors the same
lock-bracket pattern DeepSeek Harness uses for its own session log (see
`docs/vendor-research/ANALYSIS-deepseek-harness.md` §6) — detect an orphaned
bracket, don't hide it behind truncation or a false "finished" event.

`close_orphaned_turns()` finds any `turn/start` with no matching `turn/end`
and appends a synthetic repair `turn/end` carrying
`payload["reason"] == ORPHAN_REPAIR_REASON` ("interrupted"). That reason
string is RESERVED exclusively for this synthetic repair path.

Invariant assumed (not enforced) throughout core/: at most one `turn/start`
is open at a time per conversation -- turns don't nest. `delegation.py`'s
round-cap scoping and `close_orphaned_turns`'s "most recent open turn" logic
both rely on this. If `graph/` ever needs concurrent/nested turns per
conversation, both call sites need to be revisited together.

    No other code anywhere in this codebase — including `graph/`, which
    will emit real `turn/end` events as live turn-stopping decisions — may
    ever construct a `turn/end` event with `payload["reason"] == "interrupted"`.
    It exists so a reader can always tell "this turn ended because the
    process died mid-flight" apart from any real, intentional stopping
    decision (the model finished, the human stopped it, a budget was hit,
    etc.) — those must use a different reason string.

Callers must invoke `close_orphaned_turns(conversation_id)` once per
conversation at startup/resume, before any new turn begins, so a process
that died mid-turn never leaves a permanently-open turn behind.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel

from tapestry.storage.db import get_connection

# Reserved exclusively for the synthetic crash-recovery repair path in
# close_orphaned_turns(). See module docstring above.
ORPHAN_REPAIR_REASON = "interrupted"


class TapestryEvent(BaseModel):
    id: str
    conversation_id: str
    type: str
    timestamp: str
    actor: str
    payload: dict


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotently ensure the `events` table exists.

    core/ does not own schema.sql (that's storage/'s job), but it must be
    able to run standalone against anything that satisfies
    `get_connection() -> sqlite3.Connection`, including a bare, freshly
    created database in tests. CREATE TABLE IF NOT EXISTS is cheap enough to
    run on every call.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            actor TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_conversation_id "
        "ON events(conversation_id)"
    )
    conn.commit()


def append_event(conversation_id: str, type: str, actor: str, payload: dict) -> TapestryEvent:
    """Append one event to the log. Genuinely append-only.

    Never call UPDATE or DELETE against the `events` table from application
    code — every derived view (conversation history, delegation round
    counts, turn state) is a projection computed by re-reading this log, and
    that guarantee only holds if rows are never changed after insertion.
    """
    event = TapestryEvent(
        id=uuid.uuid4().hex,
        conversation_id=conversation_id,
        type=type,
        # Fixed microsecond precision, not the default isoformat() (which
        # drops the fractional part entirely when it's exactly zero). All
        # timestamp strings must be the same length/shape so that both
        # `ORDER BY rowid`-then-`timestamp` and read_events()'s `since`
        # lexicographic comparison agree with true chronological order.
        timestamp=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        actor=actor,
        payload=payload,
    )
    conn = get_connection()
    _ensure_schema(conn)
    conn.execute(
        "INSERT INTO events (id, conversation_id, type, timestamp, actor, payload_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            event.id,
            event.conversation_id,
            event.type,
            event.timestamp,
            event.actor,
            json.dumps(event.payload),
        ),
    )
    conn.commit()
    return event


def read_events(conversation_id: str, since: str | None = None) -> list[TapestryEvent]:
    """Read events for a conversation, ordered by insertion order.

    `since`, when given, is an ISO 8601 timestamp string; events with
    `timestamp >= since` are returned (inclusive, so passing an event's own
    timestamp back in still returns that event).
    """
    conn = get_connection()
    _ensure_schema(conn)
    if since is not None:
        cursor = conn.execute(
            "SELECT id, conversation_id, type, timestamp, actor, payload_json "
            "FROM events WHERE conversation_id = ? AND timestamp >= ? "
            "ORDER BY rowid ASC",
            (conversation_id, since),
        )
    else:
        cursor = conn.execute(
            "SELECT id, conversation_id, type, timestamp, actor, payload_json "
            "FROM events WHERE conversation_id = ? ORDER BY rowid ASC",
            (conversation_id,),
        )
    rows = cursor.fetchall()
    return [
        TapestryEvent(
            id=row[0],
            conversation_id=row[1],
            type=row[2],
            timestamp=row[3],
            actor=row[4],
            payload=json.loads(row[5]),
        )
        for row in rows
    ]


def read_recent_events(types: set[str] | None = None, limit: int = 50) -> list[TapestryEvent]:
    """Read the most recent events **across every conversation**, newest first.

    The one exception to "the event log is always read scoped to one
    conversation" (see `schema.sql`'s own comment on why that's the only
    index that matters) — a cross-conversation activity feed (Activity
    screen's "Running now" / "Recent") genuinely needs this, and
    `read_events(conversation_id)` structurally cannot answer it. `types`,
    when given, restricts to those event types via a `WHERE type IN (...)`
    clause evaluated by SQLite before `LIMIT` is applied, so `limit` means
    "the N most recent matching events," not "the N most recent events,
    then filter" (which could return fewer than `limit` even when more
    matches exist further back).
    """
    conn = get_connection()
    _ensure_schema(conn)
    if types:
        placeholders = ",".join("?" for _ in types)
        cursor = conn.execute(
            "SELECT id, conversation_id, type, timestamp, actor, payload_json "
            f"FROM events WHERE type IN ({placeholders}) ORDER BY rowid DESC LIMIT ?",
            (*types, limit),
        )
    else:
        cursor = conn.execute(
            "SELECT id, conversation_id, type, timestamp, actor, payload_json "
            "FROM events ORDER BY rowid DESC LIMIT ?",
            (limit,),
        )
    rows = cursor.fetchall()
    return [
        TapestryEvent(
            id=row[0],
            conversation_id=row[1],
            type=row[2],
            timestamp=row[3],
            actor=row[4],
            payload=json.loads(row[5]),
        )
        for row in rows
    ]


def close_orphaned_turns(conversation_id: str) -> list[TapestryEvent]:
    """Close any `turn/start` in this conversation with no matching `turn/end`.

    A `turn/start` is "matched" when some later `turn/end` event's
    `payload["turn_id"]` equals the `turn/start` event's `id`. Any
    `turn/start` left unmatched after scanning the whole log gets a
    synthetic `turn/end` appended, with `actor="system"` and
    `payload={"turn_id": <start id>, "reason": ORPHAN_REPAIR_REASON}`.

    Returns the list of synthetic repair events created (empty if nothing
    was orphaned). Intended to be called once per conversation at
    startup/resume, before any new turn begins.
    """
    events = read_events(conversation_id)
    open_starts: dict[str, TapestryEvent] = {}
    for event in events:
        if event.type == "turn/start":
            open_starts[event.id] = event
        elif event.type == "turn/end":
            turn_id = event.payload.get("turn_id")
            if turn_id is not None:
                open_starts.pop(turn_id, None)

    closed: list[TapestryEvent] = []
    for turn_id in open_starts:
        closed_event = append_event(
            conversation_id=conversation_id,
            type="turn/end",
            actor="system",
            payload={"turn_id": turn_id, "reason": ORPHAN_REPAIR_REASON},
        )
        closed.append(closed_event)
    return closed
