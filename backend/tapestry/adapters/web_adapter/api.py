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
   custom events over the WS" requirement. The `tool/status` frames are no
   longer merely forwarded-but-unused, either: `_record_running_activity`
   reads them to populate `GET /api/activity`'s "running" list, the one
   piece of activity state that genuinely cannot come from the durable
   event log.

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

6. **`Message.activity`/`.diff` are honest, not fabricated.** `_project_messages`
   emits a synthetic, empty-`text` `MessageOut` for every `tool/result`
   event (`activity`, always `done=True` — the log only ever records a
   tool call's FINAL result; "still running" is exactly what the live
   `tool/status` custom-stream frames are for, never history) and for
   every `task/diff_ready` event whose payload carries REAL captured
   counts (`payload["additions"] is not None`, i.e.
   `graph.diff_capture.capture_workspace_diff` actually succeeded).
   `task/diff_ready` events where capture failed (non-git workspace,
   `git` missing) are simply skipped — never surfaced as a message with
   fabricated 0/0 add/del counts, same principle as before, just applied
   at the per-event level instead of leaving the fields permanently
   `None`. See `_tool_result_message`/`_diff_ready_message` below.

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
    GET    /api/conversations/{conversation_id}/diff/{task_id}
    POST   /api/conversations/{conversation_id}/mode
    POST   /api/conversations/{conversation_id}/model
    POST   /api/conversations/{conversation_id}/stop
    POST   /api/conversations/{conversation_id}/ask/answers            (real, batch)
    POST   /api/conversations/{conversation_id}/ask/{question_id}/answer  (alias)
    GET    /api/search?q=...
    GET    /api/asks/pending
    GET    /api/activity
    GET    /api/status
    POST   /api/agents/pause-all           (POST /api/agents/pause also registered)
    POST   /api/agents/{persona_id}/resume
    WS     /ws/conversations/{conversation_id}
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
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
from tapestry.core.conversations import derive_conversation_context, derive_membership
from tapestry.core.personas import Mode as PersonaMode
from tapestry.core.personas import Persona, load_personas, save_persona
from tapestry.graph import build as graph_build
from tapestry.storage.db import get_connection
from tapestry.tools.mcp_client import MetaMCPClient, MetaMCPConfigurationError

__all__ = ["create_app", "start"]

logger = logging.getLogger("tapestry.web_adapter")

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
    # tapestry_modes_models_personas_spec.md §3 -- mirrors core.personas.
    # Persona's new optional fields one-for-one, camelCased on the wire.
    fallback_models: list[str] = Field(default_factory=list)
    guardian_model: str | None = None
    reasoning_effort: str | None = None
    default_mode: str = "manual"
    max_turns: int | None = None
    max_delegation_depth: int | None = None
    # web_adapter's proactive check-in loop -- see core.personas.Persona's
    # own field comment.
    proactive: bool = False


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
    fallback_models: list[str] | None = None
    guardian_model: str | None = None
    reasoning_effort: str | None = None
    # Literal-typed (not bare `str`), same reasoning as `status` above:
    # FastAPI must reject an invalid mode at request-parsing time (422),
    # not let it reach `_update_persona`'s `model_copy(update=...)` --
    # pydantic v2's `model_copy` does NOT re-validate, so a bad literal
    # there would get written straight into the persona's YAML and then
    # permanently break every subsequent `load_personas()` call.
    default_mode: PersonaMode | None = None
    max_turns: int | None = None
    max_delegation_depth: int | None = None
    proactive: bool | None = None


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
    fallback_models: list[str] | None = None
    guardian_model: str | None = None
    reasoning_effort: str | None = None
    # Literal-typed (not bare `str`), same reasoning as `status` above:
    # FastAPI must reject an invalid mode at request-parsing time (422),
    # not let it reach `_update_persona`'s `model_copy(update=...)` --
    # pydantic v2's `model_copy` does NOT re-validate, so a bad literal
    # there would get written straight into the persona's YAML and then
    # permanently break every subsequent `load_personas()` call.
    default_mode: PersonaMode | None = None
    max_turns: int | None = None
    max_delegation_depth: int | None = None
    proactive: bool | None = None


class ConversationOut(CamelModel):
    id: str
    kind: Literal["dm", "group"]
    name: str | None = None
    persona_ids: list[str]
    last_preview: str | None = None
    updated_at: str
    # The lead persona's (persona_ids[0]) current effective mode/model --
    # session/global scope only, per spec §1.6/§2.2. See
    # _conversation_row_to_out for how these are resolved.
    mode: str
    model: str
    # Human-set ground rules for this conversation, rendered into every
    # member's system prompt above their own persona instructions (see
    # graph/build.py's _conversation_context_guidance). None when never set.
    context: str | None = None


class ConversationCreateIn(CamelModel):
    kind: Literal["dm", "group"]
    name: str | None = None
    persona_ids: list[str] = Field(default_factory=list)
    context: str | None = None


class ConversationContextIn(CamelModel):
    """`POST /api/conversations/{id}/context` body -- sets or updates a
    conversation's ground rules. Same write for "set" and "edit": each call
    just appends another `conversation/context_set` event, and
    `derive_conversation_context` resolves to the most recent one.
    """

    context: str


class ModeChangeIn(CamelModel):
    """`POST /api/conversations/{id}/mode` body — matches `web/lib/api.ts`'s
    `setConversationMode`.
    """

    mode: str
    persona_id: str


class ModelSwitchIn(CamelModel):
    """`POST /api/conversations/{id}/model` body — matches `web/lib/api.ts`'s
    `setConversationModel`.
    """

    model: str
    persona_id: str
    scope: Literal["once", "session"]


class AskQuestionOut(CamelModel):
    id: str
    question: str
    detail: str | None = None
    options: list[str] | None = None
    multi_select: bool | None = None
    intent: str | None = None
    related_task_id: str | None = None


class MessageActivityOut(CamelModel):
    label: str
    done: bool
    result: str | None = None


class MessageDiffOut(CamelModel):
    task_id: str
    files: int
    add: int
    deletions: int = Field(alias="del")


# Tag-all fan-out scale controls (tapestry_mentions_concurrency_status_spec.md
# §2.5) -- config, not load-bearing constants; sensible defaults to build
# against, not a claim about what's correct for every deployment. Declared
# here (ahead of MessageOut/SendMessageIn below, which reference the
# threshold) rather than down with the rest of §2's helpers.
FANOUT_CONFIRM_THRESHOLD = 5
FANOUT_HARD_CAP = 50


class MessageOut(CamelModel):
    id: str
    conversation_id: str
    actor: str
    text: str
    timestamp: str
    event_type: str
    thread_id: str | None = None
    # Populated for synthetic tool/result and task/diff_ready entries only
    # — see module docstring judgment call 6 and _project_messages below.
    activity: MessageActivityOut | None = None
    diff: MessageDiffOut | None = None
    approval: AskQuestionOut | None = None
    # Populated only when this send resolved a multi-persona tag-all
    # mention (spec §2) — both None for an ordinary, unmentioned send, so
    # this is purely additive and doesn't change today's response shape.
    mentioned_persona_ids: list[str] | None = None
    skipped_persona_ids: list[str] | None = None


class SendMessageIn(CamelModel):
    text: str
    # Set by the client to actually spawn a fan-out whose resolved count
    # exceeded FANOUT_CONFIRM_THRESHOLD on a first, un-confirmed attempt
    # (spec §2.5) — see send_message. Defaulting to False keeps every
    # existing client (which never sends this field) exactly as
    # frictionless as before for the common, small-mention-count case.
    confirm_fan_out: bool = False


class FanOutConfirmationOut(CamelModel):
    """Returned instead of `MessageOut` (still 201, but nothing was sent
    yet) when a resolved mention count exceeds `FANOUT_CONFIRM_THRESHOLD` —
    spec §2.5. The client resends the identical request with
    `confirm_fan_out: true` to actually spawn it.
    """

    needs_confirmation: bool = True
    persona_ids: list[str]
    count: int
    threshold: int = FANOUT_CONFIRM_THRESHOLD


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


# --- Diff detail (GET .../diff/{task_id}) — full per-line content, distinct
# from MessageOut.diff's summary-only chip. Field names match
# web/lib/api.ts's DiffLine/DiffFile/DiffDetail exactly. ---


class DiffLineOut(CamelModel):
    type: str
    line_number: int
    content: str


class DiffFileOut(CamelModel):
    name: str
    lines: list[DiffLineOut]


class DiffDetailOut(CamelModel):
    task_id: str
    title: str
    file_count: int
    additions: int
    deletions: int
    files: list[DiffFileOut]


# --- Cross-conversation activity feed (GET /api/activity) ---


class ActivityItemOut(CamelModel):
    conversation_id: str
    conversation_label: str
    actor: str
    label: str
    timestamp: str
    task_id: str | None = None


class ActivityOut(CamelModel):
    running: list[ActivityItemOut]
    recent: list[ActivityItemOut]


# --- System status (GET /api/status) ---


class PlatformStatusOut(CamelModel):
    name: str
    detail: str
    connected: bool
    always_on: bool


class ProviderStatusOut(CamelModel):
    name: str
    connected: bool


class McpServerStatusOut(CamelModel):
    name: str
    connected: bool


class MetaMcpStatusOut(CamelModel):
    running: bool
    server_count: int


class StatusOut(CamelModel):
    platforms: list[PlatformStatusOut]
    providers: list[ProviderStatusOut]
    metamcp: MetaMcpStatusOut
    mcp_servers: list[McpServerStatusOut]


# ---------------------------------------------------------------------------
# Persona helpers (module-level, app-independent).
# ---------------------------------------------------------------------------


def _load_personas() -> dict[str, Persona]:
    return load_personas(_personas_dir())


def _derive_persona_status(
    persona: Persona, open_turns: dict[str, events.TapestryEvent]
) -> PersonaStatus:
    """Live status, computed at read time — never written except by an
    explicit human action (persona edit, "Pause all agents").

    Precedence: `status == "paused"` in the persona's own config always
    wins (`_pause_all_agents` writing that field is the deliberate,
    explicit human action it's designed to be — see
    `tapestry_mentions_concurrency_status_spec.md` §4). Otherwise
    `"busy"` if `events.list_open_turns()` shows this persona currently
    has a turn open (running, or paused at an approval) in any
    conversation. Otherwise the persona's own configured value, unchanged
    (in practice `"online"`/`"offline"` for every seed persona today).

    Deliberately never writes `"busy"` back to the persona's YAML at
    `turn/start` — a crash mid-turn would leave that written value stuck
    forever, and YAML round-tripping has already been observed in this
    project to lose hand-written comments on unrelated fields. Status is a
    projection, the same invariant this whole event-sourced core holds for
    messages and timelines.
    """
    if persona.status == "paused":
        return "paused"
    if persona.id in open_turns:
        return "busy"
    return persona.status


def _persona_to_out(
    persona: Persona, open_turns: dict[str, events.TapestryEvent] | None = None
) -> PersonaOut:
    """`open_turns`, when omitted, is computed fresh via a single
    `events.list_open_turns()` scan — fine for the single-persona call
    sites (create/update a persona). A caller rendering a whole roster
    (`list_personas`, the persona search results) MUST compute it once and
    pass it in instead — `list_open_turns` is a real, unbounded scan of
    every `turn/start`/`turn/end` row, and calling it once per persona
    would turn an N-persona roster render into N full scans.
    """
    if open_turns is None:
        open_turns = events.list_open_turns()
    return PersonaOut(
        id=persona.id,
        name=persona.name,
        role=persona.role,
        model=persona.model,
        status=_derive_persona_status(persona, open_turns),
        color=persona.color,
        system_prompt=persona.system_prompt,
        tools=list(persona.tools),
        mcp=list(persona.mcp_servers),
        fallback_models=list(persona.fallback_models),
        guardian_model=persona.guardian_model,
        reasoning_effort=persona.reasoning_effort,
        default_mode=persona.default_mode,
        max_turns=persona.max_turns,
        max_delegation_depth=persona.max_delegation_depth,
        proactive=persona.proactive,
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
    conversation_id: str,
    kind: str,
    name: str | None,
    persona_ids: list[str],
    context: str | None = None,
) -> sqlite3.Row:
    row = _insert_conversation_row(conversation_id, kind, name)
    events.append_event(
        conversation_id,
        "conversation/created",
        actor="system",
        payload={"kind": kind, "name": name, "persona_ids": list(persona_ids)},
    )
    if context:
        events.append_event(
            conversation_id, "conversation/context_set", actor="you", payload={"context": context}
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


def _conversation_mode_and_model(conversation_id: str, persona_ids: list[str]) -> tuple[str, str]:
    """The lead persona's (`persona_ids[0]`) current effective mode/model --
    judgment call 3's same "which persona is authoritative" convention,
    applied here to mode/model resolution. Defaults to `("manual", "")`
    when there is no lead persona to resolve against (an empty
    `persona_ids`, or a persona id no longer present in `_load_personas()`)
    rather than crashing -- an existing edge case this file already guards
    elsewhere (see `send_message`'s own empty-`persona_ids` 422).

    `resolve_model` is called with an EMPTY `state` dict deliberately: a
    "once" scope override lives only in live LangGraph checkpoint state
    (`model_override_once`), which this synchronous, non-graph helper has
    no access to -- and per spec §2.2/`Conversation.mode`'s own comment in
    `web/lib/api.ts`, a once-scope override is a one-shot value for the
    very next turn, not standing conversation state worth surfacing as
    "the" current model anyway. `resolve_model`'s own `state.get(
    "model_override_once")` on an empty dict returns `None` and falls
    through to session-scope (the `persona/model_switched` event log) or
    the persona's own global default -- exactly what's wanted here.
    """
    if not persona_ids:
        return "manual", ""
    lead = _load_personas().get(persona_ids[0])
    if lead is None:
        return "manual", ""
    mode = graph_build.resolve_mode(conversation_id, lead)
    model, _consumed_once = graph_build.resolve_model({}, conversation_id, lead)
    return mode, model


def _conversation_row_to_out(row: sqlite3.Row) -> ConversationOut:
    persona_ids, last_preview, last_timestamp = _conversation_meta(row["id"])
    mode, model = _conversation_mode_and_model(row["id"], persona_ids)
    return ConversationOut(
        id=row["id"],
        kind=row["kind"],
        name=row["name"],
        persona_ids=persona_ids,
        last_preview=last_preview,
        updated_at=last_timestamp or row["created_at"],
        mode=mode,
        model=model,
        context=derive_conversation_context(row["id"]),
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


# ---------------------------------------------------------------------------
# Tag-all / mention-routing (tapestry_mentions_concurrency_status_spec.md §2)
# ---------------------------------------------------------------------------

# Word-boundary token, e.g. "@all", "@rex" -- deliberately `\w+`, not
# something that also matches spaces: a persona whose display name has a
# space in it is only reliably taggable by its id (see spec §2.1's own
# stated limitation; verified against all four seed personas' single-word
# id/name pairs, so this covers today's roster exactly).
_MENTION_RE = re.compile(r"@(\w+)")


def _resolve_mentions(text: str, persona_ids: list[str], personas: dict[str, Persona]) -> list[str]:
    """Parse `@all` / `@<persona-id-or-name>` mentions in `text`, resolved
    ONLY against `persona_ids` -- this conversation's own members, never a
    persona outside it even if `@`-mentioned by name.

    Returns the ordered, de-duplicated list of persona ids to fan out to;
    empty if no mention was found at all (the caller falls back to
    `persona_ids[0]`, today's unchanged default -- spec §2.1).

    `@all` expands to every persona in `persona_ids`, in that order. An
    explicit `@<handle>` matches a member persona's id first, then its
    display name, case-insensitively. An unknown handle, or one naming a
    real persona who simply isn't in THIS conversation, is silently
    ignored -- a typo (or a stray `@` in ordinary prose) must never fail
    the whole send; see spec §2.1.
    """
    handles = _MENTION_RE.findall(text)
    if not handles:
        return []
    resolved: list[str] = []
    for handle in handles:
        lowered = handle.lower()
        if lowered == "all":
            for persona_id in persona_ids:
                if persona_id not in resolved:
                    resolved.append(persona_id)
            continue
        for persona_id in persona_ids:
            if persona_id in resolved:
                continue
            persona = personas.get(persona_id)
            name_matches = persona is not None and persona.name.lower() == lowered
            if persona_id.lower() == lowered or name_matches:
                resolved.append(persona_id)
                break
    return resolved


def _split_paused_mentions(
    persona_ids: list[str], personas: dict[str, Persona]
) -> tuple[list[str], list[str]]:
    """Split a resolved mention list into (active, skipped-because-paused).

    Spec §2.1's explicit resolution: a paused persona in the mention list
    is skipped, not a reason to reject the whole send -- `@all` in a
    10-persona group where 3 happen to be paused should still reach the
    other 7. Order is preserved within each list.
    """
    active: list[str] = []
    skipped: list[str] = []
    for persona_id in persona_ids:
        persona = personas.get(persona_id)
        if persona is not None and persona.status == "paused":
            skipped.append(persona_id)
        else:
            active.append(persona_id)
    return active, skipped


def _reject_if_persona_paused(persona_id: str) -> None:
    """Gate against starting a turn for a paused persona — see
    `tapestry_mentions_concurrency_status_spec.md` §4/§5 decision 2:
    `status == "paused"` must actually block a turn, not just display as
    paused. Before this, "Pause all agents" (and `nova.yaml`'s own
    deliberate `status: paused` default — her system prompt: "must be
    explicitly activated by a human before taking any action") was purely
    cosmetic; nothing anywhere read `persona.status` when deciding whether
    to run a turn (confirmed by grep across `graph/build.py` and this
    module before this fix existed).

    Checked against the persona's own real config value, not
    `_derive_persona_status`'s output — that also reports `"busy"` for a
    live turn, which is never itself a reason to refuse a NEW one; only an
    explicit `paused` flag is.
    """
    persona = _load_personas().get(persona_id)
    if persona is not None and persona.status == "paused":
        raise HTTPException(
            status_code=409,
            detail=(
                f"persona {persona_id!r} is paused -- "
                f"POST /api/agents/{persona_id}/resume before messaging it"
            ),
        )


def _reject_if_turn_in_progress(app: FastAPI, conversation_id: str) -> None:
    """Guard against the concurrency bug found while scoping tag-all (see
    `tapestry_mentions_concurrency_status_spec.md` §1): `graph_build.
    new_state()` + a fresh turn on a conversation that already has one
    running or paused at an approval `interrupt()` silently CLOBBERS the
    LangGraph checkpoint instead of erroring — proven directly against a
    real graph, not assumed.

    Two checks, covering two different gaps:
    - `app.state.turns_in_flight` (in-memory, this process only) — catches
      a turn between "about to spawn" and "`_drive_turn` returned," closing
      the narrow window a pure event-log read can't: two near-simultaneous
      sends (a double-click, a client retry) before the first turn's own
      `turn/start` event even lands in the log.
    - `find_open_turns` (`core/events.py`) — the same open-turn/close-turn
      scan `close_orphaned_turns` already uses for crash recovery, reading
      the durable log. A turn open here is NOT a crash artifact (orphans
      are already repaired by `_ensure_orphans_closed`, called just before
      this from every caller) — it's real in-flight-or-paused state, and
      it's what still catches a turn left open by a *previous* process
      (`turns_in_flight` is empty again after any restart).

    409, not silently dropping or queueing the new message: queueing is
    explicitly deferred (see the spec doc) as more work than a v1 needs —
    honest failure first, on the message that triggered it, not some later
    resume click.
    """
    if conversation_id in app.state.turns_in_flight:
        raise HTTPException(
            status_code=409,
            detail=(
                f"conversation {conversation_id!r} already has a turn in progress "
                "-- wait for it to finish before sending another message"
            ),
        )
    # Filtered to the MAIN thread only -- an open tag-all fan-out leg (see
    # §2.2) must never block a plain message or a new tag-all send; each
    # fan-out leg guards only itself (its thread_id is fresh/single-use per
    # send, so there's nothing to race there in the first place).
    open_turns = [
        e
        for e in events.find_open_turns(events.read_events(conversation_id)).values()
        if events.is_main_thread_turn(e, conversation_id)
    ]
    if not open_turns:
        return
    # Most recently opened, for the error message -- see find_open_turns'
    # own docstring: at most one main-thread turn is ever open anyway.
    turn = open_turns[-1]
    raise HTTPException(
        status_code=409,
        detail=(
            f"conversation {conversation_id!r} already has a turn in progress "
            f"(persona {turn.actor!r} is still running or waiting on your approval) "
            "-- wait for it to finish before sending another message"
        ),
    )


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
        related_task_id=question.get("related_task_id"),
    )


_ACTIVITY_RESULT_MAX_CHARS = 200


def _tool_result_message(event: events.TapestryEvent) -> MessageOut:
    """Project one `tool/result` event into a synthetic, empty-`text`
    message carrying `.activity`. Every `tool/result` event represents an
    already-finished call (the log never records "still running" — see
    module docstring judgment call 6), so `done` is always `True`.
    """
    payload = event.payload
    tool_name = payload.get("tool_name", "")
    arguments = payload.get("arguments") or {}
    detail = arguments.get("path") or arguments.get("command") or ""
    label = f"{tool_name} {detail}".strip() or tool_name
    if payload.get("is_error"):
        label = f"{label} (failed)"
    text = payload.get("text") or ""
    result = text[:_ACTIVITY_RESULT_MAX_CHARS] if text else None
    return MessageOut(
        id=event.id,
        conversation_id=event.conversation_id,
        actor=event.actor,
        text="",
        timestamp=event.timestamp,
        event_type=event.type,
        thread_id=payload.get("thread_id"),
        activity=MessageActivityOut(label=label, done=True, result=result),
    )


def _diff_ready_message(event: events.TapestryEvent) -> MessageOut | None:
    """Project one `task/diff_ready` event into a synthetic, empty-`text`
    message carrying `.diff`, or `None` when real capture failed
    (`payload["additions"] is None`) — never fabricate 0/0 counts, see
    module docstring judgment call 6.
    """
    payload = event.payload
    additions = payload.get("additions")
    if additions is None:
        return None
    files_changed = payload.get("files_changed") or []
    return MessageOut(
        id=event.id,
        conversation_id=event.conversation_id,
        actor=event.actor,
        text="",
        timestamp=event.timestamp,
        event_type=event.type,
        thread_id=payload.get("thread_id"),
        diff=MessageDiffOut(
            task_id=payload.get("task_id", ""),
            files=len(files_changed),
            add=additions,
            deletions=payload.get("deletions") or 0,
        ),
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
                    mentioned_persona_ids=event.payload.get("mentioned_persona_ids"),
                    skipped_persona_ids=event.payload.get("skipped_persona_ids"),
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
        elif event.type == "tool/result":
            out.append(_tool_result_message(event))
        elif event.type == "task/diff_ready":
            diff_message = _diff_ready_message(event)
            if diff_message is not None:
                out.append(diff_message)
    return out


def _append_user_message(
    conversation_id: str,
    text: str,
    mentioned_persona_ids: list[str] | None = None,
    skipped_persona_ids: list[str] | None = None,
) -> MessageOut:
    """`mentioned_persona_ids`/`skipped_persona_ids` are written straight
    into the event payload (not just set on the returned `MessageOut`) so
    a tag-all send's fan-out targets survive a page reload / WS reconnect
    -- `_project_messages` reads them back the same way it already does
    for `thread_id`. Found in review: the POST handler used to set these
    two fields on the object it returned but never persisted them, so
    they were only ever visible in that one immediate response and
    silently vanished from every subsequent `GET .../messages`.
    """
    payload: dict[str, Any] = {"text": text}
    if mentioned_persona_ids is not None:
        payload["mentioned_persona_ids"] = mentioned_persona_ids
    if skipped_persona_ids is not None:
        payload["skipped_persona_ids"] = skipped_persona_ids
    event = events.append_event(conversation_id, "user/message", actor="you", payload=payload)
    return MessageOut(
        id=event.id,
        conversation_id=event.conversation_id,
        actor=event.actor,
        text=text,
        timestamp=event.timestamp,
        event_type=event.type,
        mentioned_persona_ids=mentioned_persona_ids,
        skipped_persona_ids=skipped_persona_ids,
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
# Cross-conversation activity feed helpers (GET /api/activity's "recent").
# ---------------------------------------------------------------------------

# The event types read_recent_events is scoped to for the "recent" list --
# real, persisted task/delegation history only, never a raw firehose of
# every event type ever logged (model/response, ask/answered, etc. would be
# noise on an activity feed).
_RECENT_ACTIVITY_TYPES: set[str] = {
    "task/completed",
    "task/diff_ready",
    "delegation/sent",
    "task/verification_failed",
    "task/started",
}

_ACTIVITY_LABEL_TEMPLATES: dict[str, str] = {
    "task/completed": "{actor} completed a task",
    "task/diff_ready": "{actor} proposed a diff",
    "delegation/sent": "{actor} delegated",
    "task/verification_failed": "{actor}'s work failed verification",
    "task/started": "{actor} started a task",
}


def _activity_label(event_type: str, actor: str) -> str:
    template = _ACTIVITY_LABEL_TEMPLATES.get(event_type, "{actor} did something")
    return template.format(actor=actor)


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


def _record_running_activity(
    app: FastAPI, conversation_id: str, chunk: dict, graph_thread_id: str | None = None
) -> None:
    """Best-effort, in-memory-only tracking of in-flight tool calls, fed by
    `graph/build.py`'s `execute_node` `streaming.emit("tool/status", ...)`
    calls (`{"type": "tool/status", "payload": {"tool_name": ..., "status":
    "running"|"done", ...}}` — the "custom" stream frame shape). This is
    the ONE piece of activity state that genuinely cannot come from the
    durable event log: `tool/result` only ever records a call's FINAL
    result, never "still running".

    Keyed by `graph_thread_id` (defaulting to `conversation_id`, today's
    only case), NOT always `conversation_id` — caught in review: with tag-
    all's concurrent fan-out, several legs of one conversation can have a
    tool running at once. Keying by `conversation_id` alone means the
    second leg's "running" write clobbers the first's (last-writer-wins on
    one shared key), and whichever leg finishes first pops the row out
    from under every leg still running. Each dict value still carries its
    own `conversation_id` so `GET /api/activity` (which is conversation-
    scoped in its output shape) can still resolve a real conversation
    label from a key that's a fan-out thread id, not a conversation id.

    Never raises — this must not crash `_drive_turn`'s custom-frame
    handling over a chunk shape this function didn't anticipate; it's
    best-effort live state, not a durable guarantee.
    """
    thread_id = graph_thread_id or conversation_id
    try:
        if not isinstance(chunk, dict) or chunk.get("type") != "tool/status":
            return
        payload = chunk.get("payload") or {}
        status = payload.get("status")
        if status == "running":
            # No persona id travels with this frame (see streaming.emit's
            # payload above) -- fall back to the conversation's lead
            # persona, an acceptable simplification per the task brief.
            # (Still a simplification for a fan-out leg specifically: the
            # ACTUAL persona running the tool may not be persona_ids[0] --
            # the thread_id key at least stops legs from corrupting each
            # other's rows, which is the bug this fixes; attributing the
            # exact running persona per leg is a smaller, separate gap.)
            persona_ids, _, _ = _conversation_meta(conversation_id)
            actor = persona_ids[0] if persona_ids else "system"
            tool_name = payload.get("tool_name", "")
            app.state.running_activity[thread_id] = {
                "conversation_id": conversation_id,
                "actor": actor,
                "label": f"running {tool_name}".strip(),
                "timestamp": _now_iso(),
                "task_id": None,
            }
        elif status == "done":
            app.state.running_activity.pop(thread_id, None)
    except Exception:
        pass


async def _drive_turn(
    app: FastAPI,
    conversation_id: str,
    graph_input: Any,
    graph_thread_id: str | None = None,
    persona_id: str | None = None,
) -> None:
    """Run one graph turn (or resume one) to completion or until it pauses
    at an approval interrupt, forwarding events out over this
    conversation's WS subscribers as they happen. Always launched as a
    fire-and-forget `asyncio.create_task` — never awaited by the HTTP
    handler that starts it.

    `graph_thread_id` — the LangGraph checkpoint thread to actually run
    on. Defaults to `conversation_id` (today's only behavior, and still
    true for every ordinary, non-fan-out turn); a tag-all fan-out leg
    (spec §2.2) passes its own thread id instead, so its turn runs
    independently of the conversation's main thread and of every other
    concurrently-running leg.

    `stream_mode=["custom", "values"]` — verified against the installed
    `langgraph` package's own `pregel/main.py` (`astream`'s docstring:
    passing a list yields `(mode, data)` tuples; `"values"` "emit[s] all
    values in the state after each step, including interrupts"). "custom"
    frames are forwarded verbatim (graph/build.py's existing
    `streaming.emit` wiring); a "values" chunk carrying `"__interrupt__"`
    additionally gets an explicit `ask/requested`-typed frame out
    immediately. Either way, `_broadcast_new_messages` runs after every
    step and is what actually delivers a fresh assistant reply or approval
    prompt in the `{"type": "message", ...}` shape the frontend needs. The
    same custom frames also feed `_record_running_activity`, which the
    backend itself now keys off of (`GET /api/activity`'s "running" list) —
    no longer just proven-but-unused plumbing for a future frontend.
    """
    thread_id = graph_thread_id or conversation_id
    graph = app.state.graph
    config = {"configurable": {"thread_id": thread_id}}
    if persona_id is not None:
        # Typing indicator (UX ask, not in the original scope doc): a
        # human sees the composer freeze while a persona is still working
        # -- best-effort, in-memory-only, mirroring `_record_running_activity`'s
        # own "this can't come from the durable log" reasoning. Never
        # persisted: a page reload/reconnect simply shows nothing typing,
        # which is correct (nothing was mid-response at that instant from
        # the new connection's point of view).
        await _broadcast(
            app, conversation_id, {"type": "persona/typing", "payload": {"persona_id": persona_id}}
        )
    try:
        async for mode, chunk in graph.astream(graph_input, config, stream_mode=["custom", "values"]):
            if mode == "custom":
                await _broadcast(app, conversation_id, chunk)
                _record_running_activity(app, conversation_id, chunk, thread_id)
            elif mode == "values" and isinstance(chunk, dict) and "__interrupt__" in chunk:
                interrupts = chunk["__interrupt__"]
                if interrupts:
                    await _broadcast(
                        app,
                        conversation_id,
                        {"type": "ask/requested", "payload": interrupts[0].value},
                    )
            await _broadcast_new_messages(app, conversation_id)
    except asyncio.CancelledError:
        # A human stopped generation (POST .../stop cancels this exact
        # task via app.state.turn_tasks) -- NOT a crash, so this gets its
        # own reason string and its own WS frame type, both distinct from
        # the except Exception branch below. Still needs the same
        # self-healing close_turn_on_thread call: LangGraph's own
        # checkpointer may not have had a chance to durably persist
        # anything for this step, so without this the turn/start this
        # cancelled task itself appended would otherwise be left open
        # exactly like an unhandled crash would leave it.
        app.state.running_activity.pop(thread_id, None)
        events.close_turn_on_thread(
            conversation_id, thread_id, reason=events.STOPPED_BY_HUMAN_REASON
        )
        await _broadcast(app, conversation_id, {"type": "turn/stopped", "payload": {}})
        raise
    except Exception as exc:  # pragma: no cover - defensive: surface the
        # failure on the conversation's own WS stream rather than letting
        # it vanish into asyncio's default "exception was never retrieved"
        # log line with no link back to which conversation broke.
        # Also clear any "running" row THIS THREAD left behind (not just
        # `conversation_id` -- see `_record_running_activity`'s own
        # docstring: a fan-out leg's row is keyed by its own thread_id, and
        # popping by conversation_id alone would clear nothing for it,
        # leaving a stale "running" row behind) -- a tool_fn that raises
        # inside execute_node never reaches its own "done" streaming.emit,
        # and this is the only other place that turn's failure is
        # guaranteed to pass through.
        app.state.running_activity.pop(thread_id, None)
        # Durable, so a turn failure can actually be diagnosed after the
        # fact: the WS broadcast below only reaches a client connected at
        # this exact instant, and str(exc) in that payload is often just
        # the top-line message with no traceback. logger.exception here
        # captures the full traceback in the process log, keyed by
        # conversation/thread, regardless of who's watching.
        logger.exception(
            "turn failed on conversation=%s thread=%s", conversation_id, thread_id
        )
        # Found via real browser testing: a turn that raises here (a bad
        # provider API key, an outage, an unhandled tool error) otherwise
        # leaves its own `turn/start` open forever -- the actor stays
        # "busy" and every new message to this thread 409s from
        # `_reject_if_turn_in_progress`, with no repair short of a full
        # process restart. `close_turn_on_thread` appends the missing
        # `turn/end` right here, on the one thread that actually just
        # failed, so the conversation self-heals immediately instead of
        # staying durably wedged.
        events.close_turn_on_thread(conversation_id, thread_id)
        await _broadcast(
            app, conversation_id, {"type": "turn/error", "payload": {"error": str(exc)}}
        )
        raise
    finally:
        # See `_spawn_turn`'s docstring / `_reject_if_turn_in_progress`:
        # this THREAD (not necessarily the whole conversation -- a fan-out
        # leg's own thread_id, distinct from conversation_id) is no longer
        # "actively executing in this process" the moment this function
        # returns, whether that's a natural end, a pause at an approval
        # interrupt, or a raised exception -- all three are legitimate
        # reasons a NEW message/leg should be allowed to try again (the
        # interrupt case still gets caught by `find_open_turns`' durable
        # log check, which this in-memory set was never meant to replace).
        app.state.turns_in_flight.discard(thread_id)
        if persona_id is not None:
            # Always clear the typing indicator on every exit path,
            # including a pause at an approval interrupt -- a persona
            # waiting on a human is not "typing".
            await _broadcast(
                app,
                conversation_id,
                {"type": "persona/typing", "payload": {"persona_id": persona_id, "done": True}},
            )


_RESUME_POLL_INTERVAL_SECONDS = 0.1
_RESUME_POLL_TIMEOUT_SECONDS = 5.0


def _spawn_turn(
    app: FastAPI,
    conversation_id: str,
    graph_input: Any,
    graph_thread_id: str | None = None,
    persona_id: str | None = None,
) -> asyncio.Task:
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

    Also where the target thread is marked in `app.state.turns_in_flight`
    (see `_reject_if_turn_in_progress`) — every path that starts or resumes
    a turn (`send_message`, `_resume_with_answer`, the fan-out orchestrator)
    goes through here, so marking it once in this shared spot covers all
    of them rather than repeating it at each caller. Marked by
    `graph_thread_id` (defaulting to `conversation_id`, exactly today's
    only case), NOT always `conversation_id` — critical for fan-out: each
    leg has its own distinct thread id, so marking one leg in-flight must
    never make a DIFFERENT concurrently-spawned leg (or the main thread)
    look busy too. `_drive_turn` clears its own thread's entry in a
    `finally` on every exit path (natural end, paused at an interrupt, or
    a raised exception).

    Returns the created `asyncio.Task` — `_run_fanout_round` (see below)
    awaits it directly to sequence fan-out legs one at a time, since that's
    also exactly the signal it needs to know a leg is done before deciding
    whether to invite it back for another round. Ordinary callers
    (`send_message`'s plain path, `_resume_with_answer`) simply ignore the
    return value, same as before this became non-`None`.
    """
    thread_id = graph_thread_id or conversation_id
    app.state.turns_in_flight.add(thread_id)

    async def _run() -> None:
        await _drive_turn(app, conversation_id, graph_input, thread_id, persona_id)

    task = asyncio.create_task(_run())
    app.state.background_tasks.add(task)
    task.add_done_callback(app.state.background_tasks.discard)
    # Only registered if this thread doesn't already have one -- a fan-out
    # leg's thread_id is always fresh/single-use per call, but registering
    # unconditionally is still correct defense: whichever task is current
    # for this thread_id is the one a stop request should target.
    app.state.turn_tasks[thread_id] = task
    task.add_done_callback(lambda t: app.state.turn_tasks.pop(thread_id, None) if app.state.turn_tasks.get(thread_id) is t else None)
    return task


# A tag-all continuation session (round 1's mandatory replies, plus
# however many autonomous follow-up rounds happen on their own) never runs
# forever unattended — this is the same "10-round cap" already discussed
# and confirmed for this feature.
MAX_CONTINUATION_ROUNDS = 10

# Pause BETWEEN continuation rounds (never within round 1, which stays the
# immediate, mandatory reply to being tagged) — see `_breathing_pause`'s
# own docstring for why this exists and how it's distinct from `graph.
# build`'s own per-reply pacing delay.
_MIN_CONTINUATION_PAUSE_SECONDS = 3.0
_MAX_CONTINUATION_PAUSE_SECONDS = 8.0


async def _breathing_pause(seconds: float) -> None:
    """Isolated seam so tests can monkeypatch this to a no-op — same
    reasoning as `graph.build._breathing_pause`, just for the pause
    BETWEEN continuation rounds rather than before one reply lands. UX
    ask: without a real gap here, round 2 could start the instant round 1
    ends, leaving a human essentially no window to type a follow-up before
    the group is already back-and-forth with itself again.
    """
    await asyncio.sleep(seconds)


def _leg_outcome(conversation_id: str, graph_thread_id: str) -> str | None:
    """The final `turn/end` reason recorded for the turn that ran on
    `graph_thread_id`, or `None` if that turn hasn't closed yet (still
    paused on an approval interrupt) or never started. Every event a
    single logical turn appends — including a tool-call loop or a
    delegation hand-off — shares the SAME `turn_id`, so matching this
    thread's own `turn/start` id against `turn/end`'s `turn_id` field is
    exact regardless of how many internal steps happened in between.
    """
    turn_id = None
    for event in events.read_events(conversation_id):
        if turn_id is None:
            if event.type == "turn/start" and event.payload.get("graph_thread_id") == graph_thread_id:
                turn_id = event.id
        elif event.type == "turn/end" and event.payload.get("turn_id") == turn_id:
            return event.payload.get("reason")
    return None


def _latest_user_message_id(conversation_id: str) -> str | None:
    """The most recent `user/message` event's id — used to detect a
    genuinely NEW message from the human arriving mid-continuation-loop, so
    the autonomous rounds get out of the way instead of racing whatever
    now needs to respond to that new message.
    """
    latest = None
    for event in events.read_events(conversation_id):
        if event.type == "user/message":
            latest = event.id
    return latest


async def _run_fanout_round(
    app: FastAPI,
    conversation_id: str,
    persona_ids: list[str],
    trigger_message_id: str,
    round_num: int,
) -> list[str]:
    """Run one round's legs strictly one at a time, in `persona_ids` order
    — each leg is fully awaited before the next one is even spawned, so
    its `graph.build._chat_messages_from_log` read (rebuilt from the WHOLE
    conversation's event log on every new turn) actually includes every
    earlier leg's reply from this same round. This is the same ordering
    fix as before, just expressed as a plain sequential loop over
    `_spawn_turn`'s own returned task instead of a shared semaphore — one
    leg running at a time falls out for free from awaiting each task
    before starting the next, with no separate concurrency primitive
    needed.

    `round_num == 1` is always the mandatory, immediate reply to being
    tagged — every other round is an autonomous continuation
    (`is_continuation_round=True`), which is also the only round `pass_
    turn` is ever offered on.

    Returns the persona_ids that gave a genuine reply ("assistant_reply")
    this round — the only outcome eligible for another round. A pass, a
    pause on an approval interrupt, or a raised exception all drop a
    persona out of the autonomous continuation (a paused leg still
    resolves completely normally through the human's own answer via
    `_resume_with_answer`, entirely independent of this loop; an errored
    leg already self-heals its own turn via `_drive_turn`'s own `except
    Exception` branch — this just also declines to keep retrying it every
    round).
    """
    replied: list[str] = []
    is_continuation_round = round_num > 1
    for persona_id in persona_ids:
        suffix = "" if round_num == 1 else f"::r{round_num}"
        graph_thread_id = f"{conversation_id}::mention::{persona_id}::{trigger_message_id}{suffix}"
        state = graph_build.new_state(
            conversation_id, persona_id, is_continuation_round=is_continuation_round
        )
        task = _spawn_turn(app, conversation_id, state, graph_thread_id, persona_id)
        try:
            await task
        except asyncio.CancelledError:
            # A human's Stop — propagate all the way out so the WHOLE
            # session ends here, not just this one leg (see
            # `_run_continuation_session`).
            raise
        except Exception:
            # Already self-healed and surfaced (turn/error) by _drive_turn
            # itself; don't let one broken leg abort the rest of the round.
            continue
        if _leg_outcome(conversation_id, graph_thread_id) == "assistant_reply":
            replied.append(persona_id)
    return replied


async def _run_continuation_session(
    app: FastAPI, conversation_id: str, persona_ids: list[str], trigger_message_id: str
) -> None:
    """Round 1 is the mandatory, unchanged reply to every tagged persona.
    Once it finishes, the group keeps talking on its own: each further
    round only re-invites personas who actually replied last round (see
    `_run_fanout_round`), paced by `_breathing_pause` between rounds, until
    every remaining persona has dropped out, a genuinely new human message
    arrives (`_latest_user_message_id`), or `MAX_CONTINUATION_ROUNDS` is
    hit. A human's Stop cancels whichever leg is actively running; that
    propagates out of `_run_fanout_round` as `CancelledError` and ends the
    whole session right here — not just that one leg — and cancelling
    THIS task directly (see `stop_conversation`'s own `app.state.
    continuation_tasks` check) does the same even while sitting in the
    inter-round `_breathing_pause`, since asyncio propagates a task's own
    cancellation into whatever it's currently awaiting.
    """
    try:
        active = await _run_fanout_round(
            app, conversation_id, persona_ids, trigger_message_id, round_num=1
        )
    except asyncio.CancelledError:
        return

    baseline_message_id = _latest_user_message_id(conversation_id)
    round_num = 2
    while active and round_num <= MAX_CONTINUATION_ROUNDS:
        if _latest_user_message_id(conversation_id) != baseline_message_id:
            return
        await _breathing_pause(
            random.uniform(_MIN_CONTINUATION_PAUSE_SECONDS, _MAX_CONTINUATION_PAUSE_SECONDS)
        )
        if _latest_user_message_id(conversation_id) != baseline_message_id:
            return
        try:
            active = await _run_fanout_round(
                app, conversation_id, active, trigger_message_id, round_num=round_num
            )
        except asyncio.CancelledError:
            return
        round_num += 1


def _spawn_fanout_turns(
    app: FastAPI, conversation_id: str, persona_ids: list[str], trigger_message_id: str
) -> None:
    """Kick off a whole tag-all session — round 1's mandatory replies, plus
    however many autonomous continuation rounds follow — as one tracked
    background task. See `_run_continuation_session` for the actual loop.

    Tracked in both `app.state.background_tasks` (so lifespan shutdown can
    cancel and await it, same as every other background turn) and the
    conversation-keyed `app.state.continuation_tasks` (so `stop_
    conversation` can cancel the whole session directly, including while
    it's paused between rounds with no leg actively "in flight" for `find_
    open_turns` to catch).
    """
    task = asyncio.create_task(
        _run_continuation_session(app, conversation_id, persona_ids, trigger_message_id)
    )
    app.state.background_tasks.add(task)
    task.add_done_callback(app.state.background_tasks.discard)
    app.state.continuation_tasks[conversation_id] = task
    task.add_done_callback(
        lambda t: app.state.continuation_tasks.pop(conversation_id, None)
        if app.state.continuation_tasks.get(conversation_id) is t
        else None
    )


# ---------------------------------------------------------------------------
# Proactive check-in loop -- a `proactive` persona (core.personas.Persona)
# messaging a human FIRST in a DM, unprompted, once that DM has sat idle
# since the human's own last message. Dev-scale defaults (a background loop
# scanning every conversation each tick) match this project's current
# single-process/sqlite scale; not something to run unmodified against a
# large multi-tenant deployment.
# ---------------------------------------------------------------------------

_PROACTIVE_CHECK_INTERVAL_ENV_VAR = "TAPESTRY_PROACTIVE_CHECK_INTERVAL_SECONDS"
_PROACTIVE_IDLE_ENV_VAR = "TAPESTRY_PROACTIVE_IDLE_SECONDS"
# Small dev defaults so the loop is actually observable within one test
# session -- a real deployment should raise TAPESTRY_PROACTIVE_IDLE_SECONDS
# to something like a few hours; checking in every 60 idle-seconds reads as
# unhinged outside of testing the mechanism itself.
_DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS = 15.0
_DEFAULT_PROACTIVE_IDLE_SECONDS = 60.0


def _proactive_check_interval_seconds() -> float:
    return float(
        os.environ.get(_PROACTIVE_CHECK_INTERVAL_ENV_VAR, _DEFAULT_PROACTIVE_CHECK_INTERVAL_SECONDS)
    )


def _proactive_idle_seconds() -> float:
    return float(os.environ.get(_PROACTIVE_IDLE_ENV_VAR, _DEFAULT_PROACTIVE_IDLE_SECONDS))


def _seconds_since(timestamp_iso: str) -> float:
    then = datetime.fromisoformat(timestamp_iso)
    return (datetime.now(timezone.utc) - then).total_seconds()


def _proactive_checkin_targets(
    turns_in_flight: set[str], idle_threshold: float | None = None
) -> list[tuple[str, str]]:
    """The `(conversation_id, persona_id)` pairs eligible for a proactive
    nudge RIGHT NOW -- pure decision logic, deliberately split out from
    `_run_one_proactive_checkin_pass` so it's testable directly against
    real event-log data without needing a running graph, a checkpointer,
    or the app's own event loop at all (every other piece of fan-out/
    continuation logic in this module needs those; this one doesn't touch
    the graph until AFTER this function has already decided who to nudge).

    A conversation is eligible when ALL of:

    - it's a DM (`kind == "dm"`) -- proactive is scoped to DMs only; a
      persona's own `proactive` flag has no per-conversation opt-out yet
      (see `Persona.proactive`'s own comment), so extending this to groups
      would nudge every group she's ever added to, which is very likely
      not what "she checks in on me" means.
    - its one member persona is marked `proactive`.
    - it has at least one real message already. `_lazy_vivify_dm` creates
      a `conversations` ROW the instant a DM route is merely opened (see
      that function's own docstring) -- an empty DM nobody has ever
      actually messaged in is not a relationship to check in on, it's a
      route that happened to get touched once.
    - the LAST message-shaped event is NOT itself an unanswered proactive
      nudge from this persona (`payload["proactive"] is True` -- see
      `graph.build.persona_node`'s plain-reply branch, the only place that
      flag is ever set). A human message, OR an ORDINARY reply the persona
      gave reactively, are both fine -- only "she already reached out on
      her own and hasn't heard back yet" blocks a repeat nudge; without
      this exact distinction, a normal reply and a nudge look identical by
      actor alone, and the loop could never fire again after the very
      first exchange in a DM (every human message is always answered
      immediately, so "last actor is the human" almost never stays true
      long enough to cross the idle threshold).
    - that last message is older than `idle_threshold` (defaults to
      `_proactive_idle_seconds()` -- overridable so tests don't need a
      real wall-clock wait to prove the threshold is actually honored).
    - no turn is already in flight on this conversation's own thread
      (`turns_in_flight`) -- a DM's ordinary thread_id IS its
      conversation_id, so this check is exact, not approximate.
    """
    personas = _load_personas()
    proactive_persona_ids = {pid for pid, p in personas.items() if p.proactive}
    if not proactive_persona_ids:
        return []
    if idle_threshold is None:
        idle_threshold = _proactive_idle_seconds()
    targets: list[tuple[str, str]] = []
    for row in _list_conversation_rows():
        conversation_id = row["id"]
        if row["kind"] != "dm":
            continue
        if conversation_id in turns_in_flight:
            continue
        member_ids, _kind = derive_membership(conversation_id)
        if not member_ids or member_ids[0] not in proactive_persona_ids:
            continue
        persona_id = member_ids[0]
        last_message = None
        for event in events.read_events(conversation_id):
            if event.type.endswith("/message"):
                last_message = event
        if last_message is None:
            continue
        if last_message.actor == persona_id and last_message.payload.get("proactive"):
            continue
        if _seconds_since(last_message.timestamp) < idle_threshold:
            continue
        targets.append((conversation_id, persona_id))
    return targets


async def _run_one_proactive_checkin_pass(app: FastAPI) -> None:
    for conversation_id, persona_id in _proactive_checkin_targets(app.state.turns_in_flight):
        state = graph_build.new_state(conversation_id, persona_id, is_proactive_checkin=True)
        _spawn_turn(app, conversation_id, state, persona_id=persona_id)


async def _run_proactive_checkin_loop(app: FastAPI) -> None:
    """Fire-and-forget background loop, started once in `create_app`'s
    lifespan alongside every other `app.state.background_tasks` entry.

    Each pass is wrapped in its own `try/except` -- an unhandled exception
    inside a bare `while True` would otherwise kill the loop silently for
    the rest of the process's life, with nothing in the durable log or
    even stdout to say proactive check-ins quietly stopped happening.
    """
    while True:
        await asyncio.sleep(_proactive_check_interval_seconds())
        try:
            await _run_one_proactive_checkin_pass(app)
        except Exception:
            logger.exception("proactive check-in pass failed")


def _graph_thread_id_for_question(conversation_id: str, question_id: str) -> str:
    """Which LangGraph thread `question_id`'s `ask/requested` was raised
    on — `conversation_id` itself for an ordinary approval, or a tag-all
    fan-out leg's own thread (see spec §2.4). Looked up from the event log
    rather than required as a new client-facing field, so answering a
    question works exactly the same from the frontend's perspective
    whether it came from the main thread or a fan-out leg.

    Falls back to `conversation_id` when no matching `ask/requested` is
    found (unknown `question_id` — `_resume_with_answer`'s own poll loop
    is what actually reports that as a 409) or when one is found but
    predates `graph_thread_id` existing on this payload.
    """
    for event in events.read_events(conversation_id):
        if event.type != "ask/requested":
            continue
        questions = event.payload.get("questions") or []
        if any(q.get("id") == question_id for q in questions):
            return event.payload.get("graph_thread_id", conversation_id)
    return conversation_id


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
    graph_thread_id = _graph_thread_id_for_question(conversation_id, answer.id)
    config = {"configurable": {"thread_id": graph_thread_id}}
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
    # snapshot.values is the checkpointed TapestryGraphState -- persona_id
    # was set on it back when this turn's own new_state() first ran, and
    # a resume never changes who the turn belongs to, so it's still the
    # right persona to show as "typing" again.
    persona_id = snapshot.values.get("persona_id")
    _spawn_turn(app, conversation_id, Command(resume=decision), graph_thread_id, persona_id=persona_id)


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
        # In-memory-only, per-process registry of in-flight tool calls —
        # parallel in spirit to ws_subscribers/broadcast_message_ids above.
        # Never a second source of truth: the durable event log only ever
        # records a tool call's FINAL result (tool/result), never "still
        # running" — see _record_running_activity and GET /api/activity.
        app.state.running_activity = {}
        # In-memory, per-process guard against the concurrency bug in
        # `_reject_if_turn_in_progress`'s own docstring: `conversation_id`s
        # with a turn currently between "about to spawn" and "_drive_turn
        # has returned." Closes the narrow TOCTOU gap a pure event-log scan
        # can't -- two near-simultaneous sends (a double-click, a client
        # retry) racing the moment before the first turn's own `turn/start`
        # event is even committed. Added to synchronously (no `await`
        # between the check and the add — see `send_message`), so within
        # one process this is exact, not best-effort; the event-log scan
        # alongside it is what still catches a turn left open by a
        # *previous* process (this set is empty again after any restart).
        app.state.turns_in_flight = set()
        # UX ask, not in the original scope doc: lets POST .../stop find
        # and cancel the exact asyncio task running a given thread's turn.
        # Keyed by thread_id, same as turns_in_flight/running_activity --
        # a tag-all fan-out leg has its own entry, so stopping one leg
        # never touches a sibling leg's still-running task.
        app.state.turn_tasks = {}
        # Keyed by conversation_id (not thread_id): the whole tag-all
        # continuation session's own orchestrator task (see
        # `_spawn_fanout_turns`/`_run_continuation_session`), so `POST
        # .../stop` can cancel the entire autonomous-rounds loop directly —
        # including while it's sitting in the pause BETWEEN rounds, when no
        # leg has an open turn/start for `find_open_turns` to catch at all.
        app.state.continuation_tasks = {}
        # Proactive check-in loop (see its own docstring) -- one long-lived
        # background task for the whole process, not spawned per-request
        # like every other entry in `background_tasks`. Still tracked in
        # the same set so lifespan shutdown cancels it identically.
        proactive_task = asyncio.create_task(_run_proactive_checkin_loop(app))
        app.state.background_tasks.add(proactive_task)
        proactive_task.add_done_callback(app.state.background_tasks.discard)
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
        open_turns = events.list_open_turns()
        return [_persona_to_out(p, open_turns) for p in _load_personas().values()]

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
            fallback_models=list(draft.fallback_models or []),
            guardian_model=draft.guardian_model,
            reasoning_effort=draft.reasoning_effort,
            default_mode=draft.default_mode or "manual",
            max_turns=draft.max_turns,
            max_delegation_depth=draft.max_delegation_depth,
            proactive=draft.proactive or False,
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
            "fallback_models": draft.fallback_models,
            "guardian_model": draft.guardian_model,
            "reasoning_effort": draft.reasoning_effort,
            "default_mode": draft.default_mode,
            "max_turns": draft.max_turns,
            "max_delegation_depth": draft.max_delegation_depth,
            "proactive": draft.proactive,
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
            row = _create_conversation(
                conversation_id, draft.kind, draft.name, draft.persona_ids, draft.context
            )
        return _conversation_row_to_out(row)

    @app.post("/api/conversations/{conversation_id}/context", status_code=204)
    async def set_conversation_context(conversation_id: str, body: ConversationContextIn) -> Response:
        _ensure_conversation(conversation_id, app)
        events.append_event(
            conversation_id,
            "conversation/context_set",
            actor="you",
            payload={"context": body.context},
        )
        return Response(status_code=204)

    # -- Messages ---------------------------------------------------------

    @app.get(
        "/api/conversations/{conversation_id}/messages", response_model=list[MessageOut]
    )
    async def get_messages(conversation_id: str) -> list[MessageOut]:
        _ensure_conversation(conversation_id, app)
        return _project_messages(conversation_id)

    @app.post(
        "/api/conversations/{conversation_id}/messages",
        response_model=MessageOut | FanOutConfirmationOut,
        status_code=201,
    )
    async def send_message(
        conversation_id: str, body: SendMessageIn, response: Response
    ) -> MessageOut | FanOutConfirmationOut:
        _ensure_conversation(conversation_id, app)
        persona_ids, _, _ = _conversation_meta(conversation_id)
        if not persona_ids:
            raise HTTPException(
                status_code=422,
                detail=f"conversation {conversation_id!r} has no personas to respond",
            )

        personas = _load_personas()
        mentioned = _resolve_mentions(body.text, persona_ids, personas)

        if not mentioned:
            # Judgment call 3, unchanged: the lead/entry persona for this
            # turn, on the conversation's own main thread -- see spec §2.1,
            # "no mention at all -> today's behavior, unchanged."
            _reject_if_persona_paused(persona_ids[0])
            _reject_if_turn_in_progress(app, conversation_id)
            message = _append_user_message(conversation_id, body.text)
            state = graph_build.new_state(conversation_id, persona_ids[0])
            _spawn_turn(app, conversation_id, state, persona_id=persona_ids[0])
            return message

        # Tag-all fan-out path (spec §2). No `_reject_if_turn_in_progress`
        # check here -- every mentioned persona runs on its own fresh
        # fan-out thread (spec §2.2), never the main thread, so whatever
        # is or isn't happening on the main thread has no bearing on
        # whether this send can proceed.
        if len(mentioned) > FANOUT_HARD_CAP:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{len(mentioned)} personas tagged, over the {FANOUT_HARD_CAP} limit "
                    "-- tag a smaller group, or raise FANOUT_HARD_CAP for this deployment"
                ),
            )
        if len(mentioned) > FANOUT_CONFIRM_THRESHOLD and not body.confirm_fan_out:
            response.status_code = 202
            return FanOutConfirmationOut(persona_ids=mentioned, count=len(mentioned))

        active, skipped = _split_paused_mentions(mentioned, personas)
        if not active:
            # Caught in review: skip-and-report (spec §2.1) is right when
            # SOME mentioned personas are active -- @all in a 10-persona
            # group with 3 paused should still reach the other 7. It's
            # wrong here: every mentioned persona is paused, so silently
            # returning 201 with an empty active list records a message
            # NOBODY will ever answer, with the only signal buried in a
            # response field no client reads yet -- worse than the DM
            # path's loud 409 for the exact same underlying intent
            # ("message this paused persona"). Reject instead, same as
            # the DM path, rather than accepting a message that goes
            # nowhere.
            names = ", ".join(repr(p) for p in skipped)
            raise HTTPException(
                status_code=409,
                detail=f"every tagged persona is paused ({names}) -- resume at least one first",
            )
        message = _append_user_message(
            conversation_id,
            body.text,
            mentioned_persona_ids=active,
            skipped_persona_ids=skipped,
        )
        _spawn_fanout_turns(app, conversation_id, active, message.id)
        return message

    @app.get(
        "/api/conversations/{conversation_id}/diff/{task_id}", response_model=DiffDetailOut
    )
    async def get_diff_detail(conversation_id: str, task_id: str) -> DiffDetailOut:
        """Full per-file, per-line diff detail for the diff review screen —
        separate from `MessageOut.diff`'s summary-only chip. Takes the LAST
        (most recent) `task/diff_ready` event matching `task_id`; 404s if
        none exists, or if it exists but carries no real file detail
        (`files` empty — capture failed, nothing real to show; see
        module docstring judgment call 6).
        """
        match: events.TapestryEvent | None = None
        for event in events.read_events(conversation_id):
            if event.type == "task/diff_ready" and event.payload.get("task_id") == task_id:
                match = event
        if match is None or not (match.payload.get("files") or []):
            raise HTTPException(
                status_code=404, detail=f"no diff found for task {task_id!r}"
            )

        files = [
            DiffFileOut(
                name=file.get("name", ""),
                lines=[
                    DiffLineOut(
                        type=line.get("type", "ctx"),
                        line_number=line.get("line_number", 0),
                        content=line.get("content", ""),
                    )
                    for line in (file.get("lines") or [])
                ],
            )
            for file in match.payload["files"]
        ]
        title = f"Diff · {files[0].name}" if files else f"Diff · {task_id[:8]}"
        return DiffDetailOut(
            task_id=task_id,
            title=title,
            file_count=len(files),
            additions=match.payload.get("additions") or 0,
            deletions=match.payload.get("deletions") or 0,
            files=files,
        )

    # -- Modes & models (tapestry_modes_models_personas_spec.md §1.6/§2.2) --

    def _require_conversation_persona(conversation_id: str, persona_id: str) -> None:
        """422s unless `persona_id` is actually one of this conversation's
        participants -- same "reject a request-body id the conversation
        doesn't recognize" shape as `create_conversation`'s own "unknown
        persona ids" 422, applied here to mode/model switch requests.
        """
        persona_ids, _, _ = _conversation_meta(conversation_id)
        if persona_id not in persona_ids:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"persona {persona_id!r} is not part of conversation "
                    f"{conversation_id!r}"
                ),
            )

    @app.post("/api/conversations/{conversation_id}/mode", status_code=204)
    async def set_conversation_mode(conversation_id: str, body: ModeChangeIn) -> Response:
        _ensure_conversation(conversation_id, app)
        if body.mode not in graph_build.VALID_MODES:
            raise HTTPException(
                status_code=422,
                detail=f"invalid mode {body.mode!r}; must be one of {sorted(graph_build.VALID_MODES)}",
            )
        _require_conversation_persona(conversation_id, body.persona_id)
        events.append_event(
            conversation_id,
            "mode/changed",
            actor="you",
            payload={"mode": body.mode, "persona_id": body.persona_id},
        )
        return Response(status_code=204)

    @app.post("/api/conversations/{conversation_id}/model", status_code=204)
    async def set_conversation_model(conversation_id: str, body: ModelSwitchIn) -> Response:
        _ensure_conversation(conversation_id, app)
        _require_conversation_persona(conversation_id, body.persona_id)
        if body.scope == "session":
            events.append_event(
                conversation_id,
                "persona/model_switched",
                actor="you",
                payload={"model": body.model, "persona_id": body.persona_id},
            )
        else:
            # "once" scope: lives only in live LangGraph checkpoint state,
            # not the durable event log -- persona_node reads it back via
            # graph_build.resolve_model and clears it after exactly one
            # consuming pass (see that module's own TapestryGraphState.
            # model_override_once comment). Same config shape
            # _drive_turn/_resume_with_answer already use elsewhere in this
            # file.
            config = {"configurable": {"thread_id": conversation_id}}
            await app.state.graph.aupdate_state(config, {"model_override_once": body.model})
        return Response(status_code=204)

    @app.post("/api/conversations/{conversation_id}/stop", status_code=204)
    async def stop_conversation(conversation_id: str) -> Response:
        """UX ask, not in the original scope doc: cancel every turn (the
        main thread, and every tag-all fan-out leg) currently running for
        this conversation, right now, rather than waiting for a natural
        stopping point. Also cancels a whole autonomous continuation
        session (round-continuation, see `_run_continuation_session`) in
        one shot, including while it's paused BETWEEN rounds.

        Finds each open `turn/start`'s `graph_thread_id` from the durable
        log, looks up that thread's live asyncio task in
        `app.state.turn_tasks` (registered by `_spawn_turn`), and calls
        `task.cancel()` on it directly -- `_drive_turn`'s own `except
        asyncio.CancelledError` branch is what actually appends the
        matching `turn/end` (`reason=STOPPED_BY_HUMAN_REASON`) and
        broadcasts `turn/stopped`, so this handler itself does no event-log
        writing; it only triggers cancellation and waits for that to land.

        A thread with no live task (already finished, or paused at an
        approval interrupt with nothing actively running) is silently
        skipped -- "stop the thing that's currently working" has nothing
        to do to it, and that's not an error.

        Separately, `app.state.continuation_tasks` is checked for THIS
        conversation's own continuation-session task (if any) and
        cancelled directly, regardless of whether any leg currently has an
        open turn -- during the pause between rounds nothing does, so
        relying on `find_open_turns` alone would leave that loop running
        and about to start another round right after this call returns.
        Cancelling the orchestrator task itself is enough either way:
        asyncio propagates a task's cancellation into whatever it's
        currently awaiting, so this also reaches an actively-running leg
        exactly like the loop above does.
        """
        _ensure_conversation(conversation_id, app)
        open_starts = events.find_open_turns(events.read_events(conversation_id))
        for start_event in open_starts.values():
            thread_id = start_event.payload.get("graph_thread_id", conversation_id)
            task = app.state.turn_tasks.get(thread_id)
            if task is not None and not task.done():
                task.cancel()
        continuation_task = app.state.continuation_tasks.get(conversation_id)
        if continuation_task is not None and not continuation_task.done():
            continuation_task.cancel()
        return Response(status_code=204)

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

        search_open_turns = events.list_open_turns()
        persona_results = [
            SearchPersonaResultOut(persona=_persona_to_out(p, search_open_turns))
            for p in _load_personas().values()
            if lowered in p.name.lower() or lowered in p.role.lower()
        ]

        return SearchResultsOut(messages=message_results, personas=persona_results)

    # -- Activity feed (Activity screen's "Running now" / "Recent") --------

    @app.get("/api/activity", response_model=ActivityOut)
    async def get_activity() -> ActivityOut:
        recent: list[ActivityItemOut] = []
        for event in events.read_recent_events(types=_RECENT_ACTIVITY_TYPES, limit=20):
            row = _get_conversation_row(event.conversation_id)
            label = _conversation_label(row) if row is not None else event.conversation_id
            recent.append(
                ActivityItemOut(
                    conversation_id=event.conversation_id,
                    conversation_label=label,
                    actor=event.actor,
                    label=_activity_label(event.type, event.actor),
                    timestamp=event.timestamp,
                    task_id=event.payload.get("task_id"),
                )
            )

        running: list[ActivityItemOut] = []
        for thread_id, info in app.state.running_activity.items():
            # The dict key is a thread id (== conversation_id for an
            # ordinary turn, a fan-out leg's own thread otherwise) -- the
            # real conversation_id to resolve a label from is stored in
            # the value itself (see _record_running_activity), never
            # assumed to equal the key.
            conversation_id = info.get("conversation_id", thread_id)
            row = _get_conversation_row(conversation_id)
            label = _conversation_label(row) if row is not None else conversation_id
            running.append(
                ActivityItemOut(
                    conversation_id=conversation_id,
                    conversation_label=label,
                    actor=info.get("actor", ""),
                    label=info.get("label", ""),
                    timestamp=info.get("timestamp", ""),
                    task_id=info.get("task_id"),
                )
            )

        return ActivityOut(running=running, recent=recent)

    # -- System status (Settings screen's Platforms/Providers/Tools panels) --

    @app.get("/api/status", response_model=StatusOut)
    async def get_status() -> StatusOut:
        discord_connected = bool(os.environ.get("DISCORD_BOT_TOKEN"))
        telegram_connected = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
        platforms = [
            PlatformStatusOut(
                name="Discord",
                detail="Connected" if discord_connected else "Not connected",
                connected=discord_connected,
                always_on=False,
            ),
            PlatformStatusOut(
                name="Telegram",
                detail="Connected" if telegram_connected else "Not connected",
                connected=telegram_connected,
                always_on=False,
            ),
            PlatformStatusOut(
                name="Web", detail="Always on", connected=True, always_on=True
            ),
        ]
        providers = [
            ProviderStatusOut(
                name="Anthropic", connected=bool(os.environ.get("ANTHROPIC_API_KEY"))
            ),
            ProviderStatusOut(
                name="DeepSeek", connected=bool(os.environ.get("DEEPSEEK_API_KEY"))
            ),
            ProviderStatusOut(
                name="Gemini", connected=bool(os.environ.get("GEMINI_API_KEY"))
            ),
            # No env var exists for Qwen in this project -- always
            # disconnected. Expected, not a bug.
            ProviderStatusOut(name="Qwen", connected=False),
            ProviderStatusOut(
                name="OpenRouter", connected=bool(os.environ.get("OPENROUTER_API_KEY"))
            ),
        ]

        try:
            tools = await MetaMCPClient().list_tools()
        except (MetaMCPConfigurationError, Exception):
            # MetaMCPConfigurationError (no METAMCP_API_KEY configured) is
            # already covered by the bare Exception below it -- listed
            # explicitly anyway so the "no configuration" case reads as a
            # named, expected outcome rather than an incidental catch-all.
            # This endpoint must never 500 over metamcp being unavailable.
            metamcp = MetaMcpStatusOut(running=False, server_count=0)
            mcp_servers: list[McpServerStatusOut] = []
        else:
            # metamcp's own convention: {ServerName}__{originalToolName} --
            # see tools/mcp_client.py's module docstring.
            server_names = sorted({tool.get("name", "").split("__", 1)[0] for tool in tools})
            metamcp = MetaMcpStatusOut(running=True, server_count=len(server_names))
            # Every server returned here IS connected -- list_tools() only
            # ever returns tools from servers metamcp is actually reachable
            # through.
            mcp_servers = [McpServerStatusOut(name=name, connected=True) for name in server_names]

        return StatusOut(
            platforms=platforms, providers=providers, metamcp=metamcp, mcp_servers=mcp_servers
        )

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

    @app.post("/api/agents/{persona_id}/resume", status_code=204)
    async def resume_agent(persona_id: str) -> Response:
        """The other half of `_reject_if_persona_paused` shipping at all —
        see `tapestry_mentions_concurrency_status_spec.md` §4/§5 decision
        2. Deliberately per-persona, not a blanket `resume-all`: a blanket
        resume was the first idea here too, and was dropped on review --
        `nova.yaml` ships `status: paused` deliberately (her own
        system_prompt: "must be explicitly activated by a human before
        taking any action"), and a human hitting pause-all to mean "stop
        my chat agents for a minute" then resume-all to undo it would
        silently reactivate Nova too, exactly the standing authorization
        her design forbids. This endpoint is what actually makes her
        design usable: a human explicitly activating *her*, specifically.
        """
        directory = _personas_dir()
        personas = load_personas(directory)
        persona = personas.get(persona_id)
        if persona is None:
            raise HTTPException(status_code=404, detail=f"persona {persona_id!r} not found")
        if persona.status == "paused":
            save_persona(persona.model_copy(update={"status": "online"}), directory)
            _refresh_graph_personas(directory)
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
