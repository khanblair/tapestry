"""The only place LiteLLM gets called from.

Not a thin pass-through over `litellm.acompletion` -- a policy layer, per
`docs/vendor-research/ANALYSIS-litellm.md` (verified against LiteLLM
1.101.0's real source, not general knowledge, and re-verified directly
against the actually-installed `litellm==1.99.0` package for the streaming
additions below -- see `call_model_stream`'s docstring for the specific
points where the installed package's behavior was checked rather than
assumed from the ANALYSIS doc). Two non-obvious correctness rules live here
so no other call site has to know about them, now duplicated for the
streaming call path (`call_model_stream`) since a retried stream can't
resume a non-streaming response's retry loop.
"""

from __future__ import annotations

from typing import AsyncIterator

import litellm
from pydantic import BaseModel


class ModelResponse(BaseModel):
    text: str
    tool_calls: list[dict] | None = None
    cost: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict | None = None
    # Which model actually produced this response -- equal to the `model`
    # `call_model` was originally called with UNLESS a `fallback_models`
    # entry ended up answering instead. Always set explicitly by
    # `call_model`, never left to infer a default; see that function's
    # docstring for the full fallback contract.
    model_used: str


class StreamChunk(BaseModel):
    """One normalized increment out of `call_model_stream`.

    Deliberately flat (one delta "kind" per chunk) rather than mirroring
    LiteLLM's `ModelResponseStream.choices[0].delta`'s multi-field shape --
    downstream consumers (`graph/streaming.py`'s `emit()`, and eventually an
    adapter's websocket/message-edit relay) want to switch on "what kind of
    increment is this" without re-deriving it from a raw delta object.

    - `delta_text`: a fragment of assistant-visible text, verbatim, in
      generation order. Concatenate all non-`None` values across a
      successful call's chunks to reconstruct `ModelResponse.text`.
    - `tool_call_delta`: one raw tool-call delta fragment (LiteLLM's
      `ChatCompletionDeltaToolCall.model_dump()` shape: `{id, type,
      function: {name, arguments}, index}`), NOT yet assembled -- pass every
      chunk with a non-`None` `tool_call_delta` to `assemble_tool_calls()`
      to reconstruct `ModelResponse.tool_calls`.
    - `finish_reason`: provider finish reason (`"stop"`, `"tool_calls"`,
      `"length"`, ...), present whenever the provider actually sent one.
      Informational only when it appears on a non-terminal chunk (see the
      "not authoritative" note in `call_model_stream`'s docstring) -- a
      provider that never sends one leaves it `None` even on the terminal
      chunk, so identify the terminal chunk of a successful call by `usage
      is not None`, never by `finish_reason` being set.
    - `usage`: populated ONLY on the terminal chunk of a successful call
      (mirrors LiteLLM's own real streaming behavior: usage is not
      attached to every chunk, confirmed against the installed package --
      see `call_model_stream`). Shape: `{"prompt_tokens": int | None,
      "completion_tokens": int | None, "total_tokens": int | None, "cost":
      float | None}`. `cost` lives inside this dict (not a top-level
      `StreamChunk` field) because it is only ever known once, at the very
      end, alongside token usage -- matching LiteLLM's own convention of
      hanging `.cost` off the `Usage` object (seen in
      `litellm.stream_chunk_builder`'s internals) rather than the
      non-streaming path's separate `_hidden_params["response_cost"]`,
      which has no per-chunk equivalent to attach to.
    """

    delta_text: str | None = None
    tool_call_delta: dict | None = None
    finish_reason: str | None = None
    usage: dict | None = None


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


async def _call_one_model(
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    max_retries: int,
) -> ModelResponse:
    """The original single-model `call_model` body, unchanged in behavior --
    `call_model` below wraps this in a fallback-chain loop. Split out so
    that loop can retry a WHOLE `max_retries+1`-attempt cycle against the
    next candidate model, rather than the empty-completion retry counter
    and the fallback-candidate counter being conflated into one.
    """
    last_error: EmptyResponseError | None = None

    for attempt in range(max_retries + 1):
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            tools=tools,
        )

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
            model_used=model,
        )

    assert last_error is not None  # loop always sets it before falling through
    raise last_error


async def call_model(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_retries: int = 2,
    fallback_models: list[str] = (),
) -> ModelResponse:
    """Call `model` via `litellm.acompletion` and return a normalized
    `ModelResponse`.

    `max_retries` counts retries *after* the first attempt -- so
    `max_retries=2` (the default) means up to 3 total calls to LiteLLM
    before that model is considered exhausted. This only governs the
    empty-completion retry policy; it is not passed through to LiteLLM's
    own `num_retries` (that governs retrying actual provider exceptions --
    rate limits, timeouts -- which used to be a separate, unspecified
    concern this function left alone; see the fallback-chain note below for
    why that's no longer quite true).

    `fallback_models`, when given, is walked in order after `model` itself
    is exhausted -- "exhausted" meaning either every attempt against it
    returned an empty completion (`EmptyResponseError`), or a single
    attempt raised any provider/transport exception OTHER than
    `litellm.ContextWindowExceededError`. That one exception never
    triggers a fallback and is never retried, regardless of position in the
    candidate list, exactly as when `fallback_models` is empty: reclassified
    immediately into `TapestryContextWindowExceeded` (per ANALYSIS-litellm.md
    sections 1 and 3) and raised, because a context overflow won't resolve
    itself by calling a *different* model with the exact same oversized
    message history -- it's the caller's job to react (e.g. compact
    history) and call again, not this function's job to paper over.

    With `fallback_models=()` (the default), this reproduces the exact
    previous behavior: a non-context-window exception from `litellm.
    acompletion` propagates immediately and unchanged (there is nowhere to
    "fall back" to), and empty-completion exhaustion still raises
    `EmptyResponseError`.

    The returned `ModelResponse.model_used` names whichever model actually
    produced the response, so a caller can tell a fallback happened without
    re-deriving anything (`response.model_used != model`).
    """
    candidates = [model, *fallback_models]
    last_error: Exception | None = None

    for candidate_model in candidates:
        try:
            return await _call_one_model(candidate_model, messages, tools, max_retries)
        except litellm.ContextWindowExceededError as exc:
            # Policy 2: never falls back, regardless of which candidate hit
            # it -- see docstring.
            raise TapestryContextWindowExceeded(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
            last_error = exc
            continue

    assert last_error is not None  # candidates is never empty (model is always in it)
    raise last_error


async def call_model_stream(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    max_retries: int = 2,
) -> AsyncIterator[StreamChunk]:
    """Streaming counterpart to `call_model`. Same two policies (empty-
    completion retry, context-window reclassification), adapted for a
    `stream=True` call -- see `EmptyResponseError`/`TapestryContextWindowExceeded`
    for the policies themselves, this docstring covers only what's different
    about applying them to a stream.

    Calls `litellm.acompletion(model=model, messages=messages, tools=tools,
    stream=True)`, which per ANALYSIS-litellm.md section 1 (confirmed
    directly against installed `litellm==1.99.0`'s
    `litellm/types/utils.py`, not just the doc) returns a `CustomStreamWrapper`
    whose `__anext__` always yields a `ModelResponseStream` --
    `choices[0].delta.content` for text, `choices[0].delta.tool_calls`
    (a list of `ChatCompletionDeltaToolCall {id, type, function: {name,
    arguments}, index}`) for tool-call fragments, `choices[0].finish_reason`
    when the turn ends, and -- only on a chunk carrying it, confirmed by
    reading `ModelResponseStream.__init__` in the installed package -- a
    `.usage` attribute. This function also passes `stream_options=
    {"include_usage": True}` (a deliberate addition beyond the caller's
    literal request, justified by ANALYSIS-litellm.md section 5: it makes
    provider-reported usage more likely to arrive on the terminal chunk).
    That said, usage extraction does NOT depend on it: the terminal
    `StreamChunk.usage` is built via `litellm.stream_chunk_builder(...)`,
    which falls back to counting tokens itself
    (`ChunkProcessor.calculate_usage` -> `token_counter`) when no chunk
    carried provider usage -- confirmed by reading that function's source in
    the installed package. So `usage` on the terminal chunk is populated on
    every successful call, streamed usage or not.

    RETRY SEMANTICS -- restart from scratch, and why that's still safe to
    forward live
    -------------------------------------------------------------------
    A partially-consumed `CustomStreamWrapper` cannot be resumed -- there is
    no LiteLLM API to "continue" a stream from wherever it stopped. So,
    exactly like `call_model`, retrying here means starting an entirely new
    `litellm.acompletion(..., stream=True)` call and a fresh
    `async for` loop; `max_retries` counts retries after the first attempt,
    same convention as `call_model`.

    The empty-stream check is IDENTICAL in spirit to `call_model`'s: an
    attempt is retryable if it produced zero chunks with a non-`None`
    `delta_text` or `tool_call_delta` (a `finish_reason`-only chunk, e.g. an
    immediate `finish_reason="content_filter"` with no content, still
    counts as empty). This function yields chunks live, as they arrive from
    LiteLLM, attempt by attempt -- it does NOT buffer an attempt and decide
    after the fact whether to forward it. That is deliberately safe:

        INVARIANT: a retry can never re-emit visible content
        (`delta_text` / `tool_call_delta`) the caller already received,
        because the retry-on-empty policy only ever fires on an attempt
        that yielded neither. If ANY `delta_text` or `tool_call_delta` was
        yielded during an attempt, that attempt is by definition not
        "empty" and will not be retried -- it runs to completion and this
        function returns.

    The only thing a caller can observe from a discarded (retried) attempt
    is a `finish_reason`-only `StreamChunk` (rare -- most providers only set
    `finish_reason` on their very last, otherwise-empty delta right before
    ending the connection, which is exactly the shape that trips "empty").
    Practical consequence for whoever wires `emit()` (graph/streaming.py)
    into this: treat `finish_reason` as informational, never as an
    authoritative "the turn is over" signal, until `call_model_stream`
    returns normally (no exception) -- a `finish_reason` seen mid-sequence
    could still belong to an attempt that's about to be discarded and
    retried.

    This invariant holds ONLY because retry is gated on total emptiness. If
    a future change broadens retry to also cover transient mid-stream
    errors (timeouts, disconnects) that can occur AFTER real content was
    already yielded, the invariant breaks -- a retry would then re-emit
    already-forwarded text. Whoever makes that change must revisit this
    docstring and the recommendation below.

    RECOMMENDATION for the eventual `emit()` integration: forward chunks
    eagerly, live, exactly as `call_model_stream` yields them -- buffering
    server-side until final success would defeat the entire point of this
    function (Discord/Telegram/web adapters want real token-by-token
    output, not status frames). As cheap future-proofing against the
    invariant above ever being weakened, tag each forwarded frame with an
    attempt/sequence number so an adapter *could* discard/replace a
    superseded attempt's partial output if that ever becomes necessary --
    but under the policy actually implemented here, it never will be.

    NOT WIRED INTO `graph/build.py`'s `persona_node` YET -- see the
    judgment-call note at the top of `graph/build.py` for why, and the
    reconstruction recipe below for what that integration needs to do once
    someone picks it up:

        text          = "".join(c.delta_text for c in chunks if c.delta_text)
        tool_calls    = assemble_tool_calls(chunks)  # [] if none were ever seen
        cost/tokens   = the terminal chunk's `.usage` dict (the one and only
                        chunk with `usage is not None`)

    which is exactly enough to build a `ModelResponse`-equivalent for
    `persona_node`'s `events.append_event("model/response", ...)` call and
    its `response.tool_calls[0]` dispatch logic, once that integration
    decides where the buffer lives and whether the durable event commits on
    first attempt or only after `call_model_stream` returns.
    """
    last_error: EmptyResponseError | None = None

    for attempt in range(max_retries + 1):
        try:
            stream = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=tools,
                stream=True,
                # Deliberate deviation from call_model's call shape (which
                # passes neither stream_options nor num_retries): makes
                # provider-reported usage more likely on the terminal chunk.
                # See ANALYSIS-litellm.md section 5 and this function's own
                # docstring -- usage extraction does not depend on it.
                stream_options={"include_usage": True},
            )
        except litellm.ContextWindowExceededError as exc:
            # Same policy as call_model, applied at the point the call is
            # initiated: not retried, reclassified immediately.
            raise TapestryContextWindowExceeded(str(exc)) from exc

        raw_chunks: list = []
        saw_content = False
        last_finish_reason: str | None = None

        try:
            async for raw_chunk in stream:
                raw_chunks.append(raw_chunk)

                for choice in raw_chunk.choices or []:
                    delta = getattr(choice, "delta", None)
                    finish_reason = getattr(choice, "finish_reason", None)
                    if finish_reason:
                        last_finish_reason = finish_reason

                    content = getattr(delta, "content", None) if delta is not None else None
                    if content:
                        saw_content = True
                        yield StreamChunk(delta_text=content)

                    tool_call_deltas = (
                        getattr(delta, "tool_calls", None) if delta is not None else None
                    )
                    if tool_call_deltas:
                        saw_content = True
                        for tool_call_delta in tool_call_deltas:
                            yield StreamChunk(tool_call_delta=tool_call_delta.model_dump())

                    if finish_reason and not content and not tool_call_deltas:
                        yield StreamChunk(finish_reason=finish_reason)
        except litellm.ContextWindowExceededError as exc:
            # Same policy as call_model, applied mid-stream: a provider can
            # surface this once tokens are already flowing (context grows
            # as multi-turn tool results get appended), not only up front.
            raise TapestryContextWindowExceeded(str(exc)) from exc

        if not saw_content:
            # Policy 1, streaming form: keep looping (if attempts remain)
            # rather than treating an entirely empty stream as a normal,
            # silent completion -- exact same reasoning as call_model.
            last_error = EmptyResponseError(
                f"Empty stream from model={model!r}: attempt "
                f"{attempt + 1}/{max_retries + 1} produced no delta_text and "
                "no tool_call_delta"
            )
            continue

        yield StreamChunk(
            finish_reason=last_finish_reason,
            usage=_final_usage_and_cost(raw_chunks, model=model, messages=messages),
        )
        return

    assert last_error is not None  # loop always sets it before falling through
    raise last_error


def _final_usage_and_cost(raw_chunks: list, *, model: str, messages: list[dict]) -> dict | None:
    """Reassemble a successful stream's chunks into token usage + cost.

    Per ANALYSIS-litellm.md section 5, `litellm.stream_chunk_builder(chunks,
    messages=messages)` is the documented way to turn a list of raw
    `ModelResponseStream` chunks back into a normal `ModelResponse` with
    `.usage` populated. Confirmed directly against the installed package
    (not assumed from the doc) that this call alone does NOT also populate
    `_hidden_params["response_cost"]` the way a real end-to-end
    `acompletion()` call does -- that only happens when a `logging_obj` is
    threaded through, which is LiteLLM-internal plumbing this module has no
    business constructing. So cost is computed separately via the public
    `litellm.completion_cost(...)` API against the same reassembled
    response, and folded into the same dict `stream_chunk_builder` would
    have wanted to attach it to (`Usage.cost`, per that function's own
    internal convention, seen while reading its source).
    """
    try:
        final_response = litellm.stream_chunk_builder(raw_chunks, messages=messages)
    except Exception:
        final_response = None

    if final_response is None:
        return None

    usage_dict: dict = {}
    usage = getattr(final_response, "usage", None)
    if usage is not None:
        usage_dict["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
        usage_dict["completion_tokens"] = getattr(usage, "completion_tokens", None)
        usage_dict["total_tokens"] = getattr(usage, "total_tokens", None)

    try:
        usage_dict["cost"] = litellm.completion_cost(
            completion_response=final_response, model=model, messages=messages
        )
    except Exception:
        # Mirrors the non-streaming path's silent hidden_params.get(...) ->
        # None on a cache miss (e.g. an unpriced/custom model) -- a missing
        # price should never break the stream.
        usage_dict["cost"] = None

    return usage_dict or None


def assemble_tool_calls(chunks: list[StreamChunk]) -> list[dict]:
    """Reassemble streamed `tool_call_delta` fragments into the same
    OpenAI-shaped `tool_calls` list `ModelResponse.tool_calls` already
    produces for the non-streaming path -- `[{"id", "type", "function":
    {"name", "arguments"}}]`, no `"index"` key in the output (that's
    LiteLLM's internal bookkeeping field, stripped here same as the
    non-streaming path never surfaces it).

    Per ANALYSIS-litellm.md section 2 (confirmed against the installed
    package's own `stream_chunk_builder` tool-call assembly logic, which
    does the equivalent grouping-by-index internally): LiteLLM's streaming
    layer has ALREADY renumbered Anthropic's non-zero-based tool-call
    indexes and synthesized ids for providers (older Gemini) that don't
    return one natively, before a `ChatCompletionDeltaToolCall` ever
    reaches this function. So the only work left here is the standard
    OpenAI-SDK accumulation pattern: group fragments by `index`, and for
    each index concatenate `function.arguments` string fragments IN
    ARRIVAL ORDER (they are partial JSON text, only valid once fully
    concatenated -- do not attempt to parse any individual fragment), while
    taking the first non-`None` `id`/`type`/`function.name` seen for that
    index (providers send those once, on the first fragment for a given
    tool call, then omit them on subsequent fragments of the same call).

    Output order is sorted by index, not first-appearance order, to match
    the ordering the non-streaming path returns: index 0 is caller-visible
    tool_calls[0], and code such as
    `graph/build.py`'s `persona_node` (`response.tool_calls[0]`) depends on
    that being the first tool call the model proposed, not an arrival-order
    accident.
    """
    by_index: dict[int, dict] = {}

    for chunk in chunks:
        delta = chunk.tool_call_delta
        if delta is None:
            continue

        index = delta.get("index", 0)
        entry = by_index.setdefault(
            index,
            {"id": None, "type": "function", "function": {"name": None, "arguments": ""}},
        )

        if delta.get("id"):
            entry["id"] = delta["id"]
        if delta.get("type"):
            entry["type"] = delta["type"]

        function_delta = delta.get("function") or {}
        if function_delta.get("name"):
            entry["function"]["name"] = function_delta["name"]
        if function_delta.get("arguments"):
            entry["function"]["arguments"] += function_delta["arguments"]

    return [by_index[index] for index in sorted(by_index)]
