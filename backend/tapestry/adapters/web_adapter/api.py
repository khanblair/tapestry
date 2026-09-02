"""The web adapter — the surface the Next.js app (`web/`) talks to.

Third chat surface, built last per the scoped spec's build sequence, against
an already-proven core (`core/events.py`, `core/personas.py`,
`core/conversations.py`, `core/ask.py`) and an already-tested graph
(`graph/build.py`, 180 pre-existing tests). Nothing in `graph/build.py` is
modified by this module — it is used exactly as documented (`build_graph`,
`new_state`, `ainvoke`/`astream`, `Command(resume=...)`).

Wire convention
----------------
Every request/response body is JSON, **camelCase on the wire**, matching
`web/lib/api.ts`/`safeApi.ts`/`search.ts`/`approvals.ts` field-for-field —
those files were read in full and are the actual contract this module
serves, not a re-derivation from the task brief's prose. Every Python object
underneath (core's `Persona`, `TapestryEvent`, `Message`, ...) stays
snake_case, matching the rest of the codebase.

The translation is done with one convention, used everywhere in this file:
every wire-facing Pydantic model subclasses `CamelModel`, which sets
`alias_generator=pydantic.alias_generators.to_camel` plus
`populate_by_name=True` (so a request body may use either the snake_case
Python name or the camelCase alias) and is always serialized with
`model_dump(by_alias=True)` / FastAPI's default `response_model` behavior
(which already serializes by alias). No manual `snake_to_camel` string
juggling anywhere else in this file — one mechanism, one place.

Judgment calls worth reading before assuming a piece of this is arbitrary
--------------------------------------------------------------------------
1. **Four places where the real frontend disagrees with the task brief's
   prose, resolved in favor of the real frontend** (per the task's own
   instruction to treat `web/lib/*.ts` as the actual contract, not a guess):
   - `answerAsk` posts to `POST /api/conversations/{id}/ask/answers` with a
     BATCH body `{answers: AskAnswer[]}` (`web/lib/api.ts`), not
     `POST .../ask/{questionId}/answer`. Both are served here — the batch
     path is primary/real; the brief's single-question path is also wired
     (as a convenience alias, question id taken from the URL) so neither
     the shipped frontend nor the brief's literal text breaks.
   - `updatePersona` sends `PATCH /api/personas/{id}`
     (`web/lib/api.ts`), not `PUT`. Both methods are registered against the
     same handler.
   - `pauseAllAgents` posts to `POST /api/agents/pause-all`
     (`web/lib/safeApi.ts`), not `/api/agents/pause`. Both paths are
     registered against the same handler.
   - `answerAsk`/`pauseAllAgents` both resolve on **204 No Content** with no
     body — `web/lib/api.ts`'s `request()` helper special-cases exactly
     `res.status === 204` to skip `res.json()`; returning 200 with an empty
     body would make the frontend crash trying to parse it.

2. **`sendMessage` never blocks on the agent turn.** It appends the
   `user/message` event, fires the graph turn as a background
   `asyncio.create_task` (`_drive_turn`), and returns the just-appended
   user message immediately. The turn's own output (assistant replies,
   approval prompts) reaches the frontend exclusively over
   `/ws/conversations/{id}` as `{"type": "message", "payload": <Message>}`
   frames — the ONE frame shape
   `web/components/conversation/ConversationView.tsx` actually reads (see
   that file's own comment: every other frame type is explicitly ignored).
   `_drive_turn` also forwards `graph.astream(..., stream_mode="custom")`'s
   raw frames verbatim (the coarse `persona/thinking`/`tool/status` status
   frames `graph/build.py` already emits), satisfying the "forward astream
   custom events over the WS" requirement even though the current frontend
   doesn't key off them yet.

3. **Which persona answers a new message**: `conversation.persona_ids[0]`
   is always the turn's entry persona — for a DM that's the only
   candidate; for a group it's the lead persona, consistent with the
   scoped spec's own delegation model (the model's own `delegate` tool
   call inside the graph is what fans a turn out to other personas, not a
   per-message routing decision made here).

4. **Conversation storage adds no schema.** `storage/schema.sql`'s
   `conversations` table has no column for participant personas — adding
   one would mean altering a schema owned by `storage/` for a shape only
   this adapter needs today. Instead, `persona_ids` is recorded as a
   `"conversation/created"` event (kind/name duplicated there too), and
   `GET /api/conversations` projects `personaIds`/`lastPreview`/`updatedAt`
   back out of the event log — the exact same "event log is the actual
   source of truth, everything else is a projection" rule `core/events.py`
   and `core/conversations.py` already establish, just applied to one more
   shape. See `_conversation_meta`.

5. **Lazy DM vivification.** `web/app/new-conversation/page.tsx` already
   links straight to `/conversation/dm-${persona.id}` without ever calling
   a create-conversation endpoint — there is no earlier point where that
   conversation's row would otherwise exist. `GET`/`POST .../messages`
   auto-create a conversation the first time it's touched, but ONLY when
   the id matches the `dm-<personaId>` convention against a real,
   currently-loaded persona (see `_lazy_vivify_dm`) — never a general
   "create anything" fallback. `POST /api/conversations` (createConversation)
   is also built as a real endpoint, per the task brief, for whenever the
   frontend is ready to stop relying on the URL convention and call it
   directly (`safeApi.ts`/`new-conversation/page.tsx` both flag this exact
   gap in their own comments).

6. **`Message.activity`/`.diff` are honest, not fabricated.** They are
   NEVER populated by this module. `tool/result` doesn't durably encode
   "still running" (only the final result is logged — the live
   in-progress state is exactly what `tool/status` custom-stream frames
   are for, not history), and `task/diff_ready`'s payload
   (`{task_id, files_changed, diff_summary}`, see `graph/build.py`'s
   `execute_node`) carries no add/del line counts at all. Rather than
   defaulting `diff.add`/`diff.deletions` to 0 (which would look like real
   data), both fields are simply left `None` — a documented upstream gap,
   in the same spirit as `graph/build.py`'s own "real per-token streaming
   is NOT wired here" note.

7. **`core/personas.py` gained one new function, `save_persona`** (write
   the inverse of `load_personas`, for exactly one persona). Flagged here
   because `core/` is a shared package: `load_personas` was read-only, and
   persona *content* is documented (that module's own header) as living in
   `personas/*.yaml`, so a real create/update endpoint has nowhere else to
   durably persist a change. Purely additive — `load_personas` itself is
   untouched.

8. **`graph.build.PERSONAS` is kept in sync, in place.** That module-level
   dict is loaded once at import time (see its own comment) and is exactly
   what the graph reads a persona's model/tools/system_prompt from
   mid-turn. Every persona-mutating endpoint here (create/update/pause-all)
   calls `_refresh_graph_personas` afterward, which does
   `graph_build.PERSONAS.clear(); graph_build.PERSONAS.update(fresh)` —
   mutated in place, never rebound, since `graph.build._get_persona` (and
   anything else that imported the name `PERSONAS` directly) holds a
   reference to that exact object. The directory this reads from is
   test-isolable via `TAPESTRY_PERSONAS_DIR` (see `_personas_dir`) —
   `create_app()` syncs `graph.build.PERSONAS` to that directory at
   startup, so a test pointed at an isolated tmp copy never touches the
   real `personas/*.yaml` files, and a persona created mid-test is
   immediately messageable through the graph in that same process.

Endpoint list
-------------
    GET    /api/personas
    POST   /api/personas
    PATCH  /api/personas/{persona_id}      (PUT also registered, same handler)
    GET    /api/conversations
    POST   /api/conversations
    GET    /api/conversations/{conversation_id}/messages
    POST   /api/conversations/{conversation_id}/messages
    POST   /api/conversations/{conversation_id}/ask/answers            (real, batch)
    POST   /api/conversations/{conversation_id}/ask/{question_id}/answer  (alias)
    GET    /api/search?q=...
    GET    /api/asks/pending
    POST   /api/agents/pause-all           (POST /api/agents/pause also registered)
    WS     /ws/conversations/{conversation_id}
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from tapestry.core import events
from tapestry.core.ask import AskAnswer
from tapestry.core.personas import Persona, load_personas, save_persona
from tapestry.graph import build as graph_build
from tapestry.storage.db import get_connection

__all__ = ["create_app", "start"]

# ---------------------------------------------------------------------------
# Repo-root-anchored paths + env-var configuration. Mirrors graph/build.py's
# own `_REPO_ROOT` derivation (see that module's comment for why a bare
# relative path is wrong here) — one level deeper, since this file lives one
# directory further from the repo root than graph/build.py does.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_PERSONAS_DIR = _REPO_ROOT / "personas"

PERSONAS_DIR_ENV_VAR = "TAPESTRY_PERSONAS_DIR"
CORS_ORIGINS_ENV_VAR = "TAPESTRY_WEB_ORIGINS"
DEFAULT_CORS_ORIGIN = "http://localhost:3000"


def _personas_dir() -> str:
    """Read at call time (not import time) — same reasoning as
    `checkpointer.get_checkpointer`/`storage.db.get_connection`: tests set
    this per-test via `monkeypatch.setenv`, with no import-order games.
    """
    return os.environ.get(PERSONAS_DIR_ENV_VAR) or str(_DEFAULT_PERSONAS_DIR)


def _cors_origins() -> list[str]:
    raw = os.environ.get(CORS_ORIGINS_ENV_VAR)
    if not raw:
        return [DEFAULT_CORS_ORIGIN]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# ---------------------------------------------------------------------------
# Wire models — camelCase via CamelModel; see module docstring.
# ---------------------------------------------------------------------------


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


PersonaStatus = Literal["online", "busy", "paused", "offline"]


class PersonaOut(CamelModel):
    id: str
    name: str
    role: str
    model: str
    status: PersonaStatus
    color: str
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    mcp: list[str] = Field(default_factory=list)


class PersonaDraftIn(CamelModel):
    """`POST /api/personas` body — matches `web/lib/api.ts`'s `PersonaDraft`."""

    name: str
    role: str
    model: str
    status: PersonaStatus | None = None
    color: str | None = None
    system_prompt: str | None = None
    tools: list[str] | None = None
    mcp: list[str] | None = None


class PersonaUpdateIn(CamelModel):
    """`PATCH /api/personas/{id}` body — `Partial<PersonaDraft>`: every
    field optional, only the ones present are applied.
    """

    name: str | None = None
    role: str | None = None
    model: str | None = None
    status: PersonaStatus | None = None
    color: str | None = None
    system_prompt: str | None = None
    tools: list[str] | None = None
    mcp: list[str] | None = None


class ConversationOut(CamelModel):
    id: str
    kind: Literal["dm", "group"]
    name: str | None = None
    persona_ids: list[str]
    last_preview: str | None = None
    updated_at: str


class ConversationCreateIn(CamelModel):
    kind: Literal["dm", "group"]
    name: str | None = None
    persona_ids: list[str] = Field(default_factory=list)


class AskQuestionOut(CamelModel):
    id: str
    question: str
    detail: str | None = None
    options: list[str] | None = None
    multi_select: bool | None = None
    intent: str | None = None


class MessageActivityOut(CamelModel):
    label: str
    done: bool
    result: str | None = None


class MessageDiffOut(CamelModel):
    task_id: str
    files: int
    add: int
    deletions: int = Field(alias="del")


class MessageOut(CamelModel):
    id: str
    conversation_id: str
    actor: str
    text: str
    timestamp: str
    event_type: str
    thread_id: str | None = None
    # Never populated — see module docstring judgment call 6.
    activity: MessageActivityOut | None = None
    diff: MessageDiffOut | None = None
    approval: AskQuestionOut | None = None


class SendMessageIn(CamelModel):
    text: str


class AskAnswerIn(CamelModel):
    id: str
    selected: list[str] | None = None
    custom: str | None = None


class AnswerAskBatchIn(CamelModel):
    answers: list[AskAnswerIn]


class AskAnswerBodyIn(CamelModel):
    """Body for the single-question convenience alias — no `id` field;
    the URL already carries `question_id`.
    """

    selected: list[str] | None = None
    custom: str | None = None


class SearchMessageResultOut(CamelModel):
    kind: Literal["message"] = "message"
    conversation_id: str
    conversation_label: str
    actor: str
    snippet: str


class SearchPersonaResultOut(CamelModel):
    kind: Literal["persona"] = "persona"
    persona: PersonaOut


class SearchResultsOut(CamelModel):
    messages: list[SearchMessageResultOut]
    personas: list[SearchPersonaResultOut]


class PendingApprovalOut(CamelModel):
    conversation_id: str
    conversation_label: str
    question: AskQuestionOut


# ---------------------------------------------------------------------------
# Persona helpers (module-level, app-independent).
# ---------------------------------------------------------------------------


def _load_personas() -> dict[str, Persona]:
    return load_personas(_personas_dir())


def _persona_to_out(persona: Persona) -> PersonaOut:
    return PersonaOut(
        id=persona.id,
        name=persona.name,
        role=persona.role,
        model=persona.model,
        status=persona.status,
        color=persona.color,
        system_prompt=persona.system_prompt,
        tools=list(persona.tools),
        mcp=list(persona.mcp_servers),
    )


def _refresh_graph_personas(directory: str) -> dict[str, Persona]:
    """See module docstring judgment call 8."""
    fresh = load_personas(directory)
    graph_build.PERSONAS.clear()
    graph_build.PERSONAS.update(fresh)
    return graph_build.PERSONAS


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or uuid.uuid4().hex[:8]


def _unique_persona_id(base: str, existing: dict[str, Persona]) -> str:
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"


# ---------------------------------------------------------------------------
# Conversation helpers — storage/schema.sql's existing 4-column
# `conversations` table (untouched) + a "conversation/created" event for
# everything the table has no column for. See module docstring judgment
# call 4.
# ---------------------------------------------------------------------------

_DM_ID_RE = re.compile(r"^dm-(?P<persona_id>.+)$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _get_conversation_row(conversation_id: str) -> sqlite3.Row | None:
    conn = get_connection()
    cursor = conn.execute(
        "SELECT id, kind, name, created_at FROM conversations WHERE id = ?",
        (conversation_id,),
    )
    return cursor.fetchone()


def _list_conversation_rows() -> list[sqlite3.Row]:
    conn = get_connection()
    cursor = conn.execute(
        "SELECT id, kind, name, created_at FROM conversations ORDER BY created_at ASC"
    )
    return cursor.fetchall()


def _insert_conversation_row(conversation_id: str, kind: str, name: str | None) -> sqlite3.Row:
    conn = get_connection()
    conn.execute(
        "INSERT INTO conversations (id, kind, name, created_at) VALUES (?, ?, ?, ?)",
        (conversation_id, kind, name, _now_iso()),
    )
    conn.commit()
    row = _get_conversation_row(conversation_id)
    assert row is not None
    return row


def _create_conversation(
    conversation_id: str, kind: str, name: str | None, persona_ids: list[str]
) -> sqlite3.Row:
    row = _insert_conversation_row(conversation_id, kind, name)
    events.append_event(
        conversation_id,
        "conversation/created",
        actor="system",
        payload={"kind": kind, "name": name, "persona_ids": list(persona_ids)},
    )
    return row


def _conversation_meta(conversation_id: str) -> tuple[list[str], str | None, str | None]:
    """Derive `(persona_ids, last_preview, updated_at)` from one scan of
    the conversation's event log. `updated_at` falls back to the
    `conversations` row's own `created_at` (done by the caller) if there
    are no events at all yet.
    """
    persona_ids: list[str] = []
    last_preview: str | None = None
    last_timestamp: str | None = None
    for event in events.read_events(conversation_id):
        last_timestamp = event.timestamp
        if event.type == "conversation/created":
            persona_ids = list(event.payload.get("persona_ids") or [])
        elif event.type.endswith("/message"):
            last_preview = event.payload.get("text", "")
    return persona_ids, last_preview, last_timestamp


def _conversation_row_to_out(row: sqlite3.Row) -> ConversationOut:
    persona_ids, last_preview, last_timestamp = _conversation_meta(row["id"])
    return ConversationOut(
        id=row["id"],
        kind=row["kind"],
        name=row["name"],
        persona_ids=persona_ids,
        last_preview=last_preview,
        updated_at=last_timestamp or row["created_at"],
    )


def _lazy_vivify_dm(conversation_id: str, personas: dict[str, Persona]) -> sqlite3.Row | None:
    """See module docstring judgment call 5. Returns None (caller 404s)
    unless `conversation_id` matches `dm-<personaId>` for a real, known
    persona — never a general auto-create fallback.
    """
    match = _DM_ID_RE.match(conversation_id)
    if match is None:
        return None
    persona_id = match.group("persona_id")
    if persona_id not in personas:
        return None
    return _create_conversation(conversation_id, kind="dm", name=None, persona_ids=[persona_id])


def _ensure_orphans_closed(conversation_id: str, app: FastAPI) -> None:
    """`core.events.close_orphaned_turns` is the caller's responsibility,
    once per conversation before any NEW turn begins (its own docstring).
    A conversation legitimately paused at an approval `interrupt()` has an
    open `turn/start` with no `turn/end` — that is correct, in-flight
    state, not a crash artifact, so this must never re-run mid-pause.
    Guarded via `app.state.closed_orphan_conversations`: once per
    conversation, the first time THIS process touches it (the closest
    equivalent to "at startup" a stateless-per-request API has).
    """
    closed: set[str] = app.state.closed_orphan_conversations
    if conversation_id in closed:
        return
    events.close_orphaned_turns(conversation_id)
    closed.add(conversation_id)


def _ensure_conversation(conversation_id: str, app: FastAPI) -> sqlite3.Row:
    row = _get_conversation_row(conversation_id)
    if row is None:
        row = _lazy_vivify_dm(conversation_id, _load_personas())
    if row is None:
        raise HTTPException(status_code=404, detail=f"conversation {conversation_id!r} not found")
    _ensure_orphans_closed(conversation_id, app)
    return row


# ---------------------------------------------------------------------------
# Message projection. See module docstring judgment call 6.
# ---------------------------------------------------------------------------


def _question_dict_to_out(question: dict) -> AskQuestionOut:
    return AskQuestionOut(
        id=question.get("id", ""),
        question=question.get("question", ""),
        detail=question.get("detail"),
        options=question.get("options"),
        multi_select=question.get("multi_select"),
        intent=question.get("intent"),
    )


def _project_messages(conversation_id: str) -> list[MessageOut]:
    all_events = events.read_events(conversation_id)
    answered_request_ids = {
        e.payload.get("request_id") for e in all_events if e.type == "ask/answered"
    }

    out: list[MessageOut] = []
    for event in all_events:
        if event.type.endswith("/message"):
            out.append(
                MessageOut(
                    id=event.id,
                    conversation_id=event.conversation_id,
                    actor=event.actor,
                    text=event.payload.get("text", ""),
                    timestamp=event.timestamp,
                    event_type=event.type,
                    thread_id=event.payload.get("thread_id"),
                )
            )
        elif event.type == "ask/requested" and event.id not in answered_request_ids:
            questions = event.payload.get("questions") or []
            multiple = len(questions) > 1
            for index, question in enumerate(questions):
                out.append(
                    MessageOut(
                        id=f"{event.id}:{index}" if multiple else event.id,
                        conversation_id=event.conversation_id,
                        actor=event.actor,
                        text="",
                        timestamp=event.timestamp,
                        event_type=event.type,
                        thread_id=event.payload.get("thread_id"),
                        approval=_question_dict_to_out(question),
                    )
                )
    return out


def _append_user_message(conversation_id: str, text: str) -> MessageOut:
    event = events.append_event(
        conversation_id, "user/message", actor="you", payload={"text": text}
    )
    return MessageOut(
        id=event.id,
        conversation_id=event.conversation_id,
        actor=event.actor,
        text=text,
        timestamp=event.timestamp,
        event_type=event.type,
    )


def _highlight_snippet(text: str, query: str, radius: int = 40) -> str:
    """Mirrors `web/lib/search.ts`'s `highlightSnippet` so a server-side
    hit and the client-side fallback look the same to a user.
    """
    lower_text = text.lower()
    idx = lower_text.find(query.lower())
    if idx == -1:
        return text if len(text) <= 80 else f"{text[:80]}…"
    start = max(0, idx - radius)
    end = min(len(text), idx + len(query) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def _conversation_label(row: sqlite3.Row) -> str:
    """Matches `web/lib/search.ts`'s own fallback label logic exactly:
    a group's `name` (falling back to its id), otherwise the id.
    """
    if row["kind"] == "group" and row["name"]:
        return row["name"]
    return row["id"]


# ---------------------------------------------------------------------------
# WebSocket broadcast + turn-driving. See module docstring judgment call 2.
# ---------------------------------------------------------------------------


async def _broadcast(app: FastAPI, conversation_id: str, frame: dict) -> None:
    for queue in list(app.state.ws_subscribers.get(conversation_id, [])):
        queue.put_nowait(frame)


async def _broadcast_new_messages(app: FastAPI, conversation_id: str) -> None:
    seen: set[str] = app.state.broadcast_message_ids.setdefault(conversation_id, set())
    for message in _project_messages(conversation_id):
        if message.id in seen:
            continue
        seen.add(message.id)
        await _broadcast(
            app, conversation_id, {"type": "message", "payload": message.model_dump(by_alias=True)}
        )


async def _drive_turn(app: FastAPI, conversation_id: str, graph_input: Any) -> None:
    """Run one graph turn (or resume one) to completion or until it pauses
    at an approval interrupt, forwarding events out over this
    conversation's WS subscribers as they happen. Always launched as a
    fire-and-forget `asyncio.create_task` — never awaited by the HTTP
    handler that starts it.

    `stream_mode=["custom", "values"]` — verified against the installed
    `langgraph` package's own `pregel/main.py` (`astream`'s docstring:
    passing a list yields `(mode, data)` tuples; `"values"` "emit[s] all
    values in the state after each step, including interrupts"). "custom"
    frames are forwarded verbatim (graph/build.py's existing
    `streaming.emit` wiring); a "values" chunk carrying `"__interrupt__"`
    additionally gets an explicit `ask/requested`-typed frame out
    immediately. Either way, `_broadcast_new_messages` runs after every
    step and is what actually delivers a fresh assistant reply or approval
    prompt in the `{"type": "message", ...}` shape the frontend needs.
    """
    graph = app.state.graph
    config = {"configurable": {"thread_id": conversation_id}}
    try:
        async for mode, chunk in graph.astream(graph_input, config, stream_mode=["custom", "values"]):
            if mode == "custom":
                await _broadcast(app, conversation_id, chunk)
            elif mode == "values" and isinstance(chunk, dict) and "__interrupt__" in chunk:
                interrupts = chunk["__interrupt__"]
                if interrupts:
                    await _broadcast(
                        app,
                        conversation_id,
                        {"type": "ask/requested", "payload": interrupts[0].value},
                    )
            await _broadcast_new_messages(app, conversation_id)
    except Exception as exc:  # pragma: no cover - defensive: surface the
        # failure on the conversation's own WS stream rather than letting
        # it vanish into asyncio's default "exception was never retrieved"
        # log line with no link back to which conversation broke.
        await _broadcast(
            app, conversation_id, {"type": "turn/error", "payload": {"error": str(exc)}}
        )
        raise


_RESUME_POLL_INTERVAL_SECONDS = 0.1
_RESUME_POLL_TIMEOUT_SECONDS = 5.0


def _spawn_turn(app: FastAPI, conversation_id: str, graph_input: Any) -> None:
    """Fire-and-forget `_drive_turn`, tracked in `app.state.background_tasks`
    so `create_app`'s lifespan shutdown can cancel and await it instead of
    abandoning it mid-flight. Not just tidiness: an untracked task left
    running past its app's shutdown (e.g. a `TestClient`'s portal thread
    tearing down between tests) leaks the aiosqlite connection's background
    thread and whatever else that task was holding — across many short-lived
    test apps in one process, that's real accumulated thread/fd pressure
    with no corresponding cleanup. Tracking + cancel-and-await at shutdown
    is correct resource hygiene on its own terms, independent of whether
    any specific downstream test is sensitive to it.
    """
    task = asyncio.create_task(_drive_turn(app, conversation_id, graph_input))
    app.state.background_tasks.add(task)
    task.add_done_callback(app.state.background_tasks.discard)


async def _resume_with_answer(app: FastAPI, conversation_id: str, answer: AskAnswer) -> None:
    """Resume the paused turn with `answer`.

    A genuine race, not just a test artifact: `_drive_turn` broadcasts the
    approval prompt as soon as `persona_node` durably appends its
    `ask/requested` event (see `_broadcast_new_messages`), which happens
    one full graph step BEFORE `approval_node` actually calls `interrupt()`
    and the checkpointer persists the paused state. A fast client (or a
    fast local test) can react to that WS frame and call this function
    before `aget_state` would show any pending interrupt yet. Poll briefly
    for it to appear — the same bounded-wait-over-a-deadline idiom
    `core.ask.ask_user` already uses for its own event-log poll, just with
    a much shorter budget since this is milliseconds of in-process graph
    scheduling, not a human's response time.
    """
    config = {"configurable": {"thread_id": conversation_id}}
    deadline = asyncio.get_running_loop().time() + _RESUME_POLL_TIMEOUT_SECONDS
    snapshot = await app.state.graph.aget_state(config)
    while not snapshot.interrupts:
        if asyncio.get_running_loop().time() >= deadline:
            raise HTTPException(
                status_code=409,
                detail=f"conversation {conversation_id!r} has no pending approval",
            )
        await asyncio.sleep(_RESUME_POLL_INTERVAL_SECONDS)
        snapshot = await app.state.graph.aget_state(config)
    decision = {"selected": answer.selected, "custom": answer.custom}
    _spawn_turn(app, conversation_id, Command(resume=decision))


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


async def create_app() -> FastAPI:
    """Build and return the FastAPI app the Next.js frontend talks to.

    `async def` per the task's specified entry point. The real async
    startup work (building the graph — needs an open `aiosqlite`
    connection, see `graph/checkpointer.py`'s own docstring for why that
    forces async — plus syncing `graph.build.PERSONAS`) deliberately does
    NOT happen in this function's own body. It happens in the `lifespan`
    context manager below instead, so it always runs on whichever event
    loop actually SERVES the app (uvicorn's loop via `start()`, or a test
    client's own portal loop) rather than a temporary loop this factory
    might have been awaited from — `aiosqlite`'s connection is bound to
    the loop it was opened on.
    """

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        personas_dir = _personas_dir()
        _refresh_graph_personas(personas_dir)
        app.state.graph = await graph_build.build_graph()
        app.state.ws_subscribers = {}
        app.state.broadcast_message_ids = {}
        app.state.closed_orphan_conversations = set()
        app.state.background_tasks = set()
        try:
            yield
        finally:
            pending = list(app.state.background_tasks)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await app.state.graph.checkpointer.conn.close()

    app = FastAPI(title="Tapestry Web Adapter", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Personas -----------------------------------------------------

    @app.get("/api/personas", response_model=list[PersonaOut])
    async def list_personas() -> list[PersonaOut]:
        return [_persona_to_out(p) for p in _load_personas().values()]

    @app.post("/api/personas", response_model=PersonaOut, status_code=201)
    async def create_persona(draft: PersonaDraftIn) -> PersonaOut:
        directory = _personas_dir()
        existing = load_personas(directory)
        persona_id = _unique_persona_id(_slugify(draft.name), existing)
        persona = Persona(
            id=persona_id,
            name=draft.name,
            role=draft.role,
            model=draft.model,
            system_prompt=draft.system_prompt or "",
            tools=list(draft.tools or []),
            mcp_servers=list(draft.mcp or []),
            status=draft.status or "online",
            color=draft.color or "#6B7280",
        )
        save_persona(persona, directory)
        _refresh_graph_personas(directory)
        return _persona_to_out(persona)

    async def _update_persona(persona_id: str, draft: PersonaUpdateIn) -> PersonaOut:
        directory = _personas_dir()
        existing = load_personas(directory)
        current = existing.get(persona_id)
        if current is None:
            raise HTTPException(status_code=404, detail=f"persona {persona_id!r} not found")
        updates = {
            "name": draft.name,
            "role": draft.role,
            "model": draft.model,
            "status": draft.status,
            "color": draft.color,
            "system_prompt": draft.system_prompt,
            "tools": draft.tools,
            "mcp_servers": draft.mcp,
        }
        updated = current.model_copy(update={k: v for k, v in updates.items() if v is not None})
        save_persona(updated, directory)
        _refresh_graph_personas(directory)
        return _persona_to_out(updated)

    @app.patch("/api/personas/{persona_id}", response_model=PersonaOut)
    async def update_persona_patch(persona_id: str, draft: PersonaUpdateIn) -> PersonaOut:
        return await _update_persona(persona_id, draft)

    @app.put("/api/personas/{persona_id}", response_model=PersonaOut)
    async def update_persona_put(persona_id: str, draft: PersonaUpdateIn) -> PersonaOut:
        return await _update_persona(persona_id, draft)

    # -- Conversations --------------------------------------------------

    @app.get("/api/conversations", response_model=list[ConversationOut])
    async def list_conversations() -> list[ConversationOut]:
        return [_conversation_row_to_out(row) for row in _list_conversation_rows()]

    @app.post("/api/conversations", response_model=ConversationOut, status_code=201)
    async def create_conversation(draft: ConversationCreateIn) -> ConversationOut:
        if not draft.persona_ids:
            raise HTTPException(status_code=422, detail="personaIds must be non-empty")
        personas = _load_personas()
        unknown = [pid for pid in draft.persona_ids if pid not in personas]
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown persona ids: {unknown}")

        if draft.kind == "dm":
            if len(draft.persona_ids) != 1:
                raise HTTPException(
                    status_code=422, detail="a dm conversation must have exactly one persona"
                )
            conversation_id = f"dm-{draft.persona_ids[0]}"
        else:
            conversation_id = f"grp-{uuid.uuid4().hex[:12]}"

        # Idempotent: re-POSTing the same dm id (e.g. two tabs racing the
        # lazy-vivify path) returns the existing conversation rather than
        # erroring.
        row = _get_conversation_row(conversation_id)
        if row is None:
            row = _create_conversation(conversation_id, draft.kind, draft.name, draft.persona_ids)
        return _conversation_row_to_out(row)

    # -- Messages ---------------------------------------------------------

    @app.get(
        "/api/conversations/{conversation_id}/messages", response_model=list[MessageOut]
    )
    async def get_messages(conversation_id: str) -> list[MessageOut]:
        _ensure_conversation(conversation_id, app)
        return _project_messages(conversation_id)

    @app.post(
        "/api/conversations/{conversation_id}/messages",
        response_model=MessageOut,
        status_code=201,
    )
    async def send_message(conversation_id: str, body: SendMessageIn) -> MessageOut:
        _ensure_conversation(conversation_id, app)
        persona_ids, _, _ = _conversation_meta(conversation_id)
        if not persona_ids:
            raise HTTPException(
                status_code=422,
                detail=f"conversation {conversation_id!r} has no personas to respond",
            )
        message = _append_user_message(conversation_id, body.text)

        # Judgment call 3: the lead/entry persona for this turn.
        state = graph_build.new_state(conversation_id, persona_ids[0])
        _spawn_turn(app, conversation_id, state)
        return message

    # -- Ask / approvals ---------------------------------------------------

    @app.post("/api/conversations/{conversation_id}/ask/answers", status_code=204)
    async def answer_ask_batch(conversation_id: str, body: AnswerAskBatchIn) -> Response:
        _ensure_conversation(conversation_id, app)
        if not body.answers:
            raise HTTPException(status_code=422, detail="answers must be non-empty")
        first = body.answers[0]
        await _resume_with_answer(
            app,
            conversation_id,
            AskAnswer(id=first.id, selected=first.selected, custom=first.custom),
        )
        return Response(status_code=204)

    @app.post(
        "/api/conversations/{conversation_id}/ask/{question_id}/answer", status_code=204
    )
    async def answer_ask_single(
        conversation_id: str, question_id: str, body: AskAnswerBodyIn
    ) -> Response:
        _ensure_conversation(conversation_id, app)
        await _resume_with_answer(
            app,
            conversation_id,
            AskAnswer(id=question_id, selected=body.selected, custom=body.custom),
        )
        return Response(status_code=204)

    @app.get("/api/asks/pending", response_model=list[PendingApprovalOut])
    async def list_pending_asks() -> list[PendingApprovalOut]:
        out: list[PendingApprovalOut] = []
        for row in _list_conversation_rows():
            label = _conversation_label(row)
            for message in _project_messages(row["id"]):
                if message.approval is not None:
                    out.append(
                        PendingApprovalOut(
                            conversation_id=row["id"],
                            conversation_label=label,
                            question=message.approval,
                        )
                    )
        return out

    # -- Search -------------------------------------------------------------

    @app.get("/api/search", response_model=SearchResultsOut)
    async def search(q: str = "") -> SearchResultsOut:
        query = q.strip()
        if not query:
            return SearchResultsOut(messages=[], personas=[])
        lowered = query.lower()

        message_results: list[SearchMessageResultOut] = []
        for row in _list_conversation_rows():
            label = _conversation_label(row)
            for message in _project_messages(row["id"]):
                if message.text and lowered in message.text.lower():
                    message_results.append(
                        SearchMessageResultOut(
                            conversation_id=row["id"],
                            conversation_label=label,
                            actor=message.actor,
                            snippet=_highlight_snippet(message.text, query),
                        )
                    )

        persona_results = [
            SearchPersonaResultOut(persona=_persona_to_out(p))
            for p in _load_personas().values()
            if lowered in p.name.lower() or lowered in p.role.lower()
        ]

        return SearchResultsOut(messages=message_results, personas=persona_results)

    # -- Agents ---------------------------------------------------------------

    async def _pause_all_agents() -> None:
        directory = _personas_dir()
        for persona in load_personas(directory).values():
            if persona.status != "paused":
                save_persona(persona.model_copy(update={"status": "paused"}), directory)
        _refresh_graph_personas(directory)

    @app.post("/api/agents/pause-all", status_code=204)
    async def pause_all_agents() -> Response:
        await _pause_all_agents()
        return Response(status_code=204)

    @app.post("/api/agents/pause", status_code=204)
    async def pause_all_agents_alias() -> Response:
        await _pause_all_agents()
        return Response(status_code=204)

    # -- WebSocket --------------------------------------------------------

    @app.websocket("/ws/conversations/{conversation_id}")
    async def conversation_ws(websocket: WebSocket, conversation_id: str) -> None:
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue()
        app.state.ws_subscribers.setdefault(conversation_id, []).append(queue)
        try:
            while True:
                frame = await queue.get()
                await websocket.send_json(frame)
        except WebSocketDisconnect:
            pass
        finally:
            subscribers = app.state.ws_subscribers.get(conversation_id)
            if subscribers and queue in subscribers:
                subscribers.remove(queue)

    return app


async def start(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Serve the app as a sibling asyncio task.

    `uvicorn.Server.serve()` awaited directly — NOT `uvicorn.run()`, which
    builds and blocks on its own event loop and can't coexist with the
    Discord/Telegram adapters' own sibling tasks on one loop, per
    `project_structure.md`'s `main.py` note. Intended call site (once
    `main.py` exists): `asyncio.create_task(web_adapter.api.start(...))`
    alongside the other adapters' own start tasks.
    """
    app = await create_app()
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
