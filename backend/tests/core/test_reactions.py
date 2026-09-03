from __future__ import annotations

from tapestry.core.events import append_event
from tapestry.core.reactions import is_reaction_active, toggle_reaction


def test_is_reaction_active_false_when_never_reacted():
    assert is_reaction_active("conv-1", "msg-1", "ada", "\U0001F44D") is False


def test_toggle_reaction_adds_on_first_call():
    event = toggle_reaction("conv-1", "msg-1", "ada", "\U0001F44D")

    assert event.type == "reaction/added"
    assert event.actor == "ada"
    assert event.payload == {"message_id": "msg-1", "emoji": "\U0001F44D"}
    assert is_reaction_active("conv-1", "msg-1", "ada", "\U0001F44D") is True


def test_toggle_reaction_removes_on_second_call():
    toggle_reaction("conv-1", "msg-1", "ada", "\U0001F44D")
    event = toggle_reaction("conv-1", "msg-1", "ada", "\U0001F44D")

    assert event.type == "reaction/removed"
    assert is_reaction_active("conv-1", "msg-1", "ada", "\U0001F44D") is False


def test_toggle_reaction_adds_again_on_third_call():
    toggle_reaction("conv-1", "msg-1", "ada", "\U0001F44D")
    toggle_reaction("conv-1", "msg-1", "ada", "\U0001F44D")
    event = toggle_reaction("conv-1", "msg-1", "ada", "\U0001F44D")

    assert event.type == "reaction/added"
    assert is_reaction_active("conv-1", "msg-1", "ada", "\U0001F44D") is True


def test_toggle_reaction_is_independent_per_actor():
    toggle_reaction("conv-1", "msg-1", "ada", "\U0001F44D")

    assert is_reaction_active("conv-1", "msg-1", "rex", "\U0001F44D") is False
    rex_event = toggle_reaction("conv-1", "msg-1", "rex", "\U0001F44D")
    assert rex_event.type == "reaction/added"
    # ada's own reaction is untouched by rex's toggle.
    assert is_reaction_active("conv-1", "msg-1", "ada", "\U0001F44D") is True


def test_toggle_reaction_is_independent_per_emoji():
    toggle_reaction("conv-1", "msg-1", "ada", "\U0001F44D")

    assert is_reaction_active("conv-1", "msg-1", "ada", "\U0001F389") is False
    event = toggle_reaction("conv-1", "msg-1", "ada", "\U0001F389")
    assert event.type == "reaction/added"
    assert is_reaction_active("conv-1", "msg-1", "ada", "\U0001F44D") is True


def test_toggle_reaction_is_independent_per_message():
    toggle_reaction("conv-1", "msg-1", "ada", "\U0001F44D")

    assert is_reaction_active("conv-1", "msg-2", "ada", "\U0001F44D") is False


def test_is_reaction_active_ignores_events_for_other_conversations():
    toggle_reaction("conv-1", "msg-1", "ada", "\U0001F44D")

    # append_event scopes purely by conversation_id -- msg-1 happening to
    # share an id string across two different conversations must not leak
    # reaction state between them.
    append_event("conv-2", "user/message", "ada", {"text": "unrelated"})
    assert is_reaction_active("conv-2", "msg-1", "ada", "\U0001F44D") is False
