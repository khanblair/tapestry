from __future__ import annotations

from tapestry.core.events import (
    ORPHAN_REPAIR_REASON,
    TapestryEvent,
    append_event,
    close_orphaned_turns,
    read_events,
    read_recent_events,
)


def test_append_event_returns_populated_event():
    event = append_event("conv-1", "user/message", "human", {"text": "hi"})

    assert isinstance(event, TapestryEvent)
    assert event.conversation_id == "conv-1"
    assert event.type == "user/message"
    assert event.actor == "human"
    assert event.payload == {"text": "hi"}
    # uuid4().hex -> 32 lowercase hex chars, no dashes
    assert len(event.id) == 32
    int(event.id, 16)  # raises if not valid hex
    # ISO 8601 timestamp, parseable
    assert "T" in event.timestamp


def test_append_event_persists_and_is_read_back():
    appended = append_event("conv-1", "user/message", "human", {"text": "hello"})

    events = read_events("conv-1")

    assert len(events) == 1
    assert events[0] == appended


def test_read_events_is_scoped_to_conversation():
    append_event("conv-1", "user/message", "human", {"text": "a"})
    append_event("conv-2", "user/message", "human", {"text": "b"})

    conv1_events = read_events("conv-1")

    assert len(conv1_events) == 1
    assert conv1_events[0].payload == {"text": "a"}


def test_read_events_preserves_insertion_order():
    append_event("conv-1", "user/message", "human", {"n": 1})
    append_event("conv-1", "assistant/message", "ada", {"n": 2})
    append_event("conv-1", "user/message", "human", {"n": 3})

    events = read_events("conv-1")

    assert [e.payload["n"] for e in events] == [1, 2, 3]


def test_read_events_since_filters_out_earlier_events():
    append_event("conv-1", "user/message", "human", {"n": 1})
    marker = append_event("conv-1", "user/message", "human", {"n": 2})
    append_event("conv-1", "user/message", "human", {"n": 3})

    events = read_events("conv-1", since=marker.timestamp)

    # inclusive of the marker event itself
    assert [e.payload["n"] for e in events] == [2, 3]


def test_read_events_returns_empty_list_for_unknown_conversation():
    assert read_events("does-not-exist") == []


def test_close_orphaned_turns_closes_unmatched_turn_start():
    start = append_event("conv-1", "turn/start", "human", {})

    closed = close_orphaned_turns("conv-1")

    assert len(closed) == 1
    repair = closed[0]
    assert repair.type == "turn/end"
    assert repair.actor == "system"
    assert repair.payload["turn_id"] == start.id
    assert repair.payload["reason"] == ORPHAN_REPAIR_REASON == "interrupted"

    # and the repair event itself is now part of the log
    events = read_events("conv-1")
    assert events[-1].id == repair.id


def test_close_orphaned_turns_leaves_matched_turns_alone():
    start = append_event("conv-1", "turn/start", "human", {})
    append_event("conv-1", "turn/end", "ada", {"turn_id": start.id, "reason": "done"})

    closed = close_orphaned_turns("conv-1")

    assert closed == []
    # no extra event was appended
    assert len(read_events("conv-1")) == 2


def test_close_orphaned_turns_only_closes_the_orphan_among_several():
    open_start = append_event("conv-1", "turn/start", "human", {})
    closed_start = append_event("conv-1", "turn/start", "human", {})
    append_event(
        "conv-1", "turn/end", "ada", {"turn_id": closed_start.id, "reason": "done"}
    )

    closed = close_orphaned_turns("conv-1")

    assert len(closed) == 1
    assert closed[0].payload["turn_id"] == open_start.id


def test_read_recent_events_spans_every_conversation():
    append_event("conv-1", "task/started", "ada", {"n": 1})
    append_event("conv-2", "task/started", "rex", {"n": 2})

    recent = read_recent_events()

    assert {e.payload["n"] for e in recent} == {1, 2}


def test_read_recent_events_orders_newest_first():
    append_event("conv-1", "task/started", "ada", {"n": 1})
    append_event("conv-1", "task/started", "ada", {"n": 2})
    append_event("conv-1", "task/started", "ada", {"n": 3})

    recent = read_recent_events()

    assert [e.payload["n"] for e in recent] == [3, 2, 1]


def test_read_recent_events_respects_limit():
    for n in range(5):
        append_event("conv-1", "task/started", "ada", {"n": n})

    recent = read_recent_events(limit=2)

    assert [e.payload["n"] for e in recent] == [4, 3]


def test_read_recent_events_filters_by_type_before_limiting():
    append_event("conv-1", "task/started", "ada", {"n": 1})
    append_event("conv-1", "turn/start", "ada", {"n": 2})
    append_event("conv-1", "task/started", "ada", {"n": 3})
    append_event("conv-1", "turn/start", "ada", {"n": 4})
    append_event("conv-1", "task/started", "ada", {"n": 5})

    recent = read_recent_events(types={"task/started"}, limit=2)

    # the two most recent task/started events, ignoring the turn/start
    # events interleaved between them -- not "the 2 most recent events,
    # then filter" (which would return only n=5, since n=4 is a turn/start).
    assert [e.payload["n"] for e in recent] == [5, 3]


def test_read_recent_events_on_empty_log_returns_empty_list():
    assert read_recent_events() == []


def test_close_orphaned_turns_is_scoped_to_its_conversation():
    append_event("conv-1", "turn/start", "human", {})
    append_event("conv-2", "turn/start", "human", {})

    closed = close_orphaned_turns("conv-1")

    assert len(closed) == 1
    # conv-2's orphan is untouched
    conv2_events = read_events("conv-2")
    assert len(conv2_events) == 1
    assert conv2_events[0].type == "turn/start"
