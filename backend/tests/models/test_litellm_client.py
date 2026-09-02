"""Tests for tapestry.models.litellm_client -- the only place LiteLLM gets
called from. No real network calls: litellm.acompletion is always mocked.

Fake responses are built from litellm's own pydantic types
(litellm.types.utils.ModelResponse/Choices/Message/Usage/
ChatCompletionMessageToolCall) rather than bare MagicMocks, so the tests
exercise the same .model_dump()/._hidden_params/.usage shapes call_model
actually relies on.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from litellm.types.utils import (
    ChatCompletionMessageToolCall,
    Choices,
    Function,
    Message,
    ModelResponse as LiteLLMModelResponse,
    Usage,
)

from tapestry.models.litellm_client import (
    EmptyResponseError,
    TapestryContextWindowExceeded,
    call_model,
)


def _make_response(
    *,
    content: str | None = "",
    tool_calls: list[ChatCompletionMessageToolCall] | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    response_cost: float | None = None,
) -> LiteLLMModelResponse:
    response = LiteLLMModelResponse(
        id="chatcmpl-test",
        model="claude-sonnet-4-5-20250929",
        choices=[
            Choices(
                index=0,
                message=Message(
                    role="assistant", content=content, tool_calls=tool_calls
                ),
                finish_reason="stop",
            )
        ],
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )
    if response_cost is not None:
        response._hidden_params["response_cost"] = response_cost
    return response


class TestCallModelSuccess:
    async def test_extracts_text_cost_and_tokens(self):
        mock_response = _make_response(
            content="Hello, world!",
            prompt_tokens=42,
            completion_tokens=13,
            response_cost=0.00512,
        )

        with patch("litellm.acompletion", new=AsyncMock(return_value=mock_response)):
            result = await call_model(
                model="claude-sonnet-4-5-20250929",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert result.text == "Hello, world!"
        assert result.tool_calls is None
        assert result.cost == 0.00512
        assert result.input_tokens == 42
        assert result.output_tokens == 13
        assert result.raw is not None
        assert result.raw["model"] == "claude-sonnet-4-5-20250929"

    async def test_normalizes_tool_calls_to_dicts(self):
        tool_call = ChatCompletionMessageToolCall(
            id="call_abc123",
            type="function",
            function=Function(name="get_weather", arguments='{"city": "NYC"}'),
        )
        mock_response = _make_response(content="", tool_calls=[tool_call])

        with patch("litellm.acompletion", new=AsyncMock(return_value=mock_response)):
            result = await call_model(
                model="claude-sonnet-4-5-20250929",
                messages=[{"role": "user", "content": "weather?"}],
                tools=[{"type": "function", "function": {"name": "get_weather"}}],
            )

        assert result.tool_calls == [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city": "NYC"}'},
            }
        ]


class TestEmptyResponsePolicy:
    async def test_retries_then_raises_after_max_retries(self):
        empty_response = _make_response(content="", tool_calls=None)
        mock_acompletion = AsyncMock(return_value=empty_response)

        with patch("litellm.acompletion", new=mock_acompletion):
            with pytest.raises(EmptyResponseError):
                await call_model(
                    model="claude-sonnet-4-5-20250929",
                    messages=[{"role": "user", "content": "hi"}],
                    max_retries=2,
                )

        # max_retries=2 -> 1 initial attempt + 2 retries = 3 total calls.
        assert mock_acompletion.await_count == 3

    async def test_succeeds_after_retry(self):
        empty_response = _make_response(content="", tool_calls=None)
        good_response = _make_response(content="finally!", prompt_tokens=5, completion_tokens=2)
        mock_acompletion = AsyncMock(side_effect=[empty_response, good_response])

        with patch("litellm.acompletion", new=mock_acompletion):
            result = await call_model(
                model="claude-sonnet-4-5-20250929",
                messages=[{"role": "user", "content": "hi"}],
                max_retries=2,
            )

        assert result.text == "finally!"
        assert mock_acompletion.await_count == 2

    async def test_whitespace_free_empty_string_with_no_tool_calls_is_empty(self):
        # content="" (LiteLLM's actual empty-completion shape) and no
        # tool_calls at all -- must be treated as empty, not truthy.
        empty_response = _make_response(content="", tool_calls=None)
        mock_acompletion = AsyncMock(return_value=empty_response)

        with patch("litellm.acompletion", new=mock_acompletion):
            with pytest.raises(EmptyResponseError):
                await call_model(
                    model="claude-sonnet-4-5-20250929",
                    messages=[{"role": "user", "content": "hi"}],
                    max_retries=0,
                )

        assert mock_acompletion.await_count == 1


class TestContextWindowExceededPolicy:
    async def test_reraised_as_tapestry_exception(self):
        import litellm

        provider_error = litellm.ContextWindowExceededError(
            message="context window exceeded for model claude-sonnet-4-5-20250929",
            model="claude-sonnet-4-5-20250929",
            llm_provider="anthropic",
        )
        mock_acompletion = AsyncMock(side_effect=provider_error)

        with patch("litellm.acompletion", new=mock_acompletion):
            with pytest.raises(TapestryContextWindowExceeded) as exc_info:
                await call_model(
                    model="claude-sonnet-4-5-20250929",
                    messages=[{"role": "user", "content": "a" * 1_000_000}],
                )

        assert "context window exceeded" in str(exc_info.value)
        # Not retried -- exactly one call before the reclassified exception.
        assert mock_acompletion.await_count == 1
