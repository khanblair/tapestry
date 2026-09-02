"""Tests for tapestry.graph.checkpointer -- the AsyncSqliteSaver factory.

Not in the original project tree (`project_structure.md` only lists
test_build/test_budgets/test_verify.py under backend/tests/graph/) -- a
real gap in the original scaffold, since checkpointer.py is exactly the
kind of small-but-load-bearing module ("does it actually persist across a
fresh connection to the same file?") that's cheap to get wrong silently.

Uses a REAL AsyncSqliteSaver against a tmp_path file throughout -- no
mocking of aiosqlite/langgraph's checkpoint machinery -- since the entire
point of this module is "does the real persistence actually work."
"""

from __future__ import annotations

import os

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from tapestry.graph.checkpointer import (
    CHECKPOINT_PATH_ENV_VAR,
    DEFAULT_CHECKPOINT_PATH,
    get_checkpointer,
)


class _TinyState(TypedDict):
    value: int


def _tiny_node(state: _TinyState) -> dict:
    return {"value": state["value"] + 1}


async def test_get_checkpointer_returns_async_sqlite_saver(tmp_path):
    db_path = str(tmp_path / "checkpoints.sqlite")

    saver = await get_checkpointer(db_path)
    try:
        assert isinstance(saver, AsyncSqliteSaver)
    finally:
        await saver.conn.close()


async def test_get_checkpointer_persists_real_graph_state_to_disk(tmp_path):
    db_path = str(tmp_path / "checkpoints.sqlite")

    saver = await get_checkpointer(db_path)
    try:
        builder = StateGraph(_TinyState)
        builder.add_node("tiny", _tiny_node)
        builder.add_edge(START, "tiny")
        builder.add_edge("tiny", END)
        graph = builder.compile(checkpointer=saver)

        config = {"configurable": {"thread_id": "thread-1"}}
        result = await graph.ainvoke({"value": 1}, config)
        assert result == {"value": 2}

        # The checkpoint actually landed in the real sqlite file on disk --
        # not just in the live connection's in-memory state.
        assert os.path.exists(db_path)
        assert os.path.getsize(db_path) > 0
    finally:
        await saver.conn.close()


async def test_get_checkpointer_survives_a_fresh_connection_to_the_same_file(tmp_path):
    """The actual durability story for v1: open, write a checkpoint, close,
    reopen a brand new AsyncSqliteSaver against the same path, and confirm
    the checkpointed state is still there -- proving this isn't just an
    in-process cache.
    """
    db_path = str(tmp_path / "checkpoints.sqlite")
    config = {"configurable": {"thread_id": "thread-durable"}}

    saver1 = await get_checkpointer(db_path)
    try:
        builder = StateGraph(_TinyState)
        builder.add_node("tiny", _tiny_node)
        builder.add_edge(START, "tiny")
        builder.add_edge("tiny", END)
        graph1 = builder.compile(checkpointer=saver1)
        await graph1.ainvoke({"value": 10}, config)
    finally:
        await saver1.conn.close()

    saver2 = await get_checkpointer(db_path)
    try:
        builder2 = StateGraph(_TinyState)
        builder2.add_node("tiny", _tiny_node)
        builder2.add_edge(START, "tiny")
        builder2.add_edge("tiny", END)
        graph2 = builder2.compile(checkpointer=saver2)

        state = await graph2.aget_state(config)
        assert state is not None
        assert state.values == {"value": 11}
    finally:
        await saver2.conn.close()


async def test_get_checkpointer_reads_env_var_when_path_omitted(tmp_path, monkeypatch):
    db_path = str(tmp_path / "from_env.sqlite")
    monkeypatch.setenv(CHECKPOINT_PATH_ENV_VAR, db_path)

    saver = await get_checkpointer()
    try:
        builder = StateGraph(_TinyState)
        builder.add_node("tiny", _tiny_node)
        builder.add_edge(START, "tiny")
        builder.add_edge("tiny", END)
        graph = builder.compile(checkpointer=saver)
        await graph.ainvoke({"value": 1}, {"configurable": {"thread_id": "t"}})

        assert os.path.exists(db_path)
    finally:
        await saver.conn.close()


async def test_explicit_path_overrides_env_var(tmp_path, monkeypatch):
    env_path = str(tmp_path / "env.sqlite")
    explicit_path = str(tmp_path / "explicit.sqlite")
    monkeypatch.setenv(CHECKPOINT_PATH_ENV_VAR, env_path)

    saver = await get_checkpointer(explicit_path)
    try:
        assert not os.path.exists(env_path)
        assert os.path.exists(explicit_path)
    finally:
        await saver.conn.close()


def test_default_checkpoint_path_matches_spec():
    assert DEFAULT_CHECKPOINT_PATH == "./tapestry_checkpoints.sqlite"


def test_checkpoint_path_env_var_name():
    assert CHECKPOINT_PATH_ENV_VAR == "TAPESTRY_CHECKPOINT_PATH"
