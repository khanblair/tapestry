from __future__ import annotations

from tapestry.core.conversations import Message, derive_messages
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
