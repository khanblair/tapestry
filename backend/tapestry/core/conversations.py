"""Conversation history, projected FROM the event log — never stored twice.

`derive_messages` is the ONLY way conversation history is ever assembled.
Nothing in this codebase should keep a separate "messages" table or list —
every message a human or persona ever sees is recomputed from
`events.read_events` on demand.
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
