"""Tests for tapestry.graph.build -- the persona -> approval -> execute graph.

`models.litellm_client.call_model` is mocked throughout (no real API
calls). Everything else is real: `core.events`/`core.ask`/`core.delegation`
against the in-memory-sqlite fixture from tests/graph/conftest.py, and a
REAL `AsyncSqliteSaver` against a tmp_path file for every graph-execution
test -- so the actual interrupt/resume/checkpoint mechanics are genuinely
exercised, not mocked away, per the task's own instruction.

The single most important test here is
`test_approval_interrupt_resume_tool_runs_exactly_once` -- it's the direct
proof that the persona/approval/execute node split actually honors
interrupt()'s documented re-execution-from-the-top behavior: the tool must
run zero times while paused and exactly once after resume, never twice.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.types import Command

from tapestry.core import events as events_module
from tapestry.graph import build
from tapestry.graph.budgets import DelegationDepthExceeded, TurnBudgetExceeded
from tapestry.graph.checkpointer import get_checkpointer
from tapestry.graph.verify import VerificationResult
from tapestry.models.litellm_client import ModelResponse
from tapestry.skills.registry import SkillSummary
from tapestry.tools.file_editor import ToolResult


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _plain_response(text: str) -> ModelResponse:
    return ModelResponse(text=text, tool_calls=None, model_used="test-model")


def _tool_call_response(text: str, name: str, arguments: dict, call_id: str = "call_1") -> ModelResponse:
    return ModelResponse(
        text=text, tool_calls=[_tool_call(name, arguments, call_id)], model_used="test-model"
    )


async def _run_graph(tmp_path, conversation_id: str):
    """Build a real graph against a tmp_path checkpoint file. Returns
    (graph, config); caller is responsible for closing
    graph.checkpointer.conn when done.
    """
    checkpoint_path = str(tmp_path / f"{conversation_id}.sqlite")
    graph = await build.build_graph(checkpoint_path)
    config = {"configurable": {"thread_id": conversation_id}}
    return graph, config


# ---------------------------------------------------------------------------
# graph_thread_id -- the real LangGraph checkpoint thread a turn ran on
# (tapestry_mentions_concurrency_status_spec.md §2.2/§2.3's foundation)
# ---------------------------------------------------------------------------


async def test_turn_start_records_the_real_graph_thread_id_not_the_conversation_id():
    """Foundational to fan-out (§2.2): a turn/start's `graph_thread_id`
    must reflect the ACTUAL LangGraph checkpoint thread this invocation
    ran on, sourced from LangGraph's own injected `RunnableConfig` -- not
    assumed to equal `conversation_id`. Proven here by deliberately using
    a DIFFERENT thread_id than conversation_id, exactly the shape a
    fan-out leg will have (its own thread, same conversation's event log).
    """
    import tempfile

    conversation_id = "conv-graph-thread-id-1"
    fanout_thread_id = "conv-graph-thread-id-1::mention::rex::msg-1"

    with tempfile.TemporaryDirectory() as tmp:
        graph = await build.build_graph(f"{tmp}/checkpoint.sqlite")
        try:
            call_model_mock = AsyncMock(return_value=_plain_response("hi"))
            with patch.object(build, "call_model", call_model_mock):
                state = build.new_state(conversation_id, "rex")
                config = {"configurable": {"thread_id": fanout_thread_id}}
                await graph.ainvoke(state, config)

            logged = events_module.read_events(conversation_id)
            turn_start = next(e for e in logged if e.type == "turn/start")
            assert turn_start.payload["graph_thread_id"] == fanout_thread_id
        finally:
            await graph.checkpointer.conn.close()


# ---------------------------------------------------------------------------
# Static registry/schema sanity
# ---------------------------------------------------------------------------


def test_tool_registry_has_the_seven_canonical_keys_plus_skill_loader():
    assert set(build.TOOL_REGISTRY.keys()) == {
        "file_editor_read",
        "file_editor",
        "terminal",
        "terminal_read_only",
        "git",
        "test_runner",
        "deploy_pipeline",
        "skill_loader",
    }


def test_every_persona_yaml_tool_is_a_real_registry_key():
    for persona in build.PERSONAS.values():
        for tool_name in persona.tools:
            assert tool_name in build.TOOL_REGISTRY, (
                f"persona {persona.id!r} lists tool {tool_name!r}, which is not "
                "a real TOOL_REGISTRY key"
            )


def test_tool_schemas_cover_every_registry_key_plus_pseudo_tools():
    for name in build.TOOL_REGISTRY:
        assert name in build.TOOL_SCHEMAS
    assert build.DELEGATE_TOOL_NAME in build.TOOL_SCHEMAS
    assert build.TASK_COMPLETE_TOOL_NAME in build.TOOL_SCHEMAS


def test_approval_and_diff_producing_tools_are_real_registry_keys():
    for name in build.TOOLS_REQUIRING_APPROVAL | build.DIFF_PRODUCING_TOOLS:
        assert name in build.TOOL_REGISTRY


def test_build_tool_schemas_always_includes_core_capabilities():
    ada = build.PERSONAS["ada"]
    schemas = build._build_tool_schemas(ada.tools)
    names = {s["function"]["name"] for s in schemas}
    assert build.SKILL_LOADER_TOOL_NAME in names
    assert build.DELEGATE_TOOL_NAME in names
    assert build.TASK_COMPLETE_TOOL_NAME in names
    # Ada's own permitted tools are included too.
    assert "file_editor_read" in names
    assert "terminal_read_only" in names
    # But NOT tools she isn't permitted (file_editor is Rex's, not hers).
    assert "file_editor" not in names


# ---------------------------------------------------------------------------
# System prompt / skill-catalog rendering
#
# Root cause of the "Ada loads three skills to answer 'hi'" UX bug: the
# catalog SkillSummary.when_to_use field was parsed from frontmatter and
# fully populated, but _build_system_prompt only ever rendered
# `name: description`, so the model never saw the guidance that would tell
# it a skill doesn't apply to small talk. It had to guess from the
# description alone, and guessed wrong -- expensively.
# ---------------------------------------------------------------------------


def _skill_summary(name: str, description: str, when_to_use: str | None) -> SkillSummary:
    return SkillSummary(
        name=name,
        description=description,
        when_to_use=when_to_use,
        user_invocable=False,
        rank=0,
        source="test",
    )


def test_system_prompt_surfaces_when_to_use_so_the_model_can_skip_irrelevant_skills():
    ada = build.PERSONAS["ada"]
    catalog = [
        _skill_summary(
            "systematic-debugging",
            "A rigorous process for isolating and fixing bugs.",
            "Use when investigating a reported bug or unexpected behavior, not for small talk.",
        )
    ]
    prompt = build._build_system_prompt(ada, catalog)
    assert "systematic-debugging: A rigorous process" in prompt
    assert "When to use: Use when investigating a reported bug" in prompt


def test_system_prompt_tells_the_model_not_to_load_skills_for_small_talk():
    ada = build.PERSONAS["ada"]
    catalog = [_skill_summary("test-driven-development", "Write a failing test first.", "Use before implementing.")]
    prompt = build._build_system_prompt(ada, catalog)
    assert "small talk" in prompt.lower()


# ---------------------------------------------------------------------------
# Conversation-roster awareness
#
# Live-tested bug: asked "ask rex if he's online" in a conversation Rex
# wasn't a member of, Ada answered "It looks like Rex is not a recognized
# persona" -- false, he's a real persona elsewhere in the system. Root
# cause: persona_node never told the model who was actually in the room.
# ---------------------------------------------------------------------------


def test_system_prompt_lists_conversation_members_with_status():
    ada = build.PERSONAS["ada"]
    prompt = build._build_system_prompt(ada, [], ["ada", "nova"])
    assert "Ada (Architect, online) [you]" in prompt
    assert "Nova (DevOps, paused)" in prompt


def test_system_prompt_distinguishes_non_member_from_nonexistent():
    ada = build.PERSONAS["ada"]
    prompt = build._build_system_prompt(ada, [], ["ada", "nova"])
    # Rex is a real persona, just not in this conversation -- must be named
    # as existing, not lumped in with an outright unknown name.
    assert "Rex (Developer, online)" in prompt
    assert "not a recognized persona" not in prompt.lower()


def test_system_prompt_omits_roster_when_membership_is_unset():
    # member_ids=None (the default) means "unknown," not "empty conversation"
    # -- rendering an empty roster would wrongly tell a persona she herself
    # isn't in the conversation. Covers the many existing tests that invoke
    # the graph directly without ever appending a conversation/created event.
    ada = build.PERSONAS["ada"]
    prompt = build._build_system_prompt(ada, [])
    assert "Conversation roster" not in prompt


def test_system_prompt_tells_a_persona_to_acknowledge_siblings_in_a_group():
    # The other half of the fan-out visibility fix: seeing a sibling's
    # reply in history is necessary but not sufficient -- something has to
    # tell the model that reading it matters, or it just answers the human
    # independently as if it were the only persona in the room.
    ada = build.PERSONAS["ada"]
    prompt = build._build_system_prompt(ada, [], ["ada", "rex"])
    assert "not the only one replying" in prompt
    assert "Actually respond to what a sibling just said" in prompt


def test_system_prompt_omits_group_dialogue_guidance_in_a_dm():
    # A DM has exactly one persona -- "acknowledge other personas" is
    # meaningless noise there, not a helpful instruction.
    ada = build.PERSONAS["ada"]
    prompt = build._build_system_prompt(ada, [], ["ada"])
    assert "not the only one replying" not in prompt


def test_system_prompt_always_tells_the_model_to_keep_replies_short():
    # Live-tested UX complaint: a persona's reply for what should have been
    # a quick reaction ran to several paragraphs. Unlike roster/group
    # guidance, this applies unconditionally -- not gated on membership or
    # round.
    ada = build.PERSONAS["ada"]
    prompt = build._build_system_prompt(ada, [])
    assert "keep it short" in prompt.lower()
    assert "no em dashes" in prompt.lower()


def test_system_prompt_adds_continuation_guidance_only_on_a_continuation_round():
    ada = build.PERSONAS["ada"]
    normal_prompt = build._build_system_prompt(ada, [], ["ada", "rex"])
    continuation_prompt = build._build_system_prompt(
        ada, [], ["ada", "rex"], is_continuation_round=True
    )
    assert "autonomous continuation" not in normal_prompt.lower()
    assert "autonomous continuation" in continuation_prompt.lower()
    assert "pass_turn" in continuation_prompt


def test_continuation_guidance_tells_the_model_not_to_cut_short_a_timed_request():
    # Live-tested UX complaint: "@all can you hold a 2 minute conversation"
    # stopped after ~80s once both personas ran out of organic things to
    # say -- the pass-by-default framing had no exception for a human
    # asking for an extended/timed exchange, so it under-delivered on a
    # literal duration ask with zero signal telling the model that
    # mattered here.
    ada = build.PERSONAS["ada"]
    prompt = build._build_system_prompt(ada, [], ["ada", "rex"], is_continuation_round=True)
    assert "extended or timed exchange" in prompt
    assert "don't cut it short" in prompt.lower()


def test_continuation_guidance_tells_the_model_not_to_repeat_a_goodbye():
    # Live-tested UX complaint: once both personas had already said
    # goodbye, the round-continuation loop kept going for 10+ more rounds
    # of "good chat, catch you later" / "ha, you got the last word in
    # again" -- the pass-by-default framing never told the model that a
    # SECOND farewell is redundant, not a genuine continuation, even
    # though the timed-exchange exception right above this rule could
    # otherwise be read as license to keep manufacturing replies.
    ada = build.PERSONAS["ada"]
    prompt = build._build_system_prompt(ada, [], ["ada", "rex"], is_continuation_round=True)
    assert "already wound the conversation down" in prompt.lower()
    assert "second, third, or further goodbye" in prompt.lower()
    assert "no longer applies" in prompt.lower()


# ---------------------------------------------------------------------------
# Proactive check-in -- the one turn web_adapter's proactive check-in loop
# spawns when a `proactive` persona's DM has sat idle since the human's own
# last message. See web_adapter/api.py's _proactive_checkin_targets.
# ---------------------------------------------------------------------------


def test_system_prompt_adds_proactive_checkin_guidance_only_when_flagged():
    ada = build.PERSONAS["ada"]
    normal_prompt = build._build_system_prompt(ada, [])
    checkin_prompt = build._build_system_prompt(ada, [], is_proactive_checkin=True)
    assert "reaching out" not in normal_prompt.lower()
    assert "reaching out" in checkin_prompt.lower()
    assert "unprompted" in checkin_prompt.lower()


# ---------------------------------------------------------------------------
# conversation_context -- human-set ground rules for a specific conversation
# ---------------------------------------------------------------------------


def test_system_prompt_omits_conversation_context_when_unset():
    ada = build.PERSONAS["ada"]
    prompt = build._build_system_prompt(ada, [])
    assert "ground rules" not in prompt.lower()


def test_system_prompt_renders_conversation_context_above_the_personas_own_prompt():
    ada = build.PERSONAS["ada"]
    prompt = build._build_system_prompt(
        ada, [], conversation_context="No programming talk in this thread."
    )
    assert "no programming talk in this thread" in prompt.lower()
    assert prompt.lower().index("no programming talk") < prompt.lower().index(
        ada.system_prompt.strip().lower()[:30]
    )


def test_system_prompt_frames_conversation_context_as_taking_precedence():
    ada = build.PERSONAS["ada"]
    prompt = build._build_system_prompt(ada, [], conversation_context="Keep it casual.")
    assert "take precedence" in prompt.lower()


# ---------------------------------------------------------------------------
# pass_turn -- only offered on an autonomous continuation round
# ---------------------------------------------------------------------------


def test_build_tool_schemas_omits_pass_turn_by_default():
    ada = build.PERSONAS["ada"]
    names = {s["function"]["name"] for s in build._build_tool_schemas(ada.tools)}
    assert build.PASS_TURN_TOOL_NAME not in names


def test_build_tool_schemas_includes_pass_turn_on_a_continuation_round():
    ada = build.PERSONAS["ada"]
    names = {
        s["function"]["name"]
        for s in build._build_tool_schemas(ada.tools, include_pass_turn=True)
    }
    assert build.PASS_TURN_TOOL_NAME in names


# ---------------------------------------------------------------------------
# react_to_message -- offered unconditionally (unlike pass_turn), never ends
# the turn on its own: loops back to "persona" so a reaction and a real
# reply can both happen in one turn.
# ---------------------------------------------------------------------------


def test_build_tool_schemas_includes_react_to_message_unconditionally():
    ada = build.PERSONAS["ada"]
    names = {s["function"]["name"] for s in build._build_tool_schemas(ada.tools)}
    assert build.REACT_TO_MESSAGE_TOOL_NAME in names


async def test_react_to_message_appends_reaction_and_loops_back_for_a_real_reply(tmp_path):
    conversation_id = "conv-react-1"
    target = events_module.append_event(
        conversation_id, "user/message", actor="you", payload={"text": "how's everyone doing?"}
    )

    reacts = _tool_call_response(
        "", "react_to_message", {"message_id": target.id, "emoji": "\U0001F44D"}
    )
    replies = _plain_response("Doing well, thanks for asking!")
    call_model_mock = AsyncMock(side_effect=[reacts, replies])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")
            result = await graph.ainvoke(state, config)

        assert result["next_node"] == "end"
        assert call_model_mock.await_count == 2

        logged = events_module.read_events(conversation_id)
        reaction_events = [e for e in logged if e.type == "reaction/added"]
        assert len(reaction_events) == 1
        assert reaction_events[0].actor == "rex"
        assert reaction_events[0].payload == {"message_id": target.id, "emoji": "\U0001F44D"}

        # Loop-back, not turn-ending: a real assistant/message follows the
        # reaction, in the SAME turn (only one turn/end -- reacting never
        # appended its own).
        assert any(
            e.type == "assistant/message" and e.payload["text"] == "Doing well, thanks for asking!"
            for e in logged
        )
        turn_ends = [e for e in logged if e.type == "turn/end"]
        assert len(turn_ends) == 1
        assert turn_ends[0].payload["reason"] == "assistant_reply"
    finally:
        await graph.checkpointer.conn.close()


async def test_react_to_message_retry_satisfies_the_tool_response_contract(tmp_path):
    """Same bug class just fixed in _handle_task_complete: the retry's own
    message list must satisfy every OpenAI-compatible provider's contract
    -- an assistant message with tool_calls immediately followed by a
    tool-role message carrying the SAME tool_call_id, never any other role.
    """
    conversation_id = "conv-react-2"
    target = events_module.append_event(
        conversation_id, "user/message", actor="you", payload={"text": "hello"}
    )

    reacts = _tool_call_response("", "react_to_message", {"message_id": target.id, "emoji": "\U0001F389"})
    replies = _plain_response("Congrats!")
    call_model_mock = AsyncMock(side_effect=[reacts, replies])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")
            await graph.ainvoke(state, config)

        second_call_messages = call_model_mock.await_args_list[1].kwargs["messages"]
        tool_call_message = next(m for m in second_call_messages if m.get("tool_calls"))
        index = second_call_messages.index(tool_call_message)
        feedback_message = second_call_messages[index + 1]
        assert feedback_message["role"] == "tool"
        assert feedback_message["tool_call_id"] == tool_call_message["tool_calls"][0]["id"]
    finally:
        await graph.checkpointer.conn.close()


async def test_react_to_message_on_unknown_message_id_does_not_crash(tmp_path):
    conversation_id = "conv-react-3"
    reacts = _tool_call_response("", "react_to_message", {"message_id": "no-such-id", "emoji": "\U0001F44D"})
    replies = _plain_response("Anyway, how can I help?")
    call_model_mock = AsyncMock(side_effect=[reacts, replies])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")
            result = await graph.ainvoke(state, config)

        assert result["next_node"] == "end"
        logged = events_module.read_events(conversation_id)
        assert not any(e.type == "reaction/added" for e in logged)
    finally:
        await graph.checkpointer.conn.close()


# Found live: a real model has no way to know a message's actual id (never
# rendered anywhere in its own history or system prompt) -- confirmed by
# watching two personas repeatedly guess wrong ids and fail. The tests
# below cover the fix: resolving a react target from what a model actually
# has (who said it, or nothing at all) instead of requiring an exact id.


async def test_react_to_message_resolves_target_actor_you_to_the_humans_last_message(tmp_path):
    conversation_id = "conv-react-4"
    events_module.append_event(conversation_id, "user/message", actor="you", payload={"text": "first"})
    target = events_module.append_event(
        conversation_id, "user/message", actor="you", payload={"text": "second, react to this"}
    )

    reacts = _tool_call_response("", "react_to_message", {"target_actor": "you", "emoji": "\U0001F44D"})
    replies = _plain_response("Done!")
    call_model_mock = AsyncMock(side_effect=[reacts, replies])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")
            await graph.ainvoke(state, config)

        logged = events_module.read_events(conversation_id)
        reaction = next(e for e in logged if e.type == "reaction/added")
        assert reaction.payload["message_id"] == target.id
    finally:
        await graph.checkpointer.conn.close()


async def test_react_to_message_resolves_target_actor_by_persona_name_case_insensitive(tmp_path):
    conversation_id = "conv-react-5"
    events_module.append_event(conversation_id, "conversation/created", actor="system", payload={
        "kind": "group", "persona_ids": ["ada", "rex"],
    })
    ada_message = events_module.append_event(
        conversation_id, "assistant/message", actor="ada", payload={"text": "here's my proposal"}
    )

    reacts = _tool_call_response("", "react_to_message", {"target_actor": "Ada", "emoji": "\U0001F389"})
    replies = _plain_response("Nice work Ada.")
    call_model_mock = AsyncMock(side_effect=[reacts, replies])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")
            await graph.ainvoke(state, config)

        logged = events_module.read_events(conversation_id)
        reaction = next(e for e in logged if e.type == "reaction/added")
        assert reaction.payload["message_id"] == ada_message.id
    finally:
        await graph.checkpointer.conn.close()


async def test_react_to_message_with_no_target_falls_back_to_most_recent_other_message(tmp_path):
    conversation_id = "conv-react-6"
    events_module.append_event(conversation_id, "user/message", actor="you", payload={"text": "hi"})
    latest = events_module.append_event(
        conversation_id, "user/message", actor="you", payload={"text": "react to whatever's freshest"}
    )

    reacts = _tool_call_response("", "react_to_message", {"emoji": "\U0001F44D"})
    replies = _plain_response("Done!")
    call_model_mock = AsyncMock(side_effect=[reacts, replies])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")
            await graph.ainvoke(state, config)

        logged = events_module.read_events(conversation_id)
        reaction = next(e for e in logged if e.type == "reaction/added")
        assert reaction.payload["message_id"] == latest.id
    finally:
        await graph.checkpointer.conn.close()


async def test_react_to_message_target_actor_unresolvable_falls_back_to_most_recent_other_message(tmp_path):
    conversation_id = "conv-react-7"
    latest = events_module.append_event(
        conversation_id, "user/message", actor="you", payload={"text": "hello"}
    )

    # "the previous speaker" isn't a real actor id, alias, or persona name
    # -- the fallback still finds something reasonable rather than failing.
    reacts = _tool_call_response(
        "", "react_to_message", {"target_actor": "the previous speaker", "emoji": "\U0001F44D"}
    )
    replies = _plain_response("Done!")
    call_model_mock = AsyncMock(side_effect=[reacts, replies])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")
            await graph.ainvoke(state, config)

        logged = events_module.read_events(conversation_id)
        reaction = next(e for e in logged if e.type == "reaction/added")
        assert reaction.payload["message_id"] == latest.id
    finally:
        await graph.checkpointer.conn.close()


# ---------------------------------------------------------------------------
# Reply pacing -- _reply_delay_seconds is a pure function; the actual sleep
# is a separate, trivially-mockable seam (`_breathing_pause`, disabled for
# the whole test suite by tests/graph/conftest.py) so no test here should
# ever have to wait through a real delay.
# ---------------------------------------------------------------------------


def test_reply_delay_is_clamped_to_the_documented_range():
    assert build._MIN_REPLY_DELAY_SECONDS <= build._reply_delay_seconds("")
    assert build._reply_delay_seconds("x" * 10_000) <= build._MAX_REPLY_DELAY_SECONDS


def test_reply_delay_grows_with_reply_length():
    short = build._reply_delay_seconds("hi")
    long = build._reply_delay_seconds("a rather longer reply " * 20)
    assert long > short


async def test_persona_node_wires_real_conversation_membership_into_the_prompt(tmp_path):
    conversation_id = "conv-roster-1"
    events_module.append_event(
        conversation_id,
        "conversation/created",
        actor="you",
        payload={"kind": "group", "name": "#roster-test", "persona_ids": ["ada", "rex"]},
    )
    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        call_model_mock = AsyncMock(return_value=_plain_response("hi"))
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "ada")
            await graph.ainvoke(state, config)

        system_prompt = call_model_mock.call_args.kwargs["messages"][0]["content"]
        assert "Rex (Developer, online) [you]" not in system_prompt
        assert "Ada (Architect, online) [you]" in system_prompt
        assert "Nova (DevOps, paused)" in system_prompt  # not a member -- listed as existing elsewhere
    finally:
        await graph.checkpointer.conn.close()


def test_new_state_fills_documented_defaults():
    state = build.new_state("conv-1", "rex")
    assert state["conversation_id"] == "conv-1"
    assert state["persona_id"] == "rex"
    assert state["messages"] == []
    assert state["pending_tool_call"] is None
    assert state["delegation_depth"] == 0
    assert state["turn_count"] == 0
    assert state["task_id"] is None
    assert state["thread_id"] is None
    assert state["is_continuation_round"] is False
    assert state["is_proactive_checkin"] is False


def test_new_state_can_be_built_as_a_continuation_round():
    state = build.new_state("conv-1", "rex", is_continuation_round=True)
    assert state["is_continuation_round"] is True


def test_new_state_can_be_built_as_a_proactive_checkin():
    state = build.new_state("conv-1", "rex", is_proactive_checkin=True)
    assert state["is_proactive_checkin"] is True


# ---------------------------------------------------------------------------
# Full graph execution: plain reply (no tool call at all)
# ---------------------------------------------------------------------------


async def test_plain_reply_ends_the_turn_and_logs_events(tmp_path):
    conversation_id = "conv-plain-1"
    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(
            build, "call_model", AsyncMock(return_value=_plain_response("Hello there!"))
        ):
            state = build.new_state(conversation_id, "rex")
            result = await graph.ainvoke(state, config)

        assert "__interrupt__" not in result
        assert result["next_node"] == "end"

        logged = events_module.read_events(conversation_id)
        types = [e.type for e in logged]
        assert "turn/start" in types
        assert "assistant/message" in types
        assert "turn/end" in types
        turn_end = next(e for e in logged if e.type == "turn/end")
        assert turn_end.payload["reason"] == "assistant_reply"
    finally:
        await graph.checkpointer.conn.close()


# ---------------------------------------------------------------------------
# Regression: cross-turn conversation memory. Every adapter invokes the
# graph with a fresh new_state() -- messages: [] -- for EVERY external
# turn, not just the first; passed as ainvoke input against an existing
# checkpointed thread, that genuinely replaces the messages channel rather
# than merging into it (confirmed empirically, not assumed). Without
# persona_node rebuilding history from the event log at the start of a new
# turn, a persona has zero memory of anything before the current message.
# ---------------------------------------------------------------------------


async def test_second_turn_includes_the_first_turns_history(tmp_path):
    conversation_id = "conv-memory-1"
    captured_messages = []

    async def call_model_capturing(model, messages, tools, **kwargs):
        captured_messages.append(list(messages))
        return ModelResponse(text=f"reply {len(captured_messages)}", tool_calls=None, model_used=model)

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_capturing):
            events_module.append_event(conversation_id, "user/message", actor="you", payload={"text": "first message"})
            await graph.ainvoke(build.new_state(conversation_id, "rex"), config)

            events_module.append_event(
                conversation_id, "user/message", actor="you", payload={"text": "second message"}
            )
            await graph.ainvoke(build.new_state(conversation_id, "rex"), config)

        turn_2_messages = captured_messages[1]
        # system prompt, "first message", the first turn's own reply, then
        # "second message" -- in that order, nothing dropped.
        contents = [m["content"] for m in turn_2_messages]
        assert "first message" in contents
        assert "reply 1" in contents
        assert contents[-1] == "second message"
        roles_by_content = {m["content"]: m["role"] for m in turn_2_messages}
        assert roles_by_content["first message"] == "user"
        assert roles_by_content["reply 1"] == "assistant"
    finally:
        await graph.checkpointer.conn.close()


async def test_within_a_single_turns_tool_loop_history_is_unaffected(tmp_path):
    """The fix must not touch the ALREADY-correct within-turn behavior:
    the propose -> execute -> observe loop (same turn_id, multiple
    persona_node passes inside one ainvoke) keeps accumulating via
    state["messages"] exactly as before -- it must not re-read the event
    log (which wouldn't even have the in-flight tool exchange logged as a
    /message-suffixed event yet) on every internal pass.
    """
    conversation_id = "conv-memory-2"
    call_count = {"file_editor": 0}

    async def fake_file_editor(arguments: dict) -> ToolResult:
        call_count["file_editor"] += 1
        return ToolResult(text="wrote it", is_error=False)

    propose = _tool_call_response("Creating.", "file_editor", {"command": "create", "path": "/tmp/x.txt"})
    final = _plain_response("Done.")
    call_model_mock = AsyncMock(side_effect=[propose, final])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        events_module.append_event(
            conversation_id, "mode/changed", "you", {"mode": "bypass", "persona_id": "rex"}
        )
        with patch.object(build, "call_model", call_model_mock), patch.dict(
            build.TOOL_REGISTRY, {"file_editor": fake_file_editor}
        ):
            events_module.append_event(conversation_id, "user/message", actor="you", payload={"text": "make the file"})
            result = await graph.ainvoke(build.new_state(conversation_id, "rex"), config)

        assert call_count["file_editor"] == 1
        assert result["next_node"] == "end"
    finally:
        await graph.checkpointer.conn.close()


async def test_group_conversation_attributes_other_personas_messages(tmp_path):
    """A message from a persona OTHER than the one about to respond is
    still role: "user" (not "assistant" -- it isn't this persona's own
    prior output) but prefixed with who actually said it.
    """
    conversation_id = "conv-memory-3"
    captured_messages = []

    async def call_model_capturing(model, messages, tools, **kwargs):
        captured_messages.append(list(messages))
        return ModelResponse(text="ok", tool_calls=None, model_used=model)

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        events_module.append_event(conversation_id, "user/message", actor="you", payload={"text": "let's plan this"})
        events_module.append_event(conversation_id, "assistant/message", actor="ada", payload={"text": "here's the plan"})
        events_module.append_event(conversation_id, "user/message", actor="you", payload={"text": "go ahead, rex"})

        with patch.object(build, "call_model", call_model_capturing):
            await graph.ainvoke(build.new_state(conversation_id, "rex"), config)

        messages = captured_messages[0]
        ada_message = next(m for m in messages if "here's the plan" in m["content"])
        assert ada_message["role"] == "user"
        assert ada_message["content"] == "ada: here's the plan"
    finally:
        await graph.checkpointer.conn.close()


# ---------------------------------------------------------------------------
# The critical test: approval interrupt/resume proves the node split works
# ---------------------------------------------------------------------------


async def test_approval_interrupt_resume_tool_runs_exactly_once(tmp_path):
    conversation_id = "conv-approve-1"
    call_count = {"file_editor": 0}

    async def fake_file_editor(arguments: dict) -> ToolResult:
        call_count["file_editor"] += 1
        return ToolResult(text="wrote the file", is_error=False)

    propose = _tool_call_response(
        "I'll create the file.",
        "file_editor",
        {"command": "create", "path": "/tmp/x.txt", "file_text": "hi"},
    )
    final = _plain_response("Done, file created.")
    call_model_mock = AsyncMock(side_effect=[propose, final])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock), patch.dict(
            build.TOOL_REGISTRY, {"file_editor": fake_file_editor}
        ):
            state = build.new_state(conversation_id, "rex")

            # First run: pauses at the approval gate.
            paused_result = await graph.ainvoke(state, config)
            assert "__interrupt__" in paused_result
            assert call_count["file_editor"] == 0, "tool must NOT run while paused"

            events_at_pause = events_module.read_events(conversation_id)
            assert sum(1 for e in events_at_pause if e.type == "ask/requested") == 1
            assert sum(1 for e in events_at_pause if e.type == "ask/answered") == 0
            assert sum(1 for e in events_at_pause if e.type == "tool/result") == 0

            ask_requested = next(e for e in events_at_pause if e.type == "ask/requested")
            question = ask_requested.payload["questions"][0]
            assert question["related_task_id"] is not None, (
                "the approval question must carry the task_id for diff/approval linking"
            )

            # Resume with an approval.
            resumed_result = await graph.ainvoke(Command(resume=True), config)

            assert call_count["file_editor"] == 1, (
                "tool must run EXACTLY once after resume, not once per resume attempt"
            )
            assert resumed_result["next_node"] == "end"

            events_after_resume = events_module.read_events(conversation_id)
            assert sum(1 for e in events_after_resume if e.type == "ask/requested") == 1
            assert sum(1 for e in events_after_resume if e.type == "ask/answered") == 1
            assert sum(1 for e in events_after_resume if e.type == "tool/result") == 1
            assert sum(1 for e in events_after_resume if e.type == "task/diff_ready") == 1

            diff_ready = next(e for e in events_after_resume if e.type == "task/diff_ready")
            # tmp_path isn't a git repo, so capture_workspace_diff() falls
            # back to the best-effort shape -- but the new keys must still
            # be present (as None/empty, not missing) so a wire model
            # expecting them never has to special-case a KeyError.
            for key in ("additions", "deletions", "truncated", "files"):
                assert key in diff_ready.payload

            ask_answered = next(e for e in events_after_resume if e.type == "ask/answered")
            assert ask_answered.payload["answers"][0]["selected"] == ["approve"]
    finally:
        await graph.checkpointer.conn.close()


async def test_approval_rejection_skips_the_tool_and_returns_to_persona(tmp_path):
    conversation_id = "conv-reject-1"
    call_count = {"terminal": 0}

    async def fake_terminal(arguments: dict) -> ToolResult:
        call_count["terminal"] += 1
        return ToolResult(text="ran it", is_error=False)

    propose = _tool_call_response("Running a command.", "terminal", {"command": "rm -rf /"})
    final = _plain_response("Understood, I won't run that.")
    call_model_mock = AsyncMock(side_effect=[propose, final])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock), patch.dict(
            build.TOOL_REGISTRY, {"terminal": fake_terminal}
        ):
            state = build.new_state(conversation_id, "rex")
            await graph.ainvoke(state, config)

            result = await graph.ainvoke(Command(resume=False), config)

            assert call_count["terminal"] == 0, "rejected tool must never run"
            assert result["next_node"] == "end"

            logged = events_module.read_events(conversation_id)
            ask_answered = next(e for e in logged if e.type == "ask/answered")
            assert ask_answered.payload["answers"][0]["selected"] == ["reject"]
            assert not any(e.type == "tool/result" for e in logged)
    finally:
        await graph.checkpointer.conn.close()


# ---------------------------------------------------------------------------
# No-approval tool path (read-only tools skip the interrupt gate entirely)
# ---------------------------------------------------------------------------


async def test_read_only_tool_executes_without_pausing(tmp_path):
    conversation_id = "conv-readonly-1"

    propose = _tool_call_response(
        "Let me check the skill catalog.", "skill_loader", {"name": "test-driven-development"}
    )
    final = _plain_response("Got it, thanks.")
    call_model_mock = AsyncMock(side_effect=[propose, final])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")
            result = await graph.ainvoke(state, config)

        assert "__interrupt__" not in result
        assert result["next_node"] == "end"

        logged = events_module.read_events(conversation_id)
        assert not any(e.type == "ask/requested" for e in logged)
        tool_result = next(e for e in logged if e.type == "tool/result")
        # Not just "an event exists" -- the skill actually loaded
        # successfully (skill_loader's real file lookup against the real
        # skills/ dir succeeded), not merely reached the log with an error.
        assert tool_result.payload["is_error"] is False
    finally:
        await graph.checkpointer.conn.close()


# ---------------------------------------------------------------------------
# Turn budget
# ---------------------------------------------------------------------------


async def test_turn_budget_exceeded_raises_before_any_side_effect(tmp_path):
    conversation_id = "conv-budget-1"
    state = build.new_state(conversation_id, "rex")
    state["turn_count"] = 10  # DEFAULT_MAX_TURNS

    with pytest.raises(TurnBudgetExceeded):
        await build.persona_node(state, {"configurable": {"thread_id": conversation_id}})

    # Never got far enough to write anything -- the check runs before any
    # event-log write.
    assert events_module.read_events(conversation_id) == []


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------


async def test_delegation_switches_active_persona_and_increments_depth(tmp_path):
    conversation_id = "conv-delegate-1"

    ada_delegates = _tool_call_response(
        "I'll hand this to Rex.", "delegate", {"to_persona": "rex", "text": "please implement this"}
    )
    rex_replies = _plain_response("On it.")
    call_model_mock = AsyncMock(side_effect=[ada_delegates, rex_replies])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "ada")
            result = await graph.ainvoke(state, config)

        assert result["persona_id"] == "rex"
        assert result["delegation_depth"] == 1
        assert result["next_node"] == "end"

        logged = events_module.read_events(conversation_id)
        delegation_events = [e for e in logged if e.type == "delegation/sent"]
        assert len(delegation_events) == 1
        assert delegation_events[0].payload["from_persona"] == "ada"
        assert delegation_events[0].payload["to_persona"] == "rex"
    finally:
        await graph.checkpointer.conn.close()


async def test_delegation_depth_exceeded_closes_the_turn_then_raises(tmp_path):
    conversation_id = "conv-delegate-depth-1"

    ada_delegates = _tool_call_response(
        "Delegating again.", "delegate", {"to_persona": "rex", "text": "go"}
    )
    call_model_mock = AsyncMock(return_value=ada_delegates)

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "ada")
            state["delegation_depth"] = 3  # DEFAULT_MAX_DELEGATION_DEPTH

            with pytest.raises(DelegationDepthExceeded):
                await graph.ainvoke(state, config)

        logged = events_module.read_events(conversation_id)
        turn_end = next(e for e in logged if e.type == "turn/end")
        # Never "interrupted" -- that reason is reserved exclusively for
        # events.close_orphaned_turns's crash-recovery repair path.
        assert turn_end.payload["reason"] != "interrupted"
        assert "delegation_budget_exceeded" in turn_end.payload["reason"]
    finally:
        await graph.checkpointer.conn.close()


async def test_delegation_to_unknown_persona_gives_feedback_instead_of_crashing(tmp_path):
    conversation_id = "conv-delegate-unknown-1"

    bad_delegate = _tool_call_response(
        "Delegating to a persona that doesn't exist.",
        "delegate",
        {"to_persona": "nonexistent", "text": "go"},
    )
    recover = _plain_response("Never mind, I'll do it myself.")
    call_model_mock = AsyncMock(side_effect=[bad_delegate, recover])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "ada")
            result = await graph.ainvoke(state, config)

        assert result["persona_id"] == "ada"  # never switched
        assert result["next_node"] == "end"
    finally:
        await graph.checkpointer.conn.close()


async def test_delegation_to_a_paused_persona_gives_feedback_instead_of_running_it(tmp_path):
    """tapestry_mentions_concurrency_status_spec.md §4/§5 decision 2's
    gating has to cover delegation too, not just the human-facing
    send_message/on_message front doors -- nova.yaml ships `status:
    paused` deliberately (her own system_prompt requires explicit human
    activation before any action), and delegation is a separate way an
    arbitrary persona's turn can run with none of those front doors
    involved. Same "feedback instead of crashing" shape as
    `test_delegation_to_unknown_persona_gives_feedback_instead_of_crashing`.
    """
    conversation_id = "conv-delegate-paused-1"
    assert build.PERSONAS["nova"].status == "paused", "nova must ship paused by default"

    delegate_to_paused = _tool_call_response(
        "Delegating the deploy to Nova.",
        "delegate",
        {"to_persona": "nova", "text": "please deploy"},
    )
    recover = _plain_response("Nova's paused -- I'll flag this for a human instead.")
    call_model_mock = AsyncMock(side_effect=[delegate_to_paused, recover])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")
            result = await graph.ainvoke(state, config)

        assert result["persona_id"] == "rex"  # never switched to nova
        assert result["next_node"] == "end"
        logged = events_module.read_events(conversation_id)
        assert not any(e.type == "delegation/sent" for e in logged), (
            "the delegation must never actually have been sent"
        )
    finally:
        await graph.checkpointer.conn.close()


# ---------------------------------------------------------------------------
# task_complete + verify integration
# ---------------------------------------------------------------------------


async def test_task_complete_passing_verification_emits_task_completed(tmp_path):
    conversation_id = "conv-complete-pass-1"

    declares_done = _tool_call_response(
        "I'm finished.", "task_complete", {"summary": "implemented the feature"}
    )
    call_model_mock = AsyncMock(return_value=declares_done)

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock), patch.object(
            build.verify,
            "verify_before_completion",
            AsyncMock(return_value=VerificationResult(passed=True, notes="all four checks passed")),
        ):
            state = build.new_state(conversation_id, "rex")
            result = await graph.ainvoke(state, config)

        assert result["next_node"] == "end"
        logged = events_module.read_events(conversation_id)
        completed = next(e for e in logged if e.type == "task/completed")
        assert "all four checks passed" in completed.payload["notes"]
        turn_end = next(e for e in logged if e.type == "turn/end")
        assert turn_end.payload["reason"] == "task_completed"
    finally:
        await graph.checkpointer.conn.close()


async def test_task_complete_failing_verification_loops_back_to_persona(tmp_path):
    conversation_id = "conv-complete-fail-1"

    declares_done = _tool_call_response(
        "I'm finished.", "task_complete", {"summary": "implemented the feature"}
    )
    tries_again = _plain_response("You're right, let me fix that.")
    call_model_mock = AsyncMock(side_effect=[declares_done, tries_again])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock), patch.object(
            build.verify,
            "verify_before_completion",
            AsyncMock(
                return_value=VerificationResult(passed=False, notes="tests were never actually run")
            ),
        ):
            state = build.new_state(conversation_id, "rex")
            result = await graph.ainvoke(state, config)

        # Looped back through persona a second time and then ended normally
        # (not via task/completed).
        assert result["next_node"] == "end"
        logged = events_module.read_events(conversation_id)
        assert any(e.type == "task/verification_failed" for e in logged)
        assert not any(e.type == "task/completed" for e in logged)
        assert call_model_mock.await_count == 2

        # Found live (2026-09-03, a companion persona's DeepSeek call
        # 400'd): the retry's own message list must satisfy every
        # OpenAI-compatible provider's own contract -- an assistant message
        # with tool_calls has to be immediately followed by a tool-role
        # message carrying a matching tool_call_id, never a bare "user"
        # message, or the SECOND call_model invocation itself is malformed
        # and the provider rejects the whole request.
        second_call_messages = call_model_mock.await_args_list[1].kwargs["messages"]
        tool_call_message = next(m for m in second_call_messages if m.get("tool_calls"))
        index = second_call_messages.index(tool_call_message)
        feedback_message = second_call_messages[index + 1]
        assert feedback_message["role"] == "tool"
        assert feedback_message["tool_call_id"] == tool_call_message["tool_calls"][0]["id"]
        assert "tests were never actually run" in feedback_message["content"]
    finally:
        await graph.checkpointer.conn.close()


# ---------------------------------------------------------------------------
# Thread-id plumbing
# ---------------------------------------------------------------------------


async def test_thread_id_is_carried_onto_every_appended_event(tmp_path):
    conversation_id = "conv-thread-1"

    call_model_mock = AsyncMock(return_value=_plain_response("hi from a thread"))

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex", thread_id="thread-abc")
            await graph.ainvoke(state, config)

        logged = events_module.read_events(conversation_id)
        assistant_message = next(e for e in logged if e.type == "assistant/message")
        assert assistant_message.payload["thread_id"] == "thread-abc"
    finally:
        await graph.checkpointer.conn.close()


# ---------------------------------------------------------------------------
# pass_turn -- round-continuation's opt-out (web_adapter/api.py's
# _run_continuation_session is what actually offers this each round after
# the first; this covers the tool's own effect on the event log/graph).
# ---------------------------------------------------------------------------


async def test_pass_turn_logs_a_pass_not_a_message_and_ends_the_turn(tmp_path):
    conversation_id = "conv-pass-1"
    passes = _tool_call_response("", "pass_turn", {})
    call_model_mock = AsyncMock(return_value=passes)

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex", is_continuation_round=True)
            result = await graph.ainvoke(state, config)

        assert result["next_node"] == "end"
        logged = events_module.read_events(conversation_id)
        types = [e.type for e in logged]
        assert "persona/passed" in types
        # A pass has nothing for a human to read as a chat bubble -- must
        # never be projected as one (see core.conversations.derive_messages'
        # own "/message"-suffix check).
        assert "assistant/message" not in types
        turn_end = next(e for e in logged if e.type == "turn/end")
        assert turn_end.payload["reason"] == "passed"
    finally:
        await graph.checkpointer.conn.close()


async def test_pass_turn_is_not_offered_outside_a_continuation_round(tmp_path):
    """Round 1 (the mandatory reply to being tagged) must never see
    pass_turn as an available tool at all -- confirmed here by asserting
    the model was never even given the option, not just that it didn't
    take it.
    """
    conversation_id = "conv-pass-2"
    call_model_mock = AsyncMock(return_value=_plain_response("hi"))

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")  # is_continuation_round defaults False
            await graph.ainvoke(state, config)

        offered_tools = call_model_mock.call_args.kwargs["tools"]
        names = {t["function"]["name"] for t in offered_tools}
        assert build.PASS_TURN_TOOL_NAME not in names
    finally:
        await graph.checkpointer.conn.close()


# ---------------------------------------------------------------------------
# Proactive check-in -- graph-level: does the ONE turn is_proactive_checkin
# spawns actually mark its own message so web_adapter/api.py's eligibility
# check (_proactive_checkin_targets) can tell a nudge apart from a normal
# reply. See that function's own docstring for why the distinction exists.
# ---------------------------------------------------------------------------


async def test_proactive_checkin_reply_is_marked_so_it_wont_be_re_nudged(tmp_path):
    conversation_id = "conv-proactive-1"
    call_model_mock = AsyncMock(return_value=_plain_response("hey, thinking of you today"))

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex", is_proactive_checkin=True)
            await graph.ainvoke(state, config)

        logged = events_module.read_events(conversation_id)
        message = next(e for e in logged if e.type == "assistant/message")
        assert message.payload["proactive"] is True
    finally:
        await graph.checkpointer.conn.close()


async def test_ordinary_reply_is_not_marked_proactive(tmp_path):
    conversation_id = "conv-proactive-2"
    call_model_mock = AsyncMock(return_value=_plain_response("hi there"))

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")  # is_proactive_checkin defaults False
            await graph.ainvoke(state, config)

        logged = events_module.read_events(conversation_id)
        message = next(e for e in logged if e.type == "assistant/message")
        assert "proactive" not in message.payload
    finally:
        await graph.checkpointer.conn.close()


async def test_proactive_checkin_appends_a_synthetic_trailing_turn(tmp_path):
    """Live-tested failure: a DM's own history, from the persona's own
    point of view, ends with HER prior reply (role "assistant") -- unlike
    a continuation round, where a sibling's message remaps to role "user"
    (_chat_messages_from_log), a DM has nobody else to remap. Calling the
    model on a list ending in "assistant" with nothing after it produced a
    real litellm.EmptyResponseError (3/3 empty completions) for one model.
    This proves the fix: a synthetic trailing "user"-role turn is appended
    so the shape stays a normal, alternating conversation.
    """
    conversation_id = "conv-proactive-3"
    events_module.append_event(
        conversation_id, "user/message", actor="you", payload={"text": "hey"}
    )
    events_module.append_event(
        conversation_id, "assistant/message", actor="rex", payload={"text": "hi there"}
    )
    call_model_mock = AsyncMock(return_value=_plain_response("thinking of you"))

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex", is_proactive_checkin=True)
            await graph.ainvoke(state, config)

        sent_messages = call_model_mock.call_args.kwargs["messages"]
        assert sent_messages[-1]["role"] == "user"
        assert sent_messages[-2]["role"] == "assistant"  # rex's own prior reply, unchanged
    finally:
        await graph.checkpointer.conn.close()
