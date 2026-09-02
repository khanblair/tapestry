"""Tests for tapestry.graph.streaming -- stream_mode="custom" + StreamWriter.

Not in the original project tree (same gap noted in test_checkpointer.py).

Per ANALYSIS-langgraph.md §4 (verified against real source): `get_stream_
writer()` requires an actual running node context -- calling it bare
raises `RuntimeError("Called get_config outside of a runnable context")`.
So the real proof that `emit()`/`get_writer()` work is exercising them
from inside an actual compiled graph run with `stream_mode="custom"`, not
a bare unit-test call -- which is exactly what the integration test below
does. A couple of cheap monkeypatched unit tests cover the wiring
(`get_writer` delegates to the right upstream function; `emit` shapes its
payload correctly) without needing a live graph for every case.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from tapestry.graph import streaming


def test_get_writer_delegates_to_langgraph_get_stream_writer(monkeypatch):
    sentinel_writer = MagicMock()
    monkeypatch.setattr(streaming, "get_stream_writer", lambda: sentinel_writer)

    assert streaming.get_writer() is sentinel_writer


def test_emit_shapes_payload_and_forwards_to_writer(monkeypatch):
    sentinel_writer = MagicMock()
    monkeypatch.setattr(streaming, "get_stream_writer", lambda: sentinel_writer)

    streaming.emit("tool_status", {"tool_name": "terminal", "status": "running"})

    sentinel_writer.assert_called_once_with(
        {"type": "tool_status", "payload": {"tool_name": "terminal", "status": "running"}}
    )


class _StreamingState(TypedDict):
    count: int


def _emit_and_increment(state: _StreamingState) -> dict:
    streaming.emit("node_status", {"phase": "running", "count": state["count"]})
    return {"count": state["count"] + 1}


async def test_emit_reaches_a_real_astream_custom_consumer():
    """End-to-end: a real node calling streaming.emit() inside a real
    compiled graph, consumed via graph.astream(..., stream_mode="custom").
    Proves get_stream_writer() actually works from inside live node
    execution, not just that our wrapper delegates to it.
    """
    builder = StateGraph(_StreamingState)
    builder.add_node("step", _emit_and_increment)
    builder.add_edge(START, "step")
    builder.add_edge("step", END)
    graph = builder.compile()

    frames = [
        chunk
        async for chunk in graph.astream({"count": 0}, stream_mode="custom")
    ]

    assert frames == [{"type": "node_status", "payload": {"phase": "running", "count": 0}}]


async def test_emit_is_inert_when_custom_mode_not_requested():
    """Calling emit() from inside a node that's running under a DIFFERENT
    stream_mode must not raise -- the writer is simply inert, per the
    verified source (get_config() succeeds for any node mid-run; only the
    resulting writer is a no-op when "custom" wasn't requested).
    """
    builder = StateGraph(_StreamingState)
    builder.add_node("step", _emit_and_increment)
    builder.add_edge(START, "step")
    builder.add_edge("step", END)
    graph = builder.compile()

    result = await graph.ainvoke({"count": 0})
    assert result == {"count": 1}
