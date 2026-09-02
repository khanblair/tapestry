"""Tests for tapestry.models.litellm_client.call_model_stream -- the
streaming counterpart to call_model covered by test_litellm_client.py.

Same no-real-network-calls discipline as test_litellm_client.py:
litellm.acompletion is always mocked, and fake streamed responses are built
from litellm's own pydantic types (ModelResponseStream/StreamingChoices/
Delta/ChatCompletionDeltaToolCall/Function/Usage) rather than bare
MagicMocks, so these tests exercise the same .choices[0].delta/.usage
shapes call_model_stream actually relies on.

litellm.acompletion(..., stream=True) returns an async iterator
(CustomStreamWrapper in real usage). Mocks below stand in for it with a
bare async generator function -- NOT a CustomStreamWrapper instance, since
call_model_stream never asserts isinstance against that type (it only
`async for`s over whatever acompletion returns).

IMPORTANT: an async generator is exhausted after one full iteration. Every
retry test below passes a *list* of freshly-constructed generator objects
via `side_effect=[...]`, one per expected litellm.acompletion call -- never
a single generator object reused via `return_value`, which would silently
make a retry test pass vacuously (subsequent "attempts" would see an
already-exhausted generator, not a fresh empty one).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import litellm
import pytest
from litellm.types.utils import (
    ChatCompletionDeltaToolCall,
    Delta,
    Function,
    ModelResponseStream,
    StreamingChoices,
    Usage,
)

from tapestry.models.litellm_client import (
    EmptyResponseError,
    StreamChunk,
    TapestryContextWindowExceeded,
    assemble_tool_calls,
    call_model_stream,
)

_MODEL = "claude-sonnet-4-5-20250929"


def _text_chunk(content: str | None, *, finish_reason: str | None = None) -> ModelResponseStream:
    return ModelResponseStream(
        id="chatcmpl-test",
        model=_MODEL,
        choices=[
            StreamingChoices(
                index=0,
                delta=Delta(content=content),
                finish_reason=finish_reason,
            )
        ],
    )


def _usage_chunk(*, prompt_tokens: int, completion_tokens: int) -> ModelResponseStream:
    return ModelResponseStream(
        id="chatcmpl-test",
        model=_MODEL,
        choices=[StreamingChoices(index=0, delta=Delta(content=None), finish_reason=None)],
        usage=Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


def _tool_call_chunk(
    *,
    index: int = 0,
    call_id: str | None = None,
    name: str | None = None,
    arguments: str = "",
    finish_reason: str | None = None,
) -> ModelResponseStream:
    return ModelResponseStream(
        id="chatcmpl-test",
        model=_MODEL,
        choices=[
            StreamingChoices(
                index=0,
                delta=Delta(
                    tool_calls=[
                        ChatCompletionDeltaToolCall(
                            id=call_id,
                            index=index,
                            type="function" if call_id else None,
                            function=Function(name=name, arguments=arguments),
                        )
                    ]
                ),
                finish_reason=finish_reason,
            )
        ],
    )


async def _empty_stream():
    # An async generator with zero yields -- the "entirely empty stream"
    # case: no chunks at all, so no delta_text and no tool_call_delta.
    return
    yield  # pragma: no cover -- makes this an async generator function


async def _good_text_stream():
    yield _text_chunk("Hello, ")
    yield _text_chunk("world!")
    yield _text_chunk(None, finish_reason="stop")
    yield _usage_chunk(prompt_tokens=10, completion_tokens=5)


async def _tool_call_stream():
    yield _tool_call_chunk(index=0, call_id="call_abc123", name="get_weather", arguments="")
    yield _tool_call_chunk(index=0, arguments='{"city":')
    yield _tool_call_chunk(index=0, arguments='"NYC"}', finish_reason="tool_calls")
    yield _usage_chunk(prompt_tokens=20, completion_tokens=8)


async def _stream_then_context_window_error():
    yield _text_chunk("partial reply before the error")
    raise litellm.ContextWindowExceededError(
        message="context window exceeded for model claude-sonnet-4-5-20250929",
        model=_MODEL,
        llm_provider="anthropic",
    )


async def _collect(model=_MODEL, messages=None, tools=None, **kwargs) -> list[StreamChunk]:
    messages = messages if messages is not None else [{"role": "user", "content": "hi"}]
    return [
        chunk
        async for chunk in call_model_stream(model=model, messages=messages, tools=tools, **kwargs)
    ]


class TestCallModelStreamSuccess:
    async def test_multi_chunk_text_assembles_and_final_chunk_carries_usage(self):
        mock_acompletion = AsyncMock(return_value=_good_text_stream())

        with patch("litellm.acompletion", new=mock_acompletion):
            chunks = await _collect()

        text = "".join(c.delta_text for c in chunks if c.delta_text)
        assert text == "Hello, world!"
        assert all(c.tool_call_delta is None for c in chunks)

        usage_chunks = [c for c in chunks if c.usage is not None]
        assert len(usage_chunks) == 1  # usage populated ONLY on the terminal chunk
        final = usage_chunks[0]
        assert final.usage["prompt_tokens"] == 10
        assert final.usage["completion_tokens"] == 5
        assert final.usage["total_tokens"] == 15
        assert "cost" in final.usage  # None or float depending on pricing-map coverage
        assert final.usage["cost"] is None or isinstance(final.usage["cost"], float)

        # finish_reason surfaces on the terminal chunk regardless of which
        # raw chunk originally carried it.
        assert final is chunks[-1]
        assert final.finish_reason == "stop"

        mock_acompletion.assert_awaited_once()
        _, kwargs = mock_acompletion.await_args
        assert kwargs["stream"] is True

    async def test_usage_still_populated_when_no_raw_chunk_carries_it(self):
        async def _stream_without_usage():
            yield _text_chunk("hi")
            yield _text_chunk(None, finish_reason="stop")

        with patch("litellm.acompletion", new=AsyncMock(return_value=_stream_without_usage())):
            chunks = await _collect()

        final = chunks[-1]
        # stream_chunk_builder's own token_counter fallback kicks in --
        # usage must not be None just because no provider chunk reported it.
        assert final.usage is not None
        assert final.finish_reason == "stop"


class TestEmptyStreamRetryPolicy:
    async def test_retries_then_raises_after_max_retries(self):
        # max_retries=2 -> 1 initial attempt + 2 retries = 3 total calls,
        # each seeing its OWN fresh empty generator.
        mock_acompletion = AsyncMock(
            side_effect=[_empty_stream(), _empty_stream(), _empty_stream()]
        )

        with patch("litellm.acompletion", new=mock_acompletion):
            with pytest.raises(EmptyResponseError):
                async for _ in call_model_stream(
                    model=_MODEL,
                    messages=[{"role": "user", "content": "hi"}],
                    max_retries=2,
                ):
                    pass

        assert mock_acompletion.await_count == 3

    async def test_succeeds_after_retry_with_no_duplicated_content(self):
        mock_acompletion = AsyncMock(side_effect=[_empty_stream(), _good_text_stream()])

        with patch("litellm.acompletion", new=mock_acompletion):
            chunks = await _collect(max_retries=2)

        text = "".join(c.delta_text for c in chunks if c.delta_text)
        # The discarded empty attempt yielded nothing, so the successful
        # attempt's text appears exactly once -- proving the no-duplication
        # invariant for the retry-on-empty policy.
        assert text == "Hello, world!"
        assert mock_acompletion.await_count == 2


class TestContextWindowExceededPolicy:
    async def test_reraised_when_raised_at_call_time(self):
        provider_error = litellm.ContextWindowExceededError(
            message="context window exceeded for model claude-sonnet-4-5-20250929",
            model=_MODEL,
            llm_provider="anthropic",
        )
        mock_acompletion = AsyncMock(side_effect=provider_error)

        with patch("litellm.acompletion", new=mock_acompletion):
            with pytest.raises(TapestryContextWindowExceeded) as exc_info:
                async for _ in call_model_stream(
                    model=_MODEL, messages=[{"role": "user", "content": "a" * 1_000_000}]
                ):
                    pass

        assert "context window exceeded" in str(exc_info.value)
        # Not retried -- exactly one call before the reclassified exception.
        assert mock_acompletion.await_count == 1

    async def test_reraised_mid_stream_after_partial_content(self):
        mock_acompletion = AsyncMock(return_value=_stream_then_context_window_error())

        received: list[StreamChunk] = []
        with patch("litellm.acompletion", new=mock_acompletion):
            with pytest.raises(TapestryContextWindowExceeded):
                async for chunk in call_model_stream(
                    model=_MODEL, messages=[{"role": "user", "content": "hi"}]
                ):
                    received.append(chunk)

        # The chunk(s) preceding the mid-stream error were already yielded
        # (real streaming, not buffered) before the exception propagated.
        assert any(c.delta_text == "partial reply before the error" for c in received)
        # Not retried -- context-window errors are reclassified immediately,
        # exactly like the non-streaming call_model policy.
        assert mock_acompletion.await_count == 1


class TestAssembleToolCalls:
    async def test_tool_call_deltas_assemble_to_openai_shape_via_call_model_stream(self):
        mock_acompletion = AsyncMock(return_value=_tool_call_stream())

        with patch("litellm.acompletion", new=mock_acompletion):
            chunks = await _collect(
                tools=[{"type": "function", "function": {"name": "get_weather"}}]
            )

        assert assemble_tool_calls(chunks) == [
            {
                "id": "call_abc123",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"NYC"}'},
            }
        ]
        final = [c for c in chunks if c.usage is not None][0]
        assert final.finish_reason == "tool_calls"

    def test_multiple_tool_calls_assemble_in_index_order_not_arrival_order(self):
        # index 1's fragments arrive before index 0's later fragment --
        # output must still be sorted by index, matching the order
        # ModelResponse.tool_calls / persona_node's tool_calls[0] expects.
        chunks = [
            StreamChunk(
                tool_call_delta={
                    "id": "call_second",
                    "index": 1,
                    "type": "function",
                    "function": {"name": "b_tool", "arguments": ""},
                }
            ),
            StreamChunk(
                tool_call_delta={
                    "id": "call_first",
                    "index": 0,
                    "type": "function",
                    "function": {"name": "a_tool", "arguments": ""},
                }
            ),
            StreamChunk(
                tool_call_delta={
                    "index": 1,
                    "function": {"arguments": '{"x": 1}'},
                }
            ),
            StreamChunk(
                tool_call_delta={
                    "index": 0,
                    "function": {"arguments": '{"y": 2}'},
                }
            ),
        ]

        assert assemble_tool_calls(chunks) == [
            {
                "id": "call_first",
                "type": "function",
                "function": {"name": "a_tool", "arguments": '{"y": 2}'},
            },
            {
                "id": "call_second",
                "type": "function",
                "function": {"name": "b_tool", "arguments": '{"x": 1}'},
            },
        ]

    def test_ignores_chunks_with_no_tool_call_delta(self):
        chunks = [
            StreamChunk(delta_text="hello"),
            StreamChunk(finish_reason="stop", usage={"prompt_tokens": 1}),
        ]
        assert assemble_tool_calls(chunks) == []
