from __future__ import annotations

import pytest

from tapestry.core.delegation import (
    DelegationMessage,
    DelegationRoundLimitExceeded,
    delegate,
)
from tapestry.core.events import append_event, read_events


@pytest.mark.asyncio
async def test_delegate_appends_delegation_sent_event_and_returns_message():
    message = await delegate("conv-1", "ada", "rex", "please implement this")

    assert isinstance(message, DelegationMessage)
    assert message.from_persona == "ada"
    assert message.to_persona == "rex"
    assert message.text == "please implement this"
    assert message.round == 1

    events = read_events("conv-1")
    assert len(events) == 1
    assert events[0].type == "delegation/sent"
    assert events[0].actor == "ada"
    assert events[0].payload == {
        "from_persona": "ada",
        "to_persona": "rex",
        "text": "please implement this",
        "round": 1,
    }


@pytest.mark.asyncio
async def test_delegate_increments_round_for_repeated_same_pair():
    first = await delegate("conv-1", "ada", "rex", "round 1")
    second = await delegate("conv-1", "ada", "rex", "round 2")

    assert first.round == 1
    assert second.round == 2


@pytest.mark.asyncio
async def test_delegate_tracks_rounds_independently_per_pair():
    await delegate("conv-1", "ada", "rex", "a->r")
    await delegate("conv-1", "ada", "rex", "a->r again")
    first_vex_round = await delegate("conv-1", "ada", "vex", "a->v")

    # a different (from, to) pair starts its own count at 1, unaffected by
    # the ada->rex pair's count
    assert first_vex_round.round == 1


@pytest.mark.asyncio
async def test_delegate_raises_once_max_rounds_exceeded():
    await delegate("conv-1", "ada", "rex", "1", max_rounds=2)
    await delegate("conv-1", "ada", "rex", "2", max_rounds=2)

    with pytest.raises(DelegationRoundLimitExceeded):
        await delegate("conv-1", "ada", "rex", "3", max_rounds=2)

    # the rejected attempt must not have appended anything
    events = read_events("conv-1")
    assert len(events) == 2


@pytest.mark.asyncio
async def test_delegate_round_count_resets_on_new_turn():
    append_event("conv-1", "turn/start", "human", {})
    await delegate("conv-1", "ada", "rex", "1", max_rounds=2)
    await delegate("conv-1", "ada", "rex", "2", max_rounds=2)

    # close the first turn, open a new one
    events = read_events("conv-1")
    first_turn_start = next(e for e in events if e.type == "turn/start")
    append_event("conv-1", "turn/end", "ada", {"turn_id": first_turn_start.id})
    append_event("conv-1", "turn/start", "human", {})

    # within the new turn, the same pair should be able to delegate again
    third = await delegate("conv-1", "ada", "rex", "3", max_rounds=2)

    assert third.round == 1


@pytest.mark.asyncio
async def test_delegate_with_explicit_turn_id_ignores_other_concurrently_open_turns():
    """tapestry_mentions_concurrency_status_spec.md §2.3: with tag-all's
    concurrent fan-out, more than one turn can be open in one conversation
    at once. The round cap must scope to the CALLING persona's own turn,
    not "whichever turn happens to be most recently opened" -- the old
    heuristic's assumption, which concurrent fan-out breaks.
    """
    # Leg A opens first and racks up delegation rounds for ada->rex.
    leg_a_start = append_event("conv-1", "turn/start", "ada", {"graph_thread_id": "conv-1::a"})
    await delegate("conv-1", "ada", "rex", "a-round-1", max_rounds=2, turn_id=leg_a_start.id)
    await delegate("conv-1", "ada", "rex", "a-round-2", max_rounds=2, turn_id=leg_a_start.id)

    # Leg B opens SECOND (so it's "most recently opened") and is still a
    # totally fresh turn for the very same (ada, rex) pair.
    leg_b_start = append_event("conv-1", "turn/start", "ada", {"graph_thread_id": "conv-1::b"})

    # Without turn_id, the old heuristic would slice from leg B's start
    # anyway (it's the most recent), so this specific assertion doesn't
    # distinguish old from new -- the real proof is the reverse case below.
    first_in_b = await delegate("conv-1", "ada", "rex", "b-round-1", max_rounds=2, turn_id=leg_b_start.id)
    assert first_in_b.round == 1, "leg B's own turn must start its count at 1, unpolluted by leg A"

    # Leg A delegates AGAIN after leg B opened -- the "most recently
    # opened" heuristic (turn_id=None) would now incorrectly slice from
    # leg B's start and undercount leg A's own history. With turn_id
    # pinned to leg A explicitly, it must still see its own 2 prior rounds
    # and raise.
    with pytest.raises(DelegationRoundLimitExceeded):
        await delegate("conv-1", "ada", "rex", "a-round-3", max_rounds=2, turn_id=leg_a_start.id)
