"""Real-time event streaming out of the graph — `stream_mode="custom"`.

Per `docs/vendor-research/ANALYSIS-langgraph.md` §4 (verified against real
source, not general knowledge): `stream_mode="messages"` is implemented as a
LangChain callback handler that hooks `on_llm_new_token` on LangChain
`Runnable` chat models. Our persona node calls `litellm.acompletion()`
directly — no LangChain `Runnable` involved — so `"messages"` mode never
fires for it; at best it would surface one whole `BaseMessage` at
`on_chain_end` if we bothered wrapping our response in one, which is not
real streaming.

`stream_mode="custom"` is provider-agnostic and exactly matches "arbitrary
async callable as a node": any node can request a `writer` kwarg or call
`get_stream_writer()` to get a `StreamWriter = Callable[[Any], None]`.
Anything passed to it is emitted immediately as a `CustomStreamPart` on
`graph.astream(..., stream_mode="custom")` — this is what an adapter
(Discord/Telegram/web) will eventually consume to forward tokens and
"tool running" status live.
"""

from __future__ import annotations

from langgraph.config import get_stream_writer
from langgraph.types import StreamWriter

__all__ = ["StreamWriter", "get_writer", "emit"]


def get_writer() -> StreamWriter:
    """Return the current node's `StreamWriter`.

    Thin, named wrapper around `langgraph.config.get_stream_writer` so the
    rest of `graph/` imports from one place and never needs to know the
    upstream module layout changed.

    Safe to call from inside ANY node execution, regardless of how the
    graph was invoked (`.ainvoke()`, `.astream()` with `stream_mode=
    "values"`/`"updates"`/etc.) — LangGraph's own `get_config()` succeeds
    for any node mid-run, and the writer it returns is simply inert
    (writes go nowhere) when `"custom"` wasn't requested, per the verified
    source. It is NOT safe to call outside a running node at all (e.g.
    directly from a plain unit test with no graph executing) — that raises
    `RuntimeError("Called get_config outside of a runnable context")`, per
    `langgraph/config.py`'s own `get_config()`. Tests that need to assert on
    emitted events should invoke a node inside a real (or `.ainvoke`'d)
    graph run rather than calling node functions bare.
    """
    return get_stream_writer()


def emit(event_type: str, payload: dict) -> None:
    """Push one incremental event out of the currently-running node.

    `event_type` is a short, dot/slash-free-form tag a consumer switches on
    (e.g. `"token"`, `"tool_status"`, `"delegation"`) — deliberately not
    reusing `core.events` event *type* strings (`"model/response"`,
    `"tool/result"`, ...), since those name durable, checkpointed log
    entries while this names a transient, never-persisted stream frame.
    Mixing the two vocabularies would make it look like every stream frame
    is also a log event, which it isn't — most streamed tokens never get
    logged individually (only the final assembled text does, via
    `core.events.append_event` in the persona node).

    `payload` is forwarded verbatim as the emitted value's `"payload"` key,
    alongside `"type": event_type`, so a consumer downstream (an adapter's
    websocket relay) can dispatch on `frame["type"]` without inspecting
    `payload` first.
    """
    writer = get_writer()
    writer({"type": event_type, "payload": payload})
