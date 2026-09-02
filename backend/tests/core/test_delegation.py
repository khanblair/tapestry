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
