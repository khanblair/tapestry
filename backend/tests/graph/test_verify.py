"""Tests for tapestry.graph.verify -- self-verification before "done".

`models.litellm_client.call_model` is mocked (no real API calls); the
event log is real (via tests/graph/conftest.py's in-memory sqlite fixture)
so "re-read your own diff/output" evidence-gathering is exercised against
actual logged events, not a fake.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from tapestry.core.events import append_event
from tapestry.core.personas import Persona
from tapestry.graph.verify import MAX_RECENT_EVENTS, VerificationResult, verify_before_completion
from tapestry.models.litellm_client import ModelResponse

_PERSONA = Persona(
    id="rex",
    name="Rex",
    role="Developer",
    model="deepseek/deepseek-chat",
    system_prompt="You implement things.",
    tools=["file_editor", "terminal"],
    mcp_servers=[],
    status="online",
    color="#8B5CF6",
)


def _json_response(**fields) -> ModelResponse:
    payload = {
        "restated_ask": "fix the bug",
        "diff_review": "no issues found",
        "tests_run": True,
        "test_result": "42 passed",
        "acceptance_criteria_check": "all met",
        "passed": True,
        "notes": "looks good",
    }
    payload.update(fields)
    return ModelResponse(text=json.dumps(payload), tool_calls=None, model_used="test-model")


class TestPassingVerification:
    async def test_returns_passed_true_with_notes(self):
        with patch(
            "tapestry.graph.verify.call_model", new=AsyncMock(return_value=_json_response())
        ):
            result = await verify_before_completion("conv-1", "fix the bug", _PERSONA)

        assert isinstance(result, VerificationResult)
        assert result.passed is True
        assert "looks good" in result.notes
        assert "42 passed" in result.notes

    async def test_calls_model_with_personas_own_model(self):
        mock = AsyncMock(return_value=_json_response())
        with patch("tapestry.graph.verify.call_model", new=mock):
            await verify_before_completion("conv-1", "fix the bug", _PERSONA)

        assert mock.await_args.kwargs["model"] == "deepseek/deepseek-chat"

    async def test_feeds_recent_tool_result_events_into_the_prompt(self):
        append_event(
            "conv-1", "tool/result", "rex",
            {"tool_name": "file_editor", "text": "created foo.py", "is_error": False},
        )
        append_event(
            "conv-1", "task/diff_ready", "rex",
            {"task_id": "t1", "files_changed": ["foo.py"], "diff_summary": "+ def foo(): ..."},
        )

        mock = AsyncMock(return_value=_json_response())
        with patch("tapestry.graph.verify.call_model", new=mock):
            await verify_before_completion("conv-1", "add foo.py", _PERSONA)

        messages = mock.await_args.kwargs["messages"]
        user_message = next(m["content"] for m in messages if m["role"] == "user")
        assert "created foo.py" in user_message
        assert "foo.py" in user_message

    async def test_bounds_evidence_to_max_recent_events(self):
        for i in range(MAX_RECENT_EVENTS + 10):
            append_event("conv-1", "tool/result", "rex", {"text": f"result-{i}", "is_error": False})

        mock = AsyncMock(return_value=_json_response())
        with patch("tapestry.graph.verify.call_model", new=mock):
            await verify_before_completion("conv-1", "do many things", _PERSONA)

        messages = mock.await_args.kwargs["messages"]
        user_message = next(m["content"] for m in messages if m["role"] == "user")
        # The oldest events must be dropped, the most recent kept.
        assert "result-0" not in user_message
        assert f"result-{MAX_RECENT_EVENTS + 9}" in user_message


class TestFailingVerification:
    async def test_returns_passed_false_when_model_says_so(self):
        with patch(
            "tapestry.graph.verify.call_model",
            new=AsyncMock(return_value=_json_response(passed=False, notes="tests were never run")),
        ):
            result = await verify_before_completion("conv-1", "fix the bug", _PERSONA)

        assert result.passed is False
        assert "tests were never run" in result.notes


class TestFailsClosed:
    """The gate must never default to passed=True on a malformed response."""

    async def test_unparseable_text_fails_closed(self):
        bad_response = ModelResponse(text="Sure, looks good to me!", tool_calls=None, model_used="test-model")
        with patch("tapestry.graph.verify.call_model", new=AsyncMock(return_value=bad_response)):
            result = await verify_before_completion("conv-1", "fix the bug", _PERSONA)

        assert result.passed is False
        assert "Sure, looks good to me!" in result.notes

    async def test_json_missing_passed_key_fails_closed(self):
        payload = {"restated_ask": "fix it", "notes": "did something"}
        response = ModelResponse(text=json.dumps(payload), tool_calls=None, model_used="test-model")
        with patch("tapestry.graph.verify.call_model", new=AsyncMock(return_value=response)):
            result = await verify_before_completion("conv-1", "fix the bug", _PERSONA)

        assert result.passed is False

    async def test_passed_as_non_bool_fails_closed(self):
        payload = {"passed": "yes", "notes": "trust me"}
        response = ModelResponse(text=json.dumps(payload), tool_calls=None, model_used="test-model")
        with patch("tapestry.graph.verify.call_model", new=AsyncMock(return_value=response)):
            result = await verify_before_completion("conv-1", "fix the bug", _PERSONA)

        assert result.passed is False

    async def test_json_wrapped_in_prose_is_still_extracted(self):
        # Models occasionally ignore "no prose outside the JSON" -- the
        # best-effort {...}-span extraction should still recover it.
        payload = {"passed": True, "notes": "all good"}
        response = ModelResponse(
            text=f"Here is my verification:\n{json.dumps(payload)}\nHope that helps!",
            tool_calls=None,
            model_used="test-model",
        )
        with patch("tapestry.graph.verify.call_model", new=AsyncMock(return_value=response)):
            result = await verify_before_completion("conv-1", "fix the bug", _PERSONA)

        assert result.passed is True
        assert "all good" in result.notes
