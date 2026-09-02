"""Tests for tapestry.graph.budgets -- the three separate budget mechanisms.

Exercises the REAL tapestry.core.events event log (via the autouse
in-memory-sqlite fixture in tests/graph/conftest.py), not a mock of it --
measure_conversation_cost's whole job is reading real logged events back.
"""

from __future__ import annotations

import pytest

from tapestry.core.delegation import DelegationRoundLimitExceeded as CoreDelegationRoundLimitExceeded
from tapestry.core.events import append_event
from tapestry.graph.budgets import (
    DEFAULT_MAX_DELEGATION_DEPTH,
    DEFAULT_MAX_TURNS,
    DelegationDepthExceeded,
    DelegationRoundLimitExceeded,
    TurnBudgetExceeded,
    check_delegation_depth,
    check_turn_budget,
    measure_conversation_cost,
)


class TestTurnBudget:
    def test_passes_under_cap(self):
        check_turn_budget(0, max_turns=3)
        check_turn_budget(2, max_turns=3)  # last turn still allowed

    def test_raises_at_cap(self):
        with pytest.raises(TurnBudgetExceeded):
            check_turn_budget(3, max_turns=3)

    def test_raises_over_cap(self):
        with pytest.raises(TurnBudgetExceeded):
            check_turn_budget(5, max_turns=3)

    def test_default_cap_matches_spec_example(self):
        assert DEFAULT_MAX_TURNS == 10
        check_turn_budget(9)  # last turn allowed under the default
        with pytest.raises(TurnBudgetExceeded):
            check_turn_budget(10)


class TestDelegationDepth:
    def test_passes_under_cap(self):
        check_delegation_depth(0, max_depth=3)
        check_delegation_depth(2, max_depth=3)

    def test_raises_at_cap(self):
        with pytest.raises(DelegationDepthExceeded):
            check_delegation_depth(3, max_depth=3)

    def test_default_cap_matches_spec_example(self):
        assert DEFAULT_MAX_DELEGATION_DEPTH == 3
        check_delegation_depth(2)
        with pytest.raises(DelegationDepthExceeded):
            check_delegation_depth(3)


def test_delegation_round_limit_exceeded_is_reexported_from_core_delegation():
    # budgets.py re-exports core.delegation's exception rather than
    # reimplementing it -- confirm it's literally the same class, not a
    # lookalike, so `except budgets.DelegationRoundLimitExceeded` actually
    # catches what core.delegation.delegate() raises.
    assert DelegationRoundLimitExceeded is CoreDelegationRoundLimitExceeded


class TestMeasureConversationCost:
    def test_zero_when_no_events_logged(self):
        result = measure_conversation_cost("conv-empty")
        assert result == {"cost": 0.0, "input_tokens": 0, "output_tokens": 0, "call_count": 0}

    def test_sums_across_multiple_model_response_events(self):
        append_event(
            "conv-1", "model/response", "rex",
            {"cost": 0.01, "input_tokens": 100, "output_tokens": 50},
        )
        append_event(
            "conv-1", "model/response", "rex",
            {"cost": 0.02, "input_tokens": 200, "output_tokens": 75},
        )

        result = measure_conversation_cost("conv-1")
        assert result["cost"] == pytest.approx(0.03)
        assert result["input_tokens"] == 300
        assert result["output_tokens"] == 125
        assert result["call_count"] == 2

    def test_treats_none_fields_as_zero_not_a_crash(self):
        # Matches models.litellm_client.ModelResponse's own shape: cost/
        # input_tokens/output_tokens are all `| None` when a provider
        # doesn't report them.
        append_event(
            "conv-1", "model/response", "rex",
            {"cost": None, "input_tokens": None, "output_tokens": None},
        )
        append_event(
            "conv-1", "model/response", "rex",
            {"cost": 0.05, "input_tokens": 10, "output_tokens": 5},
        )

        result = measure_conversation_cost("conv-1")
        assert result["cost"] == pytest.approx(0.05)
        assert result["input_tokens"] == 10
        assert result["output_tokens"] == 5
        assert result["call_count"] == 2

    def test_ignores_non_model_response_events(self):
        append_event("conv-1", "model/response", "rex", {"cost": 1.0, "input_tokens": 1, "output_tokens": 1})
        append_event("conv-1", "assistant/message", "rex", {"text": "hi"})
        append_event("conv-1", "tool/result", "rex", {"text": "ran a tool"})

        result = measure_conversation_cost("conv-1")
        assert result["call_count"] == 1
        assert result["cost"] == pytest.approx(1.0)

    def test_scoped_per_conversation(self):
        append_event("conv-a", "model/response", "rex", {"cost": 1.0, "input_tokens": 1, "output_tokens": 1})
        append_event("conv-b", "model/response", "rex", {"cost": 2.0, "input_tokens": 2, "output_tokens": 2})

        result_a = measure_conversation_cost("conv-a")
        result_b = measure_conversation_cost("conv-b")
        assert result_a["cost"] == pytest.approx(1.0)
        assert result_b["cost"] == pytest.approx(2.0)

    def test_does_not_raise_or_gate_anything(self):
        # Per the module docstring: this is a pure measurement, never a
        # gate. Sanity check that a huge cost total still just comes back
        # as a number, nothing raised.
        append_event("conv-1", "model/response", "rex", {"cost": 999999.0, "input_tokens": 0, "output_tokens": 0})
        result = measure_conversation_cost("conv-1")
        assert result["cost"] == pytest.approx(999999.0)
