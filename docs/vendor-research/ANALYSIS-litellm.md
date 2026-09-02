# LiteLLM verification for Tapestry

Source: `vendor-research/litellm`, shallow clone of `github.com/BerriAI/litellm` at commit `5988d93fed159642d0d6fa13bcd11eb93b34c047` (2026-09-01), package version `1.101.0` per the clone's own source. All file paths below are relative to that clone unless stated otherwise.

**Correction (found during implementation, 2026-09-02):** `1.101.0` was the clone's internal version string, not a published release — it 404s on PyPI. The real latest published version is `1.99.0`, confirmed live against `pypi.org/pypi/litellm/json`. Everything else in this report (API behavior, normalization, license, provider config) was verified against the actual library and still holds — only the version number was wrong, and `backend/pyproject.toml` now pins `1.99.0`.

## Summary

The web-search-derived assumption holds at the core: LiteLLM does normalize Claude, DeepSeek, Gemini, Qwen, and OpenRouter behind one `completion()`/`acompletion()` interface, one streaming chunk shape, and one OpenAI-shaped `tool_calls` structure, verified against source and test fixtures rather than docs. It is a legitimate provider-normalization layer for a hand-rolled agent loop, not just marketing copy.

Three things contradict or complicate the assumptions as stated, in order of how much they should affect the design:

1. **License is not uniformly MIT.** The `enterprise/` directory ships under the proprietary BerriAI Enterprise License (`enterprise/LICENSE.md`), not MIT. This only matters if Tapestry installs the `proxy` extra, which pulls in `litellm-enterprise==0.1.63` (`pyproject.toml` line ~74). Pure SDK usage (`pip install litellm`, no extras) never touches that code path and is MIT end to end.
2. **"Qwen" is not one provider inside LiteLLM, it's three**, differing only by which Alibaba Cloud region/endpoint they hit: `dashscope`, `qwencloud`, and `qwen_ai_platform` are three separate entries in the `LlmProviders` enum (`litellm/types/utils.py:3811-3813`), each with its own env-var name that falls back to `DASHSCOPE_API_KEY`. Picking the wrong one silently hits the wrong region's endpoint, not an error.
3. **Normalization has real seams, not a leaky abstraction, but not free either.** Anthropic's streaming tool-call indexes don't start at 0 the way OpenAI's do, and LiteLLM has to renumber them (`tests/llm_translation/test_anthropic_completion.py:239`, `test_anthropic_tool_streaming`). Gemini doesn't return a tool-call `id` at all on older models, so LiteLLM synthesizes one (`call_<uuid>`), while Gemini 3.5+ does return a native stable id that LiteLLM preserves instead (`litellm/llms/vertex_ai/gemini/vertex_and_google_ai_studio_gemini.py:1568-1589`). These are handled for you, but they're evidence the normalization is doing real work, not a thin wrapper.

Everything else confirms the assumption cleanly: base SDK dependencies are lean (14 packages, no FastAPI/uvicorn/prisma), `num_retries`/`fallbacks` work at both the single-call and `Router` level, and per-call cost/token usage is a first-class field on the response object, not something you have to compute yourself from a pricing table.

## 1. `completion()` / `acompletion()` signature and streaming

Both are defined in `litellm/main.py`: sync `completion()` at line 4955, async `acompletion()` at line 389. Signature (trimmed to the parts Tapestry will use):

```python
def completion(
    model: str,
    messages: list = [],
    stream: bool | None = None,
    stream_options: dict | None = None,
    tools: list | None = None,
    tool_choice: str | dict | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
    num_retries: int | None = None,   # via **kwargs, see below
    **kwargs,
) -> ModelResponse | CustomStreamWrapper
```

`acompletion()` has the identical OpenAI-shaped surface (`litellm/main.py:389-441`) and, per its own docstring, is literally `completion()` run via `run_in_executor` on the event loop (`litellm/main.py:481-483`) -- there's no separate async provider code path to audit for correctness, both funnel through the same transformation layer.

Non-streaming calls return a `ModelResponse` (`litellm/types/utils.py:2062`), an OpenAI-`ChatCompletion`-shaped Pydantic model regardless of provider: `.choices[0].message.content`, `.choices[0].message.tool_calls`, `.usage`, `.model`, `._hidden_params`.

`stream=True` returns a `CustomStreamWrapper` (`litellm/litellm_core_utils/streaming_handler.py:228`), a sync/async iterator whose `__next__`/`__anext__` are type-annotated to return exactly one type regardless of provider:

```python
def __next__(self) -> "ModelResponseStream": ...        # streaming_handler.py:1916
async def __anext__(self) -> "ModelResponseStream": ...  # streaming_handler.py:2124
```

`ModelResponseStream` (`litellm/types/utils.py:1984`) always sets `object = "chat.completion.chunk"` and wraps `choices: list[StreamingChoices]`, each with a `Delta` (`litellm/types/utils.py:1405`) carrying `content`, `role`, `tool_calls`, `reasoning_content`, `thinking_blocks`. Every provider's native SSE/stream format (Anthropic's `content_block_delta` events, Gemini's chunked JSON, etc.) gets parsed into this one shape before it reaches your code -- confirmed by the constructor coercion logic at `types/utils.py:1996-2007`, which accepts `StreamingChoices | dict | BaseModel` and always normalizes to `StreamingChoices`.

## 2. Tool/function calling -- is `tool_calls` really normalized?

Yes, to OpenAI's shape, and this is directly provable from source, not just docs.

The target type, `ChatCompletionMessageToolCall` (`litellm/types/utils.py:1184`), is constructed as `{id, type: "function", function: {name, arguments: <json string>}}` no matter which provider produced it -- `Function.__init__` (`types/utils.py:1079-1103`) explicitly `json.dumps`'s dict arguments into a string, since some providers hand back a dict and OpenAI's wire format wants a string.

**Anthropic proof.** Anthropic's Messages API returns tool calls as content blocks: `{"type": "tool_use", "id": "...", "name": "...", "input": {...}}`. LiteLLM converts this explicitly:

```python
# litellm/llms/anthropic/chat/transformation.py:345
@staticmethod
def convert_tool_use_to_openai_format(
    anthropic_tool_content: dict[str, Any], index: int,
) -> ChatCompletionToolCallChunk:
    tool_call: Final = ChatCompletionToolCallChunk(
        id=anthropic_tool_content["id"],
        type="function",
        function=ChatCompletionToolCallFunctionChunk(
            name=anthropic_tool_content["name"],
            arguments=json.dumps(anthropic_tool_content["input"]),
        ),
        index=index,
    )
    return tool_call
```

called from `extract_response_content()` at the point where a `"tool_use"` (or `"server_tool_use"`) content block is encountered (`transformation.py:2117-2121`). A parallel unit test fixture (`tests/test_litellm/llms/anthropic/chat/test_anthropic_chat_transformation.py:49-66`) hard-codes the target shape as literal `{"id": "toolu_...", "type": "function", "function": {"name": ..., "arguments": "<json string>"}, "index": ...}` dicts, i.e. tests assert against the OpenAI shape directly.

**Gemini proof.** Gemini's response carries `{"functionCall": {"name": ..., "args": {...}}}` (a dict, not a string, and historically no call id at all). LiteLLM converts:

```python
# litellm/llms/vertex_ai/gemini/vertex_and_google_ai_studio_gemini.py:1560-1589
_function_chunk = {
    "name": part["functionCall"]["name"],
    "arguments": json.dumps(part["functionCall"]["args"], ensure_ascii=False),
}
gemini_call_id = part["functionCall"].get("id")
...
_tool_response_chunk: ChatCompletionToolCallChunk = {
    "id": f"call_{uuid.uuid4().hex[:28]}",   # synthesized fallback
    "type": "function",
    "function": _function_chunk,
    "index": cumulative_tool_call_idx,
}
if gemini_call_id:
    _tool_response_chunk["id"] = gemini_call_id  # Gemini 3.5+ native id, preferred
```

Gotcha worth flagging for the agent loop: if you're on an older Gemini model, the `tool_call_id` you echo back in the follow-up turn is one LiteLLM invented, not one Google issued -- that's fine as long as you always round-trip through LiteLLM and never compare ids across providers.

**Streaming tool-call gotcha (Anthropic).** OpenAI numbers tool-call indexes starting at 0 for the first tool call in a turn. Anthropic's streaming events don't guarantee that -- LiteLLM's `ModelResponseIterator.chunk_parser` renumbers them, and there's a dedicated regression test for it:

```python
# tests/llm_translation/test_anthropic_completion.py:239
def test_anthropic_tool_streaming():
    """
    OpenAI starts tool_use indexes at 0 for the first tool, regardless of preceding text.
    Anthropic gives tool_use indexes starting at the first chunk, meaning they often start
    at 1 when they should start at 0
    """
```

Net: if your agent loop's tool-dispatch logic keys off `delta.tool_calls[i].index` to accumulate streamed argument fragments (the standard OpenAI-SDK pattern), that logic will work unmodified across all five providers, because LiteLLM has already done the renumbering/id-synthesis work for you.

## 3. Provider configs: Anthropic, DeepSeek, Gemini, Qwen, OpenRouter

| Provider | Model string example (from tests) | Config class | Env vars | Default API base |
|---|---|---|---|---|
| Anthropic | `"claude-sonnet-4-5-20250929"` (no prefix needed) | `AnthropicConfig` (`litellm/llms/anthropic/chat/transformation.py`) | `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` | `https://api.anthropic.com` (`common_utils.py:845,852`) |
| DeepSeek | `"deepseek/deepseek-chat"` (config subclasses `OpenAIGPTConfig`) | `DeepSeekChatConfig` (`litellm/llms/deepseek/chat/transformation.py:19`) | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/beta` (`transformation.py:350-351`) |
| Gemini | `"gemini/gemini-2.5-flash"` (`tests/llm_translation/test_gemini.py:78`) | `VertexGeminiConfig` (`litellm/llms/vertex_ai/gemini/vertex_and_google_ai_studio_gemini.py`) | `GOOGLE_API_KEY` or `GEMINI_API_KEY` (`litellm/llms/gemini/common_utils.py:380`) | Google AI Studio endpoint (Vertex path also supported, separate auth) |
| Qwen / Alibaba | `"dashscope/qwen-turbo"` (`tests/test_litellm/llms/dashscope/test_dashscope_chat_transformation.py:60`) | `DashScopeChatConfig` (subclasses `OpenAIGPTConfig`), plus `QwenCloudChatConfig` and `QwenAIPlatformChatConfig` | `DASHSCOPE_API_KEY` (mainland), `QWENCLOUD_API_KEY` (intl, falls back to `DASHSCOPE_API_KEY`), `QWEN_AI_PLATFORM_API_KEY` (same base URL as `dashscope`, alias) | `dashscope`: `dashscope.aliyuncs.com/compatible-mode/v1`; `qwencloud`: `dashscope-intl.aliyuncs.com/compatible-mode/v1` |
| OpenRouter | `"openrouter/anthropic/claude-sonnet-4"` (`tests/llm_translation/test_openrouter.py:9`) | `OpenrouterConfig` (subclasses `OpenAIGPTConfig`, `litellm/llms/openrouter/chat/transformation.py:39`) | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` |

Gotchas beyond the Qwen triple-provider issue above:

- **DeepSeek, DashScope/Qwen, and OpenRouter are all OpenAI-compatible-endpoint providers** -- their config classes literally subclass `OpenAIGPTConfig` (`deepseek/chat/transformation.py:19`, `dashscope/chat/transformation.py:14`, `openrouter/chat/transformation.py:39`) and mostly override URL/key resolution and a handful of param mappings. This means DeepSeek and Qwen "work out of the box" precisely because their wire format is already OpenAI-shaped upstream -- LiteLLM's job there is thin (env var + base URL + a few param quirks), unlike Anthropic and Gemini where it does real translation.
- **DeepSeek reasoning mode has a footgun**: if you use DeepSeek's `thinking`/reasoner mode, every assistant turn in a multi-turn conversation must carry back `reasoning_content` or the API errors with "The reasoning_content in the thinking mode must be passed back to the API." LiteLLM patches this automatically (`_fill_reasoning_content`, `deepseek/chat/transformation.py:63-80`), injecting a placeholder if your own message history dropped it -- worth knowing if Tapestry stores/replays conversation history itself.
- **OpenRouter model strings can double up the prefix.** OpenRouter's own model IDs already look like `anthropic/claude-sonnet-4`; LiteLLM's outer `openrouter/` prefix stacks on top (`openrouter/anthropic/claude-sonnet-4`), and there's a specific edge case where an OpenRouter-native model literally starts with `openrouter/` (e.g. `openrouter/aurora-alpha`), producing `openrouter/openrouter/aurora-alpha` as the full LiteLLM model string (`tests/test_litellm/llms/openrouter/test_openrouter_provider_routing.py:1-28`). LiteLLM's `get_llm_provider` strips only the outer prefix correctly, but it's a sharp edge if Tapestry builds model strings dynamically from a persona config.
- **Anthropic is the only one of the five that takes a bare model id** (no `anthropic/` prefix required, though it's accepted) -- worth normalizing in Tapestry's persona schema so you don't have inconsistent prefixing rules per provider.

## 4. Retry and fallback support

Two layers exist.

**Per-call (`completion`/`acompletion`)**: pass `num_retries` (preferred) or `max_retries` as a kwarg; internally `num_retries` becomes `max_retries` unless the call is already going through a `Router` model-group, in which case `main.py` forces `max_retries = 0` and defers all retry logic to the `Router` (`litellm/main.py:5259-5268`, `is_router_call` check). Separately, passing `fallbacks=[...]` (a list of alternate model configs) routes the call through `completion_with_fallbacks(**args)` instead of the normal path (`main.py:5265-5270`). There's also a tenacity-based `completion_with_retries()`/`acompletion_with_retries()` pair (`main.py:5849-5877`) that wraps the whole call in a `tenacity.Retrying`/`AsyncRetrying` loop with `stop_after_attempt(num_retries)`.

**Router-level** (`litellm/router.py`, `class Router` at line 561): this is the layer Tapestry should actually use if personas can fail over between models/providers. Constructor params include `fallbacks: list`, `context_window_fallbacks: list`, `content_policy_fallbacks: list`, `default_fallbacks: list[str]`, `max_fallbacks: int`, and a structured `retry_policy: RetryPolicy | dict` plus `model_group_retry_policy: dict[str, RetryPolicy]` for per-exception-type retry counts (e.g. different retry counts for `RateLimitError` vs `Timeout`) -- `router.py:594-613`. This means "if Claude times out, retry twice then fall back to DeepSeek" is a native, declarative Router config, not something Tapestry needs to hand-roll in the LangGraph node.

## 5. Cost and token tracking

Confirmed as a first-class, non-optional feature -- not something bolted on via a side pricing table you maintain yourself.

**Non-streaming**: every `ModelResponse.usage` is a `Usage` object (`litellm/types/utils.py:1738`) that subclasses OpenAI's `CompletionUsage` (`prompt_tokens`, `completion_tokens`, `total_tokens`) and adds a `cost: float | None` field directly. Separately, LiteLLM computes cost automatically after every call and stores it at `response._hidden_params["response_cost"]` -- confirmed by a real assertion in the test suite:

```python
# tests/audio_tests/test_audio_speech.py:437-438
print("Response cost: ", response._hidden_params["response_cost"])
assert response._hidden_params["response_cost"] > 0
```

This is populated by `_response_cost_calculator` inside the logging object (`litellm/litellm_core_utils/litellm_logging.py:1605-1720`), which calls `litellm.response_cost_calculator(...)` against `model_prices_and_context_window.json` (a 2MB, actively-maintained pricing map shipped in the repo root) unless the provider itself reports cost, in which case the provider-reported number wins.

**Streaming** needs one extra step, since usage generally isn't attached to every chunk. Passing `stream_options={"include_usage": True}` controls what the *caller* sees in the stream, but LiteLLM tracks usage-only chunks internally regardless, specifically to keep cost calculation correct even if you don't ask for `include_usage` (`litellm/litellm_core_utils/streaming_handler.py:1874-1881`, `_record_usage_only_chunk`). The clean way to get a final cost number for a streamed call is to accumulate the chunks and call `litellm.stream_chunk_builder(chunks, messages=messages)`, which reassembles them into a normal `ModelResponse` and -- if the provider didn't report cost natively -- falls back to `litellm.cost_per_token(model=..., usage_object=...)` against the same pricing map, then sets `_hidden_params["response_cost"]` on the reassembled object (`litellm/main.py:8632-8637`, `_set_stream_builder_response_cost`).

**Recommended pattern for "per-persona cost" in the product**: register a `litellm.success_callback` / custom `CustomLogger` (`litellm/integrations/custom_logger.py:64`, `log_success_event(kwargs, response_obj, start_time, end_time)`) once, globally. LiteLLM populates `kwargs["response_cost"]` on every successful call -- streaming or not -- right before firing this callback (`litellm_logging.py:1520`, `self.model_call_details["response_cost"] = response.hidden_params.response_cost`). That gives Tapestry one central place to attribute cost to a persona/call without threading cost-extraction logic through every call site in the LangGraph graph.

## 6. License

`LICENSE` at repo root: MIT (Berri AI, 2023), standard text, no modifications. However the license file itself carves out an exception at the top:

> All content that resides under the "enterprise/" directory of this repository... is licensed under the license defined in "enterprise/LICENSE".

`enterprise/LICENSE.md` is the proprietary **BerriAI Enterprise License** -- production use requires a paid subscription/seats; you may modify and use it for dev/test without one, but BerriAI retains rights to any patches, and you can't fork/resell it.

**This does not affect SDK-only usage.** `litellm-enterprise` is a separate PyPI package (`litellm-enterprise==0.1.63`) that only enters the dependency tree via the `proxy` extra (`pyproject.toml`, `[project.optional-dependencies] proxy = [..., "litellm-enterprise==0.1.63", ...]`). A bare `pip install litellm` never installs or imports it. Since Tapestry is explicitly SDK-only (no proxy server), the effective license for everything Tapestry will actually ship with is plain MIT.

## 7. Package name and dependency footprint

`pip install litellm` -- confirmed via `pyproject.toml` line 2 (`name = "litellm"`).

Base install (no extras) pulls exactly 14 direct dependencies (`pyproject.toml:12-30`):

```
fastuuid, httpx, openai, python-dotenv, tiktoken, importlib-metadata,
tokenizers, click, jinja2, aiohttp, pydantic, pydantic-settings,
jsonschema, boto3
```

No FastAPI, uvicorn, gunicorn, Prisma, Redis, or any proxy-server dependency is in the base install -- those all live behind the `proxy` extra (`pyproject.toml:44-75`, `gunicorn`, `uvicorn`, `fastapi`, `starlette`, `rq`, `apscheduler`, `mcp`, `litellm-proxy-extras`, `litellm-enterprise`, etc.) or `extra_proxy` (`prisma`, `psycopg`, `google-cloud-kms`, ...). A grep across `litellm/__init__.py`, `litellm/main.py`, and `litellm/utils.py` for `fastapi`/`uvicorn` imports at module level returns nothing -- confirming the proxy dependencies don't leak into core-SDK import time even transitively.

One caveat: `boto3` is an unconditional base dependency (needed for the Bedrock provider LiteLLM ships by default), so "SDK-only footprint" is meaningfully lighter than the proxy install, but it isn't a minimal 3-package library either -- expect `boto3`'s own dependency weight (`botocore`, etc.) even if Tapestry never touches Bedrock.

## Recommendation

Use LiteLLM's `acompletion()` directly inside a LangGraph node, one call per persona turn, with a global `CustomLogger` registered once at startup for cost attribution (rather than parsing `_hidden_params` at every call site). Sketch:

```python
# tapestry/llm/call_model.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import litellm
from litellm import CustomStreamWrapper, ModelResponse, acompletion


@dataclass(frozen=True, slots=True)
class Persona:
    name: str
    model: str  # e.g. "claude-sonnet-4-5-20250929", "gemini/gemini-2.5-flash",
                # "dashscope/qwen-turbo", "deepseek/deepseek-chat",
                # "openrouter/anthropic/claude-sonnet-4"
    temperature: float = 1.0
    api_key: str | None = None  # falls back to the provider's env var if unset


class PersonaCostLogger(litellm.integrations.custom_logger.CustomLogger):
    """Registered once via litellm.success_callback = [PersonaCostLogger()]."""

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time) -> None:
        persona_name = kwargs.get("litellm_params", {}).get("metadata", {}).get("persona_name")
        cost = kwargs.get("response_cost")  # populated by LiteLLM before this fires
        usage = getattr(response_obj, "usage", None)
        # write (persona_name, cost, usage.prompt_tokens, usage.completion_tokens) to your cost store


async def call_model(
    persona: Persona,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> ModelResponse:
    """One persona turn. Non-streaming; see call_model_streaming for the streamed variant."""
    response = await acompletion(
        model=persona.model,
        messages=messages,
        tools=tools,
        temperature=persona.temperature,
        api_key=persona.api_key,
        num_retries=2,
        metadata={"persona_name": persona.name},  # threaded through to the cost logger above
    )
    assert isinstance(response, ModelResponse)  # narrows away the streaming union
    return response


async def call_model_streaming(
    persona: Persona,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> ModelResponse:
    """Streamed variant: collects chunks, then reassembles a normal ModelResponse
    (with .usage and _hidden_params['response_cost'] populated) via stream_chunk_builder."""
    stream = await acompletion(
        model=persona.model,
        messages=messages,
        tools=tools,
        temperature=persona.temperature,
        api_key=persona.api_key,
        stream=True,
        stream_options={"include_usage": True},
        num_retries=2,
        metadata={"persona_name": persona.name},
    )
    assert isinstance(stream, CustomStreamWrapper)
    chunks = [chunk async for chunk in stream]
    for chunk in chunks:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content  # or push onto a LangGraph streaming channel
    final_response = litellm.stream_chunk_builder(chunks, messages=messages)
    assert isinstance(final_response, ModelResponse)
    return final_response
```

Register `litellm.success_callback = [PersonaCostLogger()]` once at process startup (not per call). For failover between personas/providers (e.g. "if Claude is down, this persona falls back to DeepSeek"), don't hand-roll it in `call_model` -- configure a `litellm.Router` with `fallbacks` and a `retry_policy` per persona/model-group instead, and call `router.acompletion(...)` in place of the bare `acompletion(...)` above; everything else in this sketch stays the same since `Router.acompletion` returns the same `ModelResponse`/`CustomStreamWrapper` union.
