"""Persona-to-persona delegation messages, with a hard per-pair round cap.

A delegation is just another event on the log (`delegation/sent`) — there is
no separate delegation store. The round count for a `(from_persona,
to_persona)` pair is derived by re-reading the log, scoped to the
*currently open turn* (the span since the most recent unmatched
`turn/start`; see `events.py`'s turn/start-turn/end pairing), so the cap
resets naturally at each new turn rather than accumulating across a whole
conversation's lifetime. If no turn is currently open (e.g. delegate() is
called outside any turn/start bracket), the count falls back to scanning the
whole conversation, which is the conservative choice — better to over-count
and cap early than to let an accounting gap defeat the cap's purpose.
"""

from __future__ import annotations

from pydantic import BaseModel

from tapestry.core import events


class DelegationRoundLimitExceeded(Exception):
    """Raised when a persona-to-persona delegation pair exceeds max_rounds.

    Hard cap so agent-to-agent @mentions can't spiral into an unbounded
    back-and-forth within one turn.
    """


class DelegationMessage(BaseModel):
    id: str
    conversation_id: str
    from_persona: str
    to_persona: str
    text: str
    round: int


def _events_since_current_turn_start(
    all_events: list[events.TapestryEvent],
) -> list[events.TapestryEvent]:
    """Slice `all_events` down to the currently-open turn, if any.

    Mirrors the same turn/start <-> turn/end matching events.py uses for
    crash recovery: a turn/start is "open" until a later turn/end whose
    payload["turn_id"] names it. Returns the suffix of `all_events` starting
    at the most recently opened still-open turn/start; returns the full
    list unchanged if no turn is currently open.
    """
    open_start_indices: dict[str, int] = {}
    for index, event in enumerate(all_events):
        if event.type == "turn/start":
            open_start_indices[event.id] = index
        elif event.type == "turn/end":
            turn_id = event.payload.get("turn_id")
            if turn_id is not None:
                open_start_indices.pop(turn_id, None)

    if not open_start_indices:
        return all_events
    current_turn_index = max(open_start_indices.values())
    return all_events[current_turn_index:]


async def delegate(
    conversation_id: str,
    from_persona: str,
    to_persona: str,
    text: str,
    max_rounds: int = 3,
) -> DelegationMessage:
    """Send one delegation message from `from_persona` to `to_persona`.

    Appends a `delegation/sent` event carrying `{from_persona, to_persona,
    text, round}` in its payload. Raises `DelegationRoundLimitExceeded`
    before appending anything if this send would exceed `max_rounds` for
    this exact (from_persona, to_persona) pair within the current turn.
    """
    all_events = events.read_events(conversation_id)
    scoped_events = _events_since_current_turn_start(all_events)
    prior_rounds = sum(
        1
        for event in scoped_events
        if event.type == "delegation/sent"
        and event.payload.get("from_persona") == from_persona
        and event.payload.get("to_persona") == to_persona
    )
    round_number = prior_rounds + 1
    if round_number > max_rounds:
        raise DelegationRoundLimitExceeded(
            f"delegation from {from_persona!r} to {to_persona!r} in "
            f"conversation {conversation_id!r} exceeded max_rounds={max_rounds}"
        )

    event = events.append_event(
        conversation_id=conversation_id,
        type="delegation/sent",
        actor=from_persona,
        payload={
            "from_persona": from_persona,
            "to_persona": to_persona,
            "text": text,
            "round": round_number,
        },
    )
    return DelegationMessage(
        id=event.id,
        conversation_id=conversation_id,
        from_persona=from_persona,
        to_persona=to_persona,
        text=text,
        round=round_number,
    )
