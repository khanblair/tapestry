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
            )
        )
    return messages
