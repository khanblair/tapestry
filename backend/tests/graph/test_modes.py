"""Tests for modes, guardian-gated Auto, and model switching --
tapestry_modes_models_personas_spec.md's routing design in graph/build.py.

Same conventions as test_build.py: `call_model` is always mocked (no real
API calls), everything else -- the event log, a real checkpointed graph via
`_run_graph`, real `interrupt()`/resume mechanics -- is exercised for real.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.types import Command

from tapestry.core import events as events_module
from tapestry.core.personas import Persona
from tapestry.graph import build
from tapestry.graph.budgets import TurnBudgetExceeded
from tapestry.models.litellm_client import ModelResponse
from tapestry.tools.file_editor import ToolResult


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _plain_response(text: str, model_used: str = "test-model") -> ModelResponse:
    return ModelResponse(text=text, tool_calls=None, model_used=model_used)


def _tool_call_response(
    text: str, name: str, arguments: dict, call_id: str = "call_1", model_used: str = "test-model"
) -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=[_tool_call(name, arguments, call_id)],
        model_used=model_used,
    )


async def _run_graph(tmp_path, conversation_id: str):
    checkpoint_path = str(tmp_path / f"{conversation_id}.sqlite")
    graph = await build.build_graph(checkpoint_path)
    config = {"configurable": {"thread_id": conversation_id}}
    return graph, config


def _persona(**overrides) -> Persona:
    """A throwaway persona for pure unit tests -- not one of the real
    personas/*.yaml personas, so tests here never depend on their content
    changing out from under them.
    """
    fields = dict(
        id="test-persona",
        name="Test",
        role="Tester",
        model="primary-model",
        system_prompt="You are a test persona.",
        tools=["file_editor", "terminal", "git", "deploy_pipeline"],
        mcp_servers=[],
        status="online",
        color="#000000",
    )
    fields.update(overrides)
    return Persona(**fields)


# ---------------------------------------------------------------------------
# Pure unit tests: _effective_tools, _resolve_mode, _resolve_model
# ---------------------------------------------------------------------------


class TestEffectiveTools:
    def test_non_plan_modes_pass_through_unchanged(self):
        tools = ["file_editor", "terminal", "git"]
        for mode in ("manual", "accept_edits", "auto", "bypass"):
            assert build._effective_tools(tools, mode) == tools

    def test_plan_mode_intersects_down_to_safe_tier(self):
        tools = ["file_editor", "terminal", "git", "deploy_pipeline", "terminal_read_only"]
        assert build._effective_tools(tools, "plan") == ["terminal_read_only"]

    def test_plan_mode_on_an_already_read_only_persona_is_a_no_op(self):
        # Ada's real toolset -- already a subset of the safe tier, so Plan
        # mode must not remove anything further from it (spec §1.4).
        ada_tools = build.PERSONAS["ada"].tools
        assert build._effective_tools(ada_tools, "plan") == ada_tools


class TestResolveMode:
    def test_falls_back_to_persona_default_mode_with_no_events(self):
        persona = _persona(default_mode="accept_edits")
        assert build._resolve_mode("conv-modes-1", persona) == "accept_edits"

    def test_reads_the_most_recent_mode_changed_event(self):
        persona = _persona(id="p1", default_mode="manual")
        events_module.append_event(
            "conv-modes-2", "mode/changed", "system", {"mode": "bypass", "persona_id": "p1"}
        )
        events_module.append_event(
            "conv-modes-2", "mode/changed", "system", {"mode": "auto", "persona_id": "p1"}
        )
        assert build._resolve_mode("conv-modes-2", persona) == "auto"

    def test_is_scoped_to_the_persona_id(self):
        persona = _persona(id="p1", default_mode="manual")
        events_module.append_event(
            "conv-modes-3", "mode/changed", "system", {"mode": "bypass", "persona_id": "someone-else"}
        )
        assert build._resolve_mode("conv-modes-3", persona) == "manual"

    def test_ignores_an_invalid_mode_value(self):
        persona = _persona(id="p1", default_mode="manual")
        events_module.append_event(
            "conv-modes-4", "mode/changed", "system", {"mode": "not-a-real-mode", "persona_id": "p1"}
        )
        assert build._resolve_mode("conv-modes-4", persona) == "manual"


class TestResolveModel:
    def test_falls_back_to_persona_model_with_nothing_set(self):
        persona = _persona(model="global-default")
        state = build.new_state("conv-models-1", persona.id)
        model, consumed = build._resolve_model(state, "conv-models-1", persona)
        assert model == "global-default"
        assert consumed is False

    def test_session_scope_event_overrides_persona_model(self):
        persona = _persona(id="p1", model="global-default")
        events_module.append_event(
            "conv-models-2", "persona/model_switched", "you", {"model": "session-model", "persona_id": "p1"}
        )
        state = build.new_state("conv-models-2", persona.id)
        model, consumed = build._resolve_model(state, "conv-models-2", persona)
        assert model == "session-model"
        assert consumed is False

    def test_once_scope_state_wins_over_session_scope_event(self):
        persona = _persona(id="p1", model="global-default")
        events_module.append_event(
            "conv-models-3", "persona/model_switched", "you", {"model": "session-model", "persona_id": "p1"}
        )
        state = build.new_state("conv-models-3", persona.id)
        state["model_override_once"] = "once-model"
        model, consumed = build._resolve_model(state, "conv-models-3", persona)
        assert model == "once-model"
        assert consumed is True

    def test_session_scope_is_isolated_per_persona(self):
        persona = _persona(id="p1", model="global-default")
        events_module.append_event(
            "conv-models-4", "persona/model_switched", "you", {"model": "session-model", "persona_id": "someone-else"}
        )
        state = build.new_state("conv-models-4", persona.id)
        model, _ = build._resolve_model(state, "conv-models-4", persona)
        assert model == "global-default"


class TestModeRequiresApproval:
    @pytest.mark.parametrize(
        "tier,expected",
        [("edit", True), ("mutate", True), ("deploy", True)],
    )
    async def test_manual_always_asks(self, tier, expected):
        persona = _persona()
        assert await build._mode_requires_approval("manual", tier, persona, "t", {}) is expected

    @pytest.mark.parametrize(
        "tier,expected",
        [("edit", False), ("mutate", False), ("deploy", False)],
    )
    async def test_bypass_never_asks(self, tier, expected):
        persona = _persona()
        assert await build._mode_requires_approval("bypass", tier, persona, "t", {}) is expected

    async def test_accept_edits_only_auto_approves_edit_tier(self):
        persona = _persona()
        assert await build._mode_requires_approval("accept_edits", "edit", persona, "t", {}) is False
        assert await build._mode_requires_approval("accept_edits", "mutate", persona, "t", {}) is True
        assert await build._mode_requires_approval("accept_edits", "deploy", persona, "t", {}) is True

    async def test_auto_deploy_tier_always_asks_without_calling_the_guardian(self):
        persona = _persona()
        with patch.object(build, "_guardian_review", AsyncMock()) as guardian:
            result = await build._mode_requires_approval("auto", "deploy", persona, "t", {})
        assert result is True
        guardian.assert_not_called()

    async def test_auto_edit_and_mutate_tiers_are_guardian_screened(self):
        persona = _persona()
        with patch.object(build, "_guardian_review", AsyncMock(return_value="approve")):
            assert await build._mode_requires_approval("auto", "edit", persona, "t", {}) is False
            assert await build._mode_requires_approval("auto", "mutate", persona, "t", {}) is False

        with patch.object(build, "_guardian_review", AsyncMock(return_value="escalate")):
            assert await build._mode_requires_approval("auto", "edit", persona, "t", {}) is True


class TestGuardianReview:
    async def test_fails_closed_with_no_guardian_model_configured(self, monkeypatch):
        monkeypatch.delenv(build._GUARDIAN_MODEL_ENV_VAR, raising=False)
        persona = _persona(guardian_model=None)
        assert await build._guardian_review(persona, "file_editor", {}) == "escalate"

    async def test_fails_closed_when_the_guardian_call_raises(self, monkeypatch):
        persona = _persona(guardian_model="guardian-model")
        with patch.object(build, "call_model", AsyncMock(side_effect=RuntimeError("boom"))):
            assert await build._guardian_review(persona, "file_editor", {}) == "escalate"

    async def test_approve_response_is_recognized(self):
        persona = _persona(guardian_model="guardian-model")
        with patch.object(build, "call_model", AsyncMock(return_value=_plain_response("APPROVE"))):
            assert await build._guardian_review(persona, "file_editor", {}) == "approve"

    async def test_anything_other_than_approve_escalates(self):
        persona = _persona(guardian_model="guardian-model")
        with patch.object(
            build, "call_model", AsyncMock(return_value=_plain_response("unsure, maybe?"))
        ):
            assert await build._guardian_review(persona, "file_editor", {}) == "escalate"

    async def test_falls_back_to_env_var_when_persona_has_no_guardian_model(self, monkeypatch):
        monkeypatch.setenv(build._GUARDIAN_MODEL_ENV_VAR, "env-guardian-model")
        persona = _persona(guardian_model=None)
        mock = AsyncMock(return_value=_plain_response("APPROVE"))
        with patch.object(build, "call_model", mock):
            await build._guardian_review(persona, "file_editor", {})
        assert mock.await_args.kwargs["model"] == "env-guardian-model"


# ---------------------------------------------------------------------------
# Integration tests: real graph execution
# ---------------------------------------------------------------------------


async def test_bypass_mode_executes_a_gated_tool_with_no_approval_prompt(tmp_path):
    conversation_id = "conv-bypass-1"
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
            state = build.new_state(conversation_id, "rex")
            result = await graph.ainvoke(state, config)

        assert "__interrupt__" not in result
        assert call_count["file_editor"] == 1

        logged = events_module.read_events(conversation_id)
        assert sum(1 for e in logged if e.type == "ask/requested") == 0
        assert sum(1 for e in logged if e.type == "mode/auto_approved") == 1
        auto_approved = next(e for e in logged if e.type == "mode/auto_approved")
        assert auto_approved.payload["tool_name"] == "file_editor"
        assert auto_approved.payload["mode"] == "bypass"
    finally:
        await graph.checkpointer.conn.close()


async def test_accept_edits_mode_auto_runs_file_editor_but_still_gates_terminal(tmp_path):
    conversation_id = "conv-accept-edits-1"
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
            conversation_id, "mode/changed", "you", {"mode": "accept_edits", "persona_id": "rex"}
        )
        with patch.object(build, "call_model", call_model_mock), patch.dict(
            build.TOOL_REGISTRY, {"file_editor": fake_file_editor}
        ):
            state = build.new_state(conversation_id, "rex")
            result = await graph.ainvoke(state, config)

        assert "__interrupt__" not in result
        assert call_count["file_editor"] == 1
        logged = events_module.read_events(conversation_id)
        assert sum(1 for e in logged if e.type == "ask/requested") == 0
    finally:
        await graph.checkpointer.conn.close()

    # A second, fresh conversation: same mode, but proposing `terminal`
    # (mutate tier, not edit tier) -- must still pause for approval.
    conversation_id_2 = "conv-accept-edits-2"
    propose_terminal = _tool_call_response("Running.", "terminal", {"command": "ls"})
    call_model_mock_2 = AsyncMock(return_value=propose_terminal)

    graph_2, config_2 = await _run_graph(tmp_path, conversation_id_2)
    try:
        events_module.append_event(
            conversation_id_2, "mode/changed", "you", {"mode": "accept_edits", "persona_id": "rex"}
        )
        with patch.object(build, "call_model", call_model_mock_2):
            state = build.new_state(conversation_id_2, "rex")
            result = await graph_2.ainvoke(state, config_2)

        assert "__interrupt__" in result
    finally:
        await graph_2.checkpointer.conn.close()


async def test_auto_mode_guardian_approve_skips_the_human_prompt(tmp_path):
    conversation_id = "conv-auto-approve-1"
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
            conversation_id, "mode/changed", "you", {"mode": "auto", "persona_id": "rex"}
        )
        with patch.object(build, "call_model", call_model_mock), patch.object(
            build, "_guardian_review", AsyncMock(return_value="approve")
        ), patch.dict(build.TOOL_REGISTRY, {"file_editor": fake_file_editor}):
            state = build.new_state(conversation_id, "rex")
            result = await graph.ainvoke(state, config)

        assert "__interrupt__" not in result
        assert call_count["file_editor"] == 1
        logged = events_module.read_events(conversation_id)
        assert sum(1 for e in logged if e.type == "ask/requested") == 0
        assert sum(1 for e in logged if e.type == "mode/auto_approved") == 1
    finally:
        await graph.checkpointer.conn.close()


async def test_auto_mode_guardian_escalate_still_pauses_for_a_human(tmp_path):
    conversation_id = "conv-auto-escalate-1"
    propose = _tool_call_response("Creating.", "file_editor", {"command": "create", "path": "/tmp/x.txt"})
    call_model_mock = AsyncMock(return_value=propose)

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        events_module.append_event(
            conversation_id, "mode/changed", "you", {"mode": "auto", "persona_id": "rex"}
        )
        with patch.object(build, "call_model", call_model_mock), patch.object(
            build, "_guardian_review", AsyncMock(return_value="escalate")
        ):
            state = build.new_state(conversation_id, "rex")
            result = await graph.ainvoke(state, config)

        assert "__interrupt__" in result
        logged = events_module.read_events(conversation_id)
        assert sum(1 for e in logged if e.type == "ask/requested") == 1
    finally:
        await graph.checkpointer.conn.close()


async def test_auto_mode_deploy_tier_always_pauses_even_with_a_permissive_guardian(tmp_path):
    conversation_id = "conv-auto-deploy-1"
    propose = _tool_call_response("Deploying.", "deploy_pipeline", {"env": "prod"})
    call_model_mock = AsyncMock(return_value=propose)

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        events_module.append_event(
            conversation_id, "mode/changed", "you", {"mode": "auto", "persona_id": "nova"}
        )
        # A guardian that would approve ANYTHING -- must never even be
        # consulted for deploy tier.
        with patch.object(build, "call_model", call_model_mock), patch.object(
            build, "_guardian_review", AsyncMock(return_value="approve")
        ) as guardian:
            state = build.new_state(conversation_id, "nova")
            result = await graph.ainvoke(state, config)

        assert "__interrupt__" in result
        guardian.assert_not_called()
    finally:
        await graph.checkpointer.conn.close()


async def test_plan_mode_never_offers_mutating_tools_to_the_model(tmp_path):
    conversation_id = "conv-plan-1"
    captured_tool_names = []

    async def call_model_capturing_tools(model, messages, tools, **kwargs):
        captured_tool_names.append({t["function"]["name"] for t in (tools or [])})
        return _plain_response("Just looking around.")

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        events_module.append_event(
            conversation_id, "mode/changed", "you", {"mode": "plan", "persona_id": "rex"}
        )
        with patch.object(build, "call_model", call_model_capturing_tools):
            state = build.new_state(conversation_id, "rex")
            await graph.ainvoke(state, config)

        offered = captured_tool_names[0]
        # Rex's real toolset includes file_editor/terminal/git -- none of
        # those may be offered while Plan mode is active.
        assert "file_editor" not in offered
        assert "terminal" not in offered
        assert "git" not in offered
        # Core orchestration primitives are unaffected.
        assert build.DELEGATE_TOOL_NAME in offered
        assert build.TASK_COMPLETE_TOOL_NAME in offered
        assert build.SKILL_LOADER_TOOL_NAME in offered
    finally:
        await graph.checkpointer.conn.close()


async def test_plan_mode_rejects_a_mutating_tool_call_even_if_the_model_proposes_it_anyway(tmp_path):
    """Defense in depth: even if a model hallucinates a tool call outside
    what it was offered, persona_node's own permission check (now against
    the mode-filtered effective_tools, not the persona's raw tools list)
    must still refuse it -- not silently execute it.
    """
    conversation_id = "conv-plan-2"
    call_count = {"file_editor": 0}

    async def fake_file_editor(arguments: dict) -> ToolResult:
        call_count["file_editor"] += 1
        return ToolResult(text="should never run", is_error=False)

    propose = _tool_call_response("I'll just edit it anyway.", "file_editor", {"command": "create", "path": "/tmp/x.txt"})
    # Second call gives up after seeing its own tool call was refused, so
    # the graph reaches a real terminal state in one ainvoke instead of
    # looping on the same rejected proposal until the turn budget trips --
    # that loop-until-budget behavior is real and correct (the model is
    # free to try again), just not what this test is about.
    give_up = _plain_response("Understood, I can't do that in this mode.")
    call_model_mock = AsyncMock(side_effect=[propose, give_up])

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        events_module.append_event(
            conversation_id, "mode/changed", "you", {"mode": "plan", "persona_id": "rex"}
        )
        with patch.object(build, "call_model", call_model_mock), patch.dict(
            build.TOOL_REGISTRY, {"file_editor": fake_file_editor}
        ):
            state = build.new_state(conversation_id, "rex")
            result = await graph.ainvoke(state, config)

        assert call_count["file_editor"] == 0
        assert result["next_node"] == "end"
        logged = events_module.read_events(conversation_id)
        assert sum(1 for e in logged if e.type == "tool/result") == 0
        assert sum(1 for e in logged if e.type == "ask/requested") == 0
    finally:
        await graph.checkpointer.conn.close()


async def test_model_override_once_is_used_for_exactly_one_pass(tmp_path):
    conversation_id = "conv-once-1"
    seen_models = []

    async def call_model_capturing_model(model, messages, tools, **kwargs):
        seen_models.append(model)
        if len(seen_models) == 1:
            return _tool_call_response("Checking skills.", build.SKILL_LOADER_TOOL_NAME, {"name": "does-not-exist"})
        return _plain_response("Second turn.")

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_capturing_model):
            state = build.new_state(conversation_id, "rex")
            state["model_override_once"] = "once-only-model"
            result = await graph.ainvoke(state, config)
            # First pass used the once-override; the graph loops back to
            # persona_node (skill_loader with a bogus name is a normal
            # tool-result round trip, not a terminal state) and a second
            # persona_node pass must NOT see the override anymore.
            assert seen_models[0] == "once-only-model"
            assert result["next_node"] in ("end", "persona")
            if result["next_node"] == "persona":
                await graph.ainvoke(result, config)
        assert seen_models[1] == build.PERSONAS["rex"].model
    finally:
        await graph.checkpointer.conn.close()


async def test_session_scope_model_switch_is_used_for_the_whole_conversation(tmp_path):
    conversation_id = "conv-session-model-1"
    events_module.append_event(
        conversation_id, "persona/model_switched", "you", {"model": "session-model", "persona_id": "rex"}
    )
    seen_models = []

    async def call_model_capturing_model(model, messages, tools, **kwargs):
        seen_models.append(model)
        return _plain_response("ok")

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_capturing_model):
            state = build.new_state(conversation_id, "rex")
            await graph.ainvoke(state, config)
        assert seen_models == ["session-model"]
    finally:
        await graph.checkpointer.conn.close()


async def test_model_fallback_is_logged_when_a_different_model_answers(tmp_path):
    conversation_id = "conv-fallback-1"
    response = _plain_response("answered by the fallback", model_used="the-fallback-model")
    call_model_mock = AsyncMock(return_value=response)

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")
            await graph.ainvoke(state, config)

        logged = events_module.read_events(conversation_id)
        fallback_events = [e for e in logged if e.type == "model/fallback"]
        assert len(fallback_events) == 1
        assert fallback_events[0].payload["to_model"] == "the-fallback-model"
        assert fallback_events[0].payload["from_model"] == build.PERSONAS["rex"].model
    finally:
        await graph.checkpointer.conn.close()


async def test_no_fallback_event_when_the_requested_model_answers(tmp_path):
    conversation_id = "conv-fallback-2"
    rex_model = build.PERSONAS["rex"].model
    response = _plain_response("answered normally", model_used=rex_model)
    call_model_mock = AsyncMock(return_value=response)

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")
            await graph.ainvoke(state, config)

        logged = events_module.read_events(conversation_id)
        assert sum(1 for e in logged if e.type == "model/fallback") == 0
    finally:
        await graph.checkpointer.conn.close()


async def test_persona_max_turns_override_takes_effect(tmp_path, monkeypatch):
    """A persona with max_turns=1 must hit TurnBudgetExceeded after its
    first persona_node pass, well before the global DEFAULT_MAX_TURNS.
    """
    conversation_id = "conv-max-turns-1"

    limited_persona = build.PERSONAS["rex"].model_copy(update={"max_turns": 1})
    monkeypatch.setitem(build.PERSONAS, "rex", limited_persona)

    # Always proposes a tool call the model isn't permitted to use, so the
    # graph loops back to persona_node (turn_count increments) without
    # ever reaching a terminal state on its own.
    propose = _tool_call_response("Trying.", "not-a-real-tool", {})
    call_model_mock = AsyncMock(return_value=propose)

    graph, config = await _run_graph(tmp_path, conversation_id)
    try:
        with patch.object(build, "call_model", call_model_mock):
            state = build.new_state(conversation_id, "rex")
            with pytest.raises(TurnBudgetExceeded):
                await graph.ainvoke(state, config)
    finally:
        await graph.checkpointer.conn.close()
