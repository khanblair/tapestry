"""Message reactions -- toggle logic, shared between web_adapter/api.py (a
human reacting via POST .../reactions) and graph/build.py (a persona
calling the react_to_message pseudo-tool). Lives in core/ rather than
either adapter, matching core/delegation.py's own precedent for action
logic triggered from more than one surface.

Reactions are always unambiguous adds/removes in the log itself, never
last-write-wins: `toggle_reaction` decides which one to append by checking
current state first, so `core.conversations.derive_messages`'s projection
never has to reconstruct intent from a repeated identical event.
"""

from __future__ import annotations

from tapestry.core import events


def is_reaction_active(conversation_id: str, message_id: str, actor: str, emoji: str) -> bool:
    """Whether `actor` currently has `emoji` active on `message_id` --
    reverse-scan for the most recent reaction/added or reaction/removed
    event matching this exact (message, actor, emoji) triple.
    """
    for event in reversed(events.read_events(conversation_id)):
        if (
            event.type not in ("reaction/added", "reaction/removed")
            or event.actor != actor
            or event.payload.get("message_id") != message_id
            or event.payload.get("emoji") != emoji
        ):
            continue
        return event.type == "reaction/added"
    return False


def toggle_reaction(
    conversation_id: str, message_id: str, actor: str, emoji: str
) -> events.TapestryEvent:
    """Flips (actor, message_id, emoji)'s reaction state and appends the
    matching event. Used identically whether `actor` is "you" (the human,
    via the HTTP endpoint) or a persona id (via the react_to_message
    pseudo-tool) -- reacting has no different meaning depending on who
    does it.
    """
    event_type = (
        "reaction/removed"
        if is_reaction_active(conversation_id, message_id, actor, emoji)
        else "reaction/added"
    )
    return events.append_event(
        conversation_id, event_type, actor=actor, payload={"message_id": message_id, "emoji": emoji}
    )
