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


class Reaction(BaseModel):
    emoji: str
    actor: str


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
    # ADDITIVE: WhatsApp-style quote-reply -- the id of the message this
    # one is replying to, or None for an ordinary message. Deliberately a
    # SEPARATE concept from thread_id above (that's the abandoned spun-off
    # thread-pane scaffold; this is one message pointing at one earlier
    # message, inline in the same stream, never its own sub-thread).
    reply_to_id: str | None = None
    # ADDITIVE: True once at least one message/edited event has targeted
    # this message. The projected `text` is always the LATEST edit's text
    # (or the original, if never edited) -- history isn't erased from the
    # log, just not surfaced as separate versions here.
    edited: bool = False
    # ADDITIVE: True once a message/deleted event has targeted this
    # message. `text` is left as the original/last-edited text on the
    # Message object itself (nothing is destroyed in an event-sourced
    # log) -- callers that must not display a deleted message's content
    # (the API layer, going out over the wire) are responsible for
    # redacting `text` themselves when `deleted` is True; core stays a
    # pure, non-lossy projection.
    deleted: bool = False
    # ADDITIVE: current reactions on this message, net of every
    # reaction/added and reaction/removed event that targeted it, applied
    # in log order via core.reactions' toggle convention.
    reactions: list[Reaction] = []


def derive_messages(conversation_id: str) -> list[Message]:
    """Project message-shaped events into `Message` objects, in log order.

    An event is message-shaped when its `type` is exactly `"user/message"`
    or `"assistant/message"`, or more generally ends with `"/message"` (so a
    future `"delegation/message"`-style type, if one is ever introduced,
    projects automatically without a change here). The event's display text
    is read from `payload["text"]`; an event missing that key projects as
    an empty string rather than raising, since a malformed historical event
    shouldn't take down the whole conversation view.

    A second pass over the same already-fetched event list then applies
    message/edited, message/deleted, and reaction/added|removed events by
    looking their target message up in `by_id` -- these three event types
    never themselves satisfy the `.endswith("/message")` check above, so
    they're invisible to a persona's own history (`graph/build.py`'s
    `_chat_messages_from_log`) while still updating the human-facing
    projection here.
    """
    all_events = events.read_events(conversation_id)
    messages: list[Message] = []
    index_by_id: dict[str, int] = {}
    for event in all_events:
        if not event.type.endswith("/message"):
            continue
        message = Message(
            id=event.id,
            conversation_id=event.conversation_id,
            actor=event.actor,
            text=event.payload.get("text", ""),
            timestamp=event.timestamp,
            event_type=event.type,
            thread_id=event.payload.get("thread_id"),
            reply_to_id=event.payload.get("reply_to_id"),
        )
        index_by_id[message.id] = len(messages)
        messages.append(message)

    reactions_by_message: dict[str, dict[tuple[str, str], bool]] = {}
    for event in all_events:
        target_id = event.payload.get("message_id")
        index = index_by_id.get(target_id) if target_id else None
        if index is None:
            continue
        if event.type == "message/edited":
            messages[index] = messages[index].model_copy(
                update={"text": event.payload.get("text", messages[index].text), "edited": True}
            )
        elif event.type == "message/deleted":
            messages[index] = messages[index].model_copy(update={"deleted": True})
        elif event.type in ("reaction/added", "reaction/removed"):
            active = reactions_by_message.setdefault(target_id, {})
            active[(event.actor, event.payload.get("emoji", ""))] = event.type == "reaction/added"

    for target_id, active in reactions_by_message.items():
        index = index_by_id.get(target_id)
        if index is None:
            continue
        reactions = [Reaction(actor=actor, emoji=emoji) for (actor, emoji), on in active.items() if on]
        messages[index] = messages[index].model_copy(update={"reactions": reactions})

    return messages


# Pure internal bookkeeping, excluded from the timeline: `model/response`
# (cost/token accounting, not display content — an explicit user decision,
# not a default), `turn/start`/`turn/end` (graph-loop bracketing, no
# human-facing content of their own), `conversation/created` and
# `conversation/context_set` (conversation metadata already reflected in the
# `Conversation` object itself -- see `ConversationOut.context` -- not an
# event that happened *during* the conversation).
_TIMELINE_EXCLUDED_TYPES = frozenset(
    {"model/response", "turn/start", "turn/end", "conversation/created", "conversation/context_set"}
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


def derive_conversation_context(conversation_id: str) -> str | None:
    """Human-set ground rules/context for this conversation, or `None` if
    never set. Projected the same way `resolve_mode` (`graph/build.py`)
    resolves a persona's mode -- reverse-scan the log for the most recent
    `conversation/context_set` event -- so "set" and "edit" are the same
    write (append a new event) and there is nothing to migrate.
    """
    for event in reversed(events.read_events(conversation_id)):
        if event.type == "conversation/context_set":
            return event.payload.get("context") or None
    return None


def derive_conversation_archived(conversation_id: str) -> bool:
    """Whether this conversation is currently archived -- reverse-scan for
    the most recent `conversation/archive_changed` event, same toggle
    convention as reactions (`core.reactions`): the event itself carries
    the target state (`payload["archived"]`), so this is a plain
    last-write-wins read, not a running toggle count. False (not
    archived) when never set.
    """
    for event in reversed(events.read_events(conversation_id)):
        if event.type == "conversation/archive_changed":
            return bool(event.payload.get("archived", False))
    return False


def derive_conversation_deleted(conversation_id: str) -> bool:
    """Whether this conversation has been deleted. One-way: there is no
    "undelete" UI, so unlike archive this is a plain "has a
    conversation/deleted event ever been appended" check, not a toggle --
    scanning forward or backward makes no difference, but reversed matches
    every other derive_conversation_* function's own convention here.
    """
    for event in reversed(events.read_events(conversation_id)):
        if event.type == "conversation/deleted":
            return True
    return False


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
