from __future__ import annotations

from tapestry.core.events import (
    ORPHAN_REPAIR_REASON,
    TapestryEvent,
    append_event,
    close_orphaned_turns,
    find_open_turns,
    is_main_thread_turn,
    list_open_turns,
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


def test_find_open_turns_empty_log_returns_empty_dict():
    assert find_open_turns([]) == {}


def test_find_open_turns_closed_turn_is_not_open():
    start = append_event("conv-1", "turn/start", "human", {})
    append_event("conv-1", "turn/end", "ada", {"turn_id": start.id, "reason": "done"})

    assert find_open_turns(read_events("conv-1")) == {}


def test_find_open_turns_returns_the_unmatched_start():
    start = append_event("conv-1", "turn/start", "human", {})

    open_turns = find_open_turns(read_events("conv-1"))

    assert list(open_turns.keys()) == [start.id]
    assert open_turns[start.id] == start


def test_find_open_turns_multiple_closed_one_open():
    closed_start = append_event("conv-1", "turn/start", "human", {})
    append_event("conv-1", "turn/end", "ada", {"turn_id": closed_start.id, "reason": "done"})
    open_start = append_event("conv-1", "turn/start", "human", {})

    open_turns = find_open_turns(read_events("conv-1"))

    assert list(open_turns.keys()) == [open_start.id]


def test_find_open_turns_several_open_at_once_are_all_returned():
    # Pre-fan-out this shouldn't occur in practice (see the module docstring's
    # documented-not-enforced invariant), but the scan itself makes no
    # assumption about how many are open -- that's what makes it reusable
    # once concurrent fan-out legs exist.
    first = append_event("conv-1", "turn/start", "human", {})
    second = append_event("conv-1", "turn/start", "human", {})

    open_turns = find_open_turns(read_events("conv-1"))

    assert list(open_turns.keys()) == [first.id, second.id]


def test_find_open_turns_ignores_turn_end_with_no_turn_id():
    start = append_event("conv-1", "turn/start", "human", {})
    append_event("conv-1", "turn/end", "system", {"reason": ORPHAN_REPAIR_REASON})

    open_turns = find_open_turns(read_events("conv-1"))

    assert list(open_turns.keys()) == [start.id]


def test_is_main_thread_turn_true_when_graph_thread_id_matches_conversation():
    event = append_event(
        "conv-1", "turn/start", "rex", {"graph_thread_id": "conv-1"}
    )
    assert is_main_thread_turn(event, "conv-1") is True


def test_is_main_thread_turn_false_for_a_fanout_leg():
    event = append_event(
        "conv-1", "turn/start", "rex", {"graph_thread_id": "conv-1::mention::rex::m1"}
    )
    assert is_main_thread_turn(event, "conv-1") is False


def test_is_main_thread_turn_true_when_field_absent_predates_fanout():
    event = append_event("conv-1", "turn/start", "rex", {})
    assert is_main_thread_turn(event, "conv-1") is True


def test_close_orphaned_turns_never_closes_an_open_fanout_leg():
    main_start = append_event(
        "conv-1", "turn/start", "rex", {"graph_thread_id": "conv-1"}
    )
    fanout_start = append_event(
        "conv-1", "turn/start", "vex", {"graph_thread_id": "conv-1::mention::vex::m1"}
    )

    closed = close_orphaned_turns("conv-1")

    assert len(closed) == 1
    assert closed[0].payload["turn_id"] == main_start.id
    # the fan-out leg's own open turn/start is left untouched
    still_open = find_open_turns(read_events("conv-1"))
    assert fanout_start.id in still_open


def test_close_orphaned_turns_still_works_after_sharing_find_open_turns():
    # Regression guard for the refactor onto find_open_turns: same behavior,
    # not just "the new function works in isolation."
    start = append_event("conv-1", "turn/start", "human", {})

    closed = close_orphaned_turns("conv-1")

    assert len(closed) == 1
    assert closed[0].payload["turn_id"] == start.id


def test_list_open_turns_empty_log_returns_empty_dict():
    assert list_open_turns() == {}


def test_list_open_turns_no_open_turns_returns_empty_dict():
    start = append_event("conv-1", "turn/start", "ada", {})
    append_event("conv-1", "turn/end", "system", {"turn_id": start.id, "reason": "done"})

    assert list_open_turns() == {}


def test_list_open_turns_keyed_by_persona_actor():
    start = append_event("conv-1", "turn/start", "rex", {})

    open_turns = list_open_turns()

    assert list(open_turns.keys()) == ["rex"]
    assert open_turns["rex"] == start


def test_list_open_turns_spans_every_conversation():
    # A conversation this process hasn't "revisited" (no read_events call
    # scoped to it) must still be found -- this is the whole point of NOT
    # using read_recent_events' limit-bounded scan.
    append_event("conv-1", "turn/start", "rex", {})
    append_event("conv-2", "turn/start", "nova", {})

    open_turns = list_open_turns()

    assert set(open_turns.keys()) == {"rex", "nova"}


def test_list_open_turns_several_open_at_once_for_different_personas_in_one_conversation():
    # The concurrent tag-all fan-out shape -- see the function's own
    # docstring. Each persona's own open turn must be found independently.
    append_event("conv-1", "turn/start", "rex", {})
    append_event("conv-1", "turn/start", "vex", {})

    open_turns = list_open_turns()

    assert set(open_turns.keys()) == {"rex", "vex"}


def test_list_open_turns_not_fooled_by_a_stale_quiet_conversation():
    # The exact gap read_recent_events(limit=...) has: pile up enough
    # unrelated activity elsewhere that a limited scan would push an old
    # open turn out of its window. list_open_turns must still find it.
    open_start = append_event("conv-quiet", "turn/start", "nova", {})
    for i in range(200):
        append_event("conv-busy", "some/other-event", "system", {"n": i})

    open_turns = list_open_turns()

    assert open_turns.get("nova") == open_start


def test_list_open_turns_excludes_a_turn_older_than_the_default_age_bound(db_connection):
    """Caught in review: close_orphaned_turns deliberately never auto-closes
    a fan-out leg's own open turn/start (see that function's docstring) --
    left completely unbounded, a crashed leg would show its persona as
    "busy" forever, with no way to clear it. list_open_turns bounds by age
    instead, specifically for that gap.
    """
    from datetime import datetime, timedelta, timezone

    open_start = append_event("conv-1", "turn/start", "nova", {})
    ancient = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="microseconds")
    db_connection.execute(
        "UPDATE events SET timestamp = ? WHERE id = ?", (ancient, open_start.id)
    )
    db_connection.commit()

    assert "nova" not in list_open_turns()


def test_list_open_turns_keeps_a_turn_within_the_default_age_bound(db_connection):
    from datetime import datetime, timedelta, timezone

    open_start = append_event("conv-1", "turn/start", "nova", {})
    recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(timespec="microseconds")
    db_connection.execute(
        "UPDATE events SET timestamp = ? WHERE id = ?", (recent, open_start.id)
    )
    db_connection.commit()

    assert list_open_turns().get("nova") == open_start.model_copy(update={"timestamp": recent})


def test_list_open_turns_respects_a_custom_max_age_seconds(db_connection):
    from datetime import datetime, timedelta, timezone

    open_start = append_event("conv-1", "turn/start", "nova", {})
    thirty_minutes_old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(
        timespec="microseconds"
    )
    db_connection.execute(
        "UPDATE events SET timestamp = ? WHERE id = ?", (thirty_minutes_old, open_start.id)
    )
    db_connection.commit()

    # Within the 3600s (1 hour) default...
    assert "nova" in list_open_turns()
    # ...but excluded by a deliberately tighter 600s (10 minute) bound.
    assert "nova" not in list_open_turns(max_age_seconds=600)


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
