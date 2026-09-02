"""Conversation history, projected FROM the event log — never stored twice.

`derive_messages` is the ONLY way conversation history is ever assembled
**as input to a model** (`graph/build.py`'s `persona_node` reads it to
rebuild message-list context for a turn). Nothing in this codebase should
keep a separate "messages" table or list — every message a human or
persona ever sees is recomputed from `events.read_events` on demand.

`derive_timeline`, below, is a *deliberately separate* wider projection for
the UI, not a replacement for `derive_messages`. The event log carries far
more than chat turns — tool results, diffs, task lifecycle, delegation —
and a human watching a conversation benefits from seeing all of it, but a
model rebuilding its own context should not: widening `derive_messages`
itself would silently leak tool-result/task-lifecycle noise into every
persona's own message history. Two readers, two projections, one source of
truth underneath.
"""

from __future__ import annotations

from pydantic import BaseModel

from tapestry.core import events


class Message(BaseModel):
    id: str
    conversation_id: str
    actor: str
    text: str
    timestamp: str
    event_type: str
    # ADDITIVE, non-breaking schema extension (added for graph/build.py):
    # the frontend already has a thread UI
    # (app/conversation/[id]/thread/[threadId]/) but nothing before this
    # populated a thread id anywhere. None for every message that isn't
    # part of a spun-off thread — existing callers that never look at this
    # field are unaffected. `derive_messages` below projects it straight
    # from the underlying event's payload; filtering messages BY thread is
    # deliberately left for whoever builds that screen's data layer next —
    # this only guarantees the data is actually there to filter on.
    thread_id: str | None = None


def derive_messages(conversation_id: str) -> list[Message]:
    """Project message-shaped events into `Message` objects, in log order.

    An event is message-shaped when its `type` is exactly `"user/message"`
    or `"assistant/message"`, or more generally ends with `"/message"` (so a
    future `"delegation/message"`-style type, if one is ever introduced,
    projects automatically without a change here). The event's display text
    is read from `payload["text"]`; an event missing that key projects as
    an empty string rather than raising, since a malformed historical event
    shouldn't take down the whole conversation view.
    """
    all_events = events.read_events(conversation_id)
    messages: list[Message] = []
    for event in all_events:
        if not event.type.endswith("/message"):
            continue
        messages.append(
            Message(
                id=event.id,
                conversation_id=event.conversation_id,
                actor=event.actor,
                text=event.payload.get("text", ""),
                timestamp=event.timestamp,
                event_type=event.type,
                thread_id=event.payload.get("thread_id"),
            )
        )
    return messages


# Pure internal bookkeeping, excluded from the timeline: `model/response`
# (cost/token accounting, not display content — an explicit user decision,
# not a default), `turn/start`/`turn/end` (graph-loop bracketing, no
# human-facing content of their own), `conversation/created` (conversation
# metadata already reflected in the `Conversation` object itself, not an
# event that happened *during* the conversation).
_TIMELINE_EXCLUDED_TYPES = frozenset(
    {"model/response", "turn/start", "turn/end", "conversation/created"}
)


class TimelineItem(BaseModel):
    id: str
    conversation_id: str
    actor: str
    timestamp: str
    type: str
    payload: dict
    thread_id: str | None = None


def derive_membership(conversation_id: str) -> tuple[list[str], str | None]:
    """(persona_ids, kind) for this conversation, projected from its
    `conversation/created` event -- the same data `web_adapter/api.py`'s
    private `_conversation_meta` already extracts for the API layer, made
    reusable here so `graph/build.py` (which must not import from
    `adapters/`) can look up conversation membership too. `kind` is
    `"dm"`/`"group"` when found, else `None` (e.g. a conversation the log
    has no creation event for -- shouldn't happen, but a caller should
    treat that as "unknown membership" rather than crash).
    """
    for event in events.read_events(conversation_id):
        if event.type == "conversation/created":
            return list(event.payload.get("persona_ids", [])), event.payload.get("kind")
    return [], None


def derive_timeline(conversation_id: str) -> list[TimelineItem]:
    """Project every human-displayable event into a `TimelineItem`, in log
    order — the deliberately WIDE sibling to `derive_messages` (see module
    docstring for why these are two separate projections rather than one
    widened function).

    Unlike `derive_messages`, nothing is dropped here except the handful of
    event types in `_TIMELINE_EXCLUDED_TYPES` — a `tool/result`,
    `task/diff_ready`, `task/started`, `task/completed`,
    `task/verification_failed`, `delegation/sent`, `ask/requested`, or
    `ask/answered` event all project through, alongside `user/message` and
    `assistant/message`. `payload` is passed through unflattened: a caller
    needing type-specific fields (`task/diff_ready`'s
    `files`/`additions`/`deletions`, `tool/result`'s
    `tool_name`/`arguments`/`is_error`, ...) reads them directly rather
    than this module guessing which subset any given caller wants.
    """
    all_events = events.read_events(conversation_id)
    items: list[TimelineItem] = []
    for event in all_events:
        if event.type in _TIMELINE_EXCLUDED_TYPES:
            continue
        items.append(
            TimelineItem(
                id=event.id,
                conversation_id=event.conversation_id,
                actor=event.actor,
                timestamp=event.timestamp,
                type=event.type,
                payload=event.payload,
                thread_id=event.payload.get("thread_id"),
            )
        )
    return items
