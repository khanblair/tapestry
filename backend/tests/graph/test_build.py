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
from tapestry.tools.file_editor import ToolResult


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _plain_response(text: str) -> ModelResponse:
    return ModelResponse(text=text, tool_calls=None)


def _tool_call_response(text: str, name: str, arguments: dict, call_id: str = "call_1") -> ModelResponse:
    return ModelResponse(text=text, tool_calls=[_tool_call(name, arguments, call_id)])


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
    schemas = build._build_tool_schemas(ada)
    names = {s["function"]["name"] for s in schemas}
    assert build.SKILL_LOADER_TOOL_NAME in names
    assert build.DELEGATE_TOOL_NAME in names
    assert build.TASK_COMPLETE_TOOL_NAME in names
    # Ada's own permitted tools are included too.
    assert "file_editor_read" in names
    assert "terminal_read_only" in names
    # But NOT tools she isn't permitted (file_editor is Rex's, not hers).
    assert "file_editor" not in names


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
        await build.persona_node(state)

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
