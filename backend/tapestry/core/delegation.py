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
    turn_id: str | None = None,
) -> list[events.TapestryEvent]:
    """Slice `all_events` down to one turn's span.

    When `turn_id` is given, slices from THAT turn's own `turn/start`
    event directly -- unambiguous even when several turns are
    concurrently open in one conversation (tag-all's fan-out, see
    `tapestry_mentions_concurrency_status_spec.md` §2.3), since a
    delegating persona's own turn_id is always in scope at the call site
    (`graph/build.py`'s `_handle_delegate` already has it).

    Without `turn_id` (kept for backward compatibility with any caller
    that doesn't have one in hand), falls back to the OLD heuristic:
    mirrors the same turn/start <-> turn/end matching events.py uses for
    crash recovery, and returns the suffix starting at the most recently
    opened still-open turn/start. That heuristic is a positional GUESS --
    correct only when at most one turn is ever open per conversation at a
    time, which every caller except the fan-out spawner guarantees; with
    concurrent fan-out legs open, "most recently opened" and "this
    persona's own turn" are not necessarily the same turn.
    """
    if turn_id is not None:
        for index, event in enumerate(all_events):
            if event.type == "turn/start" and event.id == turn_id:
                return all_events[index:]
        return all_events  # turn_id not found in the log -- conservative fallback

    open_start_indices: dict[str, int] = {}
    for index, event in enumerate(all_events):
        if event.type == "turn/start":
            open_start_indices[event.id] = index
        elif event.type == "turn/end":
            closed_turn_id = event.payload.get("turn_id")
            if closed_turn_id is not None:
                open_start_indices.pop(closed_turn_id, None)

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
    turn_id: str | None = None,
) -> DelegationMessage:
    """Send one delegation message from `from_persona` to `to_persona`.

    Appends a `delegation/sent` event carrying `{from_persona, to_persona,
    text, round}` in its payload. Raises `DelegationRoundLimitExceeded`
    before appending anything if this send would exceed `max_rounds` for
    this exact (from_persona, to_persona) pair within the current turn.

    `turn_id`, when given, scopes the round-count exactly to the CALLING
    persona's own turn -- required for correctness once concurrent tag-all
    fan-out legs can be open at once (see
    `_events_since_current_turn_start`'s own docstring). Every real caller
    (`graph/build.py`'s `_handle_delegate`) always has one; omitted only by
    tests exercising `delegate()` directly without a turn in progress.
    """
    all_events = events.read_events(conversation_id)
    scoped_events = _events_since_current_turn_start(all_events, turn_id)
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
