from __future__ import annotations

from tapestry.core.conversations import (
    Message,
    TimelineItem,
    derive_conversation_context,
    derive_messages,
    derive_timeline,
)
from tapestry.core.events import append_event


def test_derive_messages_projects_user_and_assistant_message_events():
    append_event("conv-1", "user/message", "human", {"text": "hello"})
    append_event("conv-1", "assistant/message", "ada", {"text": "hi there"})

    messages = derive_messages("conv-1")

    assert len(messages) == 2
    assert all(isinstance(m, Message) for m in messages)
    assert messages[0].actor == "human"
    assert messages[0].text == "hello"
    assert messages[0].event_type == "user/message"
    assert messages[1].actor == "ada"
    assert messages[1].text == "hi there"


def test_derive_messages_ignores_non_message_events():
    append_event("conv-1", "user/message", "human", {"text": "hello"})
    append_event("conv-1", "turn/start", "human", {})
    append_event("conv-1", "delegation/sent", "ada", {"text": "go do it"})

    messages = derive_messages("conv-1")

    assert len(messages) == 1
    assert messages[0].text == "hello"


def test_derive_messages_projects_any_type_ending_in_message():
    append_event("conv-1", "delegation/message", "ada", {"text": "delegated text"})

    messages = derive_messages("conv-1")

    assert len(messages) == 1
    assert messages[0].event_type == "delegation/message"
    assert messages[0].text == "delegated text"


def test_derive_messages_preserves_event_log_order():
    append_event("conv-1", "user/message", "human", {"text": "first"})
    append_event("conv-1", "assistant/message", "ada", {"text": "second"})
    append_event("conv-1", "user/message", "human", {"text": "third"})

    messages = derive_messages("conv-1")

    assert [m.text for m in messages] == ["first", "second", "third"]


def test_derive_messages_is_scoped_to_conversation():
    append_event("conv-1", "user/message", "human", {"text": "in conv 1"})
    append_event("conv-2", "user/message", "human", {"text": "in conv 2"})

    messages = derive_messages("conv-1")

    assert len(messages) == 1
    assert messages[0].text == "in conv 1"


def test_derive_messages_defaults_missing_text_to_empty_string():
    append_event("conv-1", "user/message", "human", {"no_text_key": True})

    messages = derive_messages("conv-1")

    assert messages[0].text == ""


def test_derive_messages_on_empty_conversation_returns_empty_list():
    assert derive_messages("does-not-exist") == []


def test_derive_timeline_projects_message_and_non_message_events():
    append_event("conv-1", "user/message", "human", {"text": "fix the auth bug"})
    append_event(
        "conv-1",
        "tool/result",
        "ada",
        {"task_id": "t1", "tool_name": "file_editor", "text": "wrote 3 lines", "is_error": False},
    )
    append_event(
        "conv-1",
        "task/diff_ready",
        "ada",
        {"task_id": "t1", "files": [{"name": "auth.py"}], "additions": 3, "deletions": 1},
    )

    timeline = derive_timeline("conv-1")

    assert [item.type for item in timeline] == ["user/message", "tool/result", "task/diff_ready"]
    assert all(isinstance(item, TimelineItem) for item in timeline)
    assert timeline[1].payload["tool_name"] == "file_editor"
    assert timeline[2].payload["additions"] == 3


def test_derive_timeline_excludes_pure_bookkeeping_events():
    append_event("conv-1", "user/message", "human", {"text": "hi"})
    append_event("conv-1", "turn/start", "human", {})
    append_event("conv-1", "model/response", "ada", {"cost": 0.01})
    append_event("conv-1", "turn/end", "ada", {"turn_id": "x", "reason": "done"})
    append_event("conv-1", "conversation/created", "system", {"kind": "dm"})
    append_event("conv-1", "conversation/context_set", "you", {"context": "Keep it casual."})

    timeline = derive_timeline("conv-1")

    assert len(timeline) == 1
    assert timeline[0].type == "user/message"


def test_derive_timeline_preserves_event_log_order():
    append_event("conv-1", "user/message", "human", {"text": "first"})
    append_event("conv-1", "task/started", "ada", {"task_id": "t1"})
    append_event("conv-1", "task/completed", "ada", {"task_id": "t1"})

    timeline = derive_timeline("conv-1")

    assert [item.type for item in timeline] == ["user/message", "task/started", "task/completed"]


def test_derive_timeline_is_scoped_to_conversation():
    append_event("conv-1", "task/started", "ada", {"task_id": "t1"})
    append_event("conv-2", "task/started", "rex", {"task_id": "t2"})

    timeline = derive_timeline("conv-1")

    assert len(timeline) == 1
    assert timeline[0].payload["task_id"] == "t1"


def test_derive_timeline_on_empty_conversation_returns_empty_list():
    assert derive_timeline("does-not-exist") == []


def test_derive_conversation_context_returns_none_when_never_set():
    assert derive_conversation_context("conv-1") is None


def test_derive_conversation_context_returns_the_set_value():
    append_event("conv-1", "conversation/context_set", "you", {"context": "Casual hangout only."})

    assert derive_conversation_context("conv-1") == "Casual hangout only."


def test_derive_conversation_context_returns_the_most_recent_set():
    append_event("conv-1", "conversation/context_set", "you", {"context": "First rule."})
    append_event("conv-1", "conversation/context_set", "you", {"context": "Updated rule."})

    assert derive_conversation_context("conv-1") == "Updated rule."


def test_derive_conversation_context_is_scoped_to_conversation():
    append_event("conv-1", "conversation/context_set", "you", {"context": "conv-1's rule."})
    append_event("conv-2", "conversation/context_set", "you", {"context": "conv-2's rule."})

    assert derive_conversation_context("conv-1") == "conv-1's rule."
