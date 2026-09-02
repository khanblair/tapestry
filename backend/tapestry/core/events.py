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

Concurrent turns per conversation are expected, not an edge case: a tag-all
fan-out spawns one turn per mentioned persona, each on its own LangGraph
thread (`payload["graph_thread_id"]`), running at the same time. Matching
and scoping are therefore always by id, never by "most recent" position:
`delegation.py`'s round-cap scoping takes an explicit `turn_id` to slice
from, and `close_orphaned_turns` only auto-closes turns whose
`graph_thread_id` equals the conversation id (`is_main_thread_turn`) --
a fan-out leg's own open turn is deliberately left alone, since a blind
log scan can't tell a live paused approval apart from a crash.

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


def find_open_turns(events: list[TapestryEvent]) -> dict[str, TapestryEvent]:
    """Return every `turn/start` in `events` with no matching `turn/end`,
    keyed by the `turn/start` event's own id, in the order each was opened.

    A `turn/start` is "matched" when some later `turn/end` event's
    `payload["turn_id"]` equals its id. This is the one shared scan
    `close_orphaned_turns` (crash-recovery repair) and the turn-concurrency
    guard (`web_adapter/api.py`'s `send_message` and the Discord/Telegram
    equivalents — reject a new turn while one is already open) both need;
    extracted so the matching logic exists in exactly one place instead of
    being hand-rolled at each call site.

    Pre-fan-out (see `graph_thread_id`, added alongside tag-all), at most
    one entry is ever returned per conversation — that invariant is
    documented, not enforced, at the top of this module.
    """
    open_starts: dict[str, TapestryEvent] = {}
    for event in events:
        if event.type == "turn/start":
            open_starts[event.id] = event
        elif event.type == "turn/end":
            turn_id = event.payload.get("turn_id")
            if turn_id is not None:
                open_starts.pop(turn_id, None)
    return open_starts


# How long an open `turn/start` is still trusted for live "busy" status
# before being treated as stale (see `list_open_turns` below) — caught in
# review: `close_orphaned_turns` deliberately never auto-closes a fan-out
# leg's own open turn/start (a blind log scan can't tell a leg still
# genuinely paused at a live approval apart from one truly abandoned by a
# crash — see that function's own docstring). Left completely unbounded,
# a crashed fan-out leg would show that persona as "busy" in every roster
# render, forever, with no in-app way to clear it -- turning the exact
# dynamic-status placeholder this was built to fix right back into one.
# One hour is generous enough that a real, live human-approval wait is
# never mistaken for stale, while still bounding how long a genuine crash
# can wedge a persona's displayed status.
STALE_OPEN_TURN_AGE_SECONDS = 3600


def list_open_turns(max_age_seconds: float = STALE_OPEN_TURN_AGE_SECONDS) -> dict[str, TapestryEvent]:
    """Return the currently open `turn/start` event for every persona that
    has one, across ALL conversations, keyed by persona id (a `turn/start`
    event's own `actor` — see `graph/build.py`'s `persona_node`, which sets
    `actor=persona.id`).

    A real, unbounded (by event count) scan of every `turn/start`/`turn/end`
    row — deliberately NOT `read_recent_events(limit=...)`, which is
    cross-conversation but window-bounded and could miss, or misreport, an
    old crashed turn sitting in a quiet conversation depending on how much
    activity happened elsewhere in the meantime. This is the read side of
    deriving a persona's live status (see `web_adapter/api.py`'s
    `_persona_to_out`) rather than writing one to the persona's YAML —
    that module has its own reasoning for why status must be a projection.

    Bounded by AGE, though (`max_age_seconds`): an open `turn/start` older
    than this is excluded from the result, even though it's still
    genuinely "open" in the log. This is specifically for the fan-out-leg
    orphans `close_orphaned_turns` deliberately leaves open (see that
    function's own docstring) — without an age bound, a crashed fan-out
    leg would show its persona as permanently "busy," in every
    conversation, with no way to clear it. A real, live approval wait is
    expected to resolve in well under an hour; something still open past
    that is far more likely a stale crash artifact than a human who
    hasn't gotten to it yet.

    With concurrent tag-all fan-out (see
    `tapestry_mentions_concurrency_status_spec.md` §2), several different
    personas can legitimately have a `turn/start` open in the same
    conversation at once — expected, not something this collapses away. If
    the same persona somehow has more than one open turn (e.g. across two
    different conversations), only one is returned — status only needs
    "is this persona busy at all," not an exhaustive list of where.
    """
    conn = get_connection()
    _ensure_schema(conn)
    cursor = conn.execute(
        "SELECT id, conversation_id, type, timestamp, actor, payload_json "
        "FROM events WHERE type IN ('turn/start', 'turn/end') ORDER BY rowid ASC"
    )
    rows = cursor.fetchall()
    all_events = [
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
    open_starts = find_open_turns(all_events)
    now = datetime.now(timezone.utc)
    fresh: dict[str, TapestryEvent] = {}
    for event in open_starts.values():
        age_seconds = (now - datetime.fromisoformat(event.timestamp)).total_seconds()
        if age_seconds <= max_age_seconds:
            fresh[event.actor] = event
    return fresh


def is_main_thread_turn(event: TapestryEvent, conversation_id: str) -> bool:
    """True if `event` (a `turn/start`) ran on `conversation_id`'s own
    LangGraph checkpoint thread — as opposed to a tag-all fan-out leg's own
    thread (see `graph/build.py`'s `persona_node` and
    `tapestry_mentions_concurrency_status_spec.md` §2.2).

    A `turn/start` with no `graph_thread_id` at all (recorded before this
    field existed) is treated as main-thread — that's the only kind of
    turn that could exist before fan-out shipped.
    """
    return event.payload.get("graph_thread_id", conversation_id) == conversation_id


def close_orphaned_turns(conversation_id: str) -> list[TapestryEvent]:
    """Close any MAIN-THREAD `turn/start` in this conversation with no
    matching `turn/end`.

    Any such `turn/start` left unmatched after scanning the whole log (see
    `find_open_turns`) gets a synthetic `turn/end` appended, with
    `actor="system"` and
    `payload={"turn_id": <start id>, "reason": ORPHAN_REPAIR_REASON}`.

    Deliberately does NOT auto-close a fan-out leg's own open `turn/start`
    (`is_main_thread_turn` filters those out) — a blind log-only scan can't
    tell a fan-out leg that's still genuinely paused at a live approval
    apart from one truly abandoned by a crash, and closing the wrong one
    would silently orphan a real, resumable interrupt. A crashed fan-out
    leg is a known, accepted gap left open by this filter (it stays
    "busy" in status derivation indefinitely) rather than risking that —
    see `tapestry_mentions_concurrency_status_spec.md` §1's own note that
    a proper fix would cross-check each leg's own LangGraph checkpoint
    before closing it, not yet built.

    Returns the list of synthetic repair events created (empty if nothing
    was orphaned). Intended to be called once per conversation at
    startup/resume, before any new turn begins.
    """
    open_starts = find_open_turns(read_events(conversation_id))

    closed: list[TapestryEvent] = []
    for turn_id, start_event in open_starts.items():
        if not is_main_thread_turn(start_event, conversation_id):
            continue
        closed_event = append_event(
            conversation_id=conversation_id,
            type="turn/end",
            actor="system",
            payload={"turn_id": turn_id, "reason": ORPHAN_REPAIR_REASON},
        )
        closed.append(closed_event)
    return closed
