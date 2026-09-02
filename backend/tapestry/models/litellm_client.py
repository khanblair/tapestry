"""The only place LiteLLM gets called from.

Not a thin pass-through over `litellm.acompletion` -- a policy layer, per
`docs/vendor-research/ANALYSIS-litellm.md` (verified against LiteLLM
1.101.0's real source, not general knowledge). Two non-obvious correctness
rules live here so no other call site has to know about them.
"""

from __future__ import annotations

import litellm
from pydantic import BaseModel


class ModelResponse(BaseModel):
    text: str
    tool_calls: list[dict] | None = None
    cost: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict | None = None


class EmptyResponseError(Exception):
    """Raised when a model call still has no text AND no tool_calls after
    exhausting `max_retries` attempts.

    Per ANALYSIS-litellm.md section 5 / Recommendation: LiteLLM's own retry
    machinery (`num_retries`, `Router.retry_policy`) only fires on
    exceptions. A 200-ish response whose message body is simply empty is not
    an exception at all, so left unhandled it would look like a normal,
    silent (and wrong) turn. Tapestry treats it as a retryable failure
    instead.
    """


class TapestryContextWindowExceeded(Exception):
    """Raised in place of `litellm.ContextWindowExceededError`.

    Per ANALYSIS-litellm.md sections 1 and 3: LiteLLM already normalizes
    context-window-exceeded into one real, provider-detected exception
    class (`litellm.ContextWindowExceededError`, not string-matched error
    text) across Anthropic/DeepSeek/Gemini/Qwen/OpenRouter. Tapestry wraps
    that one further, under its own exception type, so every other call
    site in the codebase has exactly one canonical exception to catch and
    never needs to import or know about LiteLLM's exception hierarchy at
    all.
    """


async def call_model(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_retries: int = 2,
) -> ModelResponse:
    """Call `model` via `litellm.acompletion` and return a normalized
    `ModelResponse`.

    `max_retries` counts retries *after* the first attempt -- so
    `max_retries=2` (the default) means up to 3 total calls to LiteLLM
    before `EmptyResponseError` is raised. This only governs the
    empty-completion retry policy below; it is not passed through to
    LiteLLM's own `num_retries` (that governs retrying actual provider
    exceptions -- rate limits, timeouts -- which is a separate, unspecified
    concern this function deliberately leaves alone).
    """
    last_error: EmptyResponseError | None = None

    for attempt in range(max_retries + 1):
        try:
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=tools,
            )
        except litellm.ContextWindowExceededError as exc:
            # Policy 2 (ANALYSIS-litellm.md sections 1 and 3): reclassify LiteLLM's own
            # normalized exception into Tapestry's canonical one. Not
            # retried -- a context-window overflow won't resolve itself on
            # a bare retry, it's the caller's job to react (e.g. compact
            # history) and call again.
            raise TapestryContextWindowExceeded(str(exc)) from exc

        message = response.choices[0].message
        text = message.content or ""
        raw_tool_calls = message.tool_calls
        # Per ANALYSIS-litellm.md section 2: LiteLLM already normalizes tool_calls
        # to the OpenAI {id, type, function: {name, arguments}} shape
        # regardless of provider (verified against Anthropic/Gemini source
        # and their own regression tests) -- no extra translation needed
        # here, just a pydantic-model -> dict conversion.
        tool_calls = (
            [tc.model_dump() for tc in raw_tool_calls] if raw_tool_calls else None
        )

        if not text and not tool_calls:
            # Policy 1: empty completion is a retryable failure, not a
            # silent success. Keep looping (if attempts remain) rather than
            # returning this as a normal ModelResponse.
            last_error = EmptyResponseError(
                f"Empty completion from model={model!r}: attempt "
                f"{attempt + 1}/{max_retries + 1} returned no text and no "
                "tool_calls"
            )
            continue

        # Per ANALYSIS-litellm.md section 5: response_cost is populated
        # automatically by LiteLLM's cost calculator at
        # `response._hidden_params["response_cost"]` -- never computed here
        # against a pricing table ourselves.
        hidden_params = getattr(response, "_hidden_params", None) or {}
        cost = hidden_params.get("response_cost")

        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        output_tokens = getattr(usage, "completion_tokens", None) if usage else None

        return ModelResponse(
            text=text,
            tool_calls=tool_calls,
            cost=cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw=response.model_dump(),
        )

    assert last_error is not None  # loop always sets it before falling through
    raise last_error
