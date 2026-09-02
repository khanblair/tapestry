"""Tests for `tapestry.adapters.web_adapter.api` — the third chat surface.

Deliberately end-to-end, per the task brief: every test runs against a REAL
temp-file SQLite backend (`TAPESTRY_DB_PATH`) and a REAL checkpointer
(`TAPESTRY_CHECKPOINT_PATH`), through a REAL `TestClient` driving the actual
FastAPI app returned by `create_app()` — nothing about the HTTP/WS layer,
the event log, or the graph's checkpoint/interrupt/resume mechanics is
mocked. The only mock anywhere in this file is `graph.build.call_model`
(`tests/graph/test_build.py`'s own established pattern) — there is no API
key or network access in this environment, and every other test file in
this repo that exercises the graph mocks exactly that one seam.

Isolation, and why it matters here specifically
-------------------------------------------------
`graph.build.PERSONAS` is a module-level dict loaded once at import time
from the REAL `personas/*.yaml` directory at the repo root. This file's
persona-mutating endpoints (create/update/pause-all) write real YAML to
disk and then refresh that exact dict in place (see `api.py`'s judgment
call 8) — so a careless test here would permanently rewrite the real
`personas/ada.yaml` etc. and could also leak a mutated `PERSONAS` dict into
`tests/graph/test_build.py`'s own assertions if both run in the same pytest
session. Two fixtures below exist specifically to prevent that:

- `personas_dir` copies the real `personas/*.yaml` files into a `tmp_path`
  directory and points `TAPESTRY_PERSONAS_DIR` at the copy — every write
  in this file lands there, never on the real files.
- `restore_graph_personas` (autouse) snapshots `graph.build.PERSONAS`
  before each test and restores it byte-for-byte after, regardless of
  pass/fail, so no test in this file can leak a mutated registry into any
  other test file in the same session.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from langgraph.types import Command

from tapestry.adapters.web_adapter import api
from tapestry.core import events as events_module
from tapestry.core.personas import Persona
from tapestry.graph import build as graph_build
from tapestry.models.litellm_client import ModelResponse
from tapestry.tools.file_editor import ToolResult

REAL_PERSONAS_DIR = api._REPO_ROOT / "personas"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def personas_dir(tmp_path):
    directory = tmp_path / "personas"
    directory.mkdir()
    for yaml_path in REAL_PERSONAS_DIR.glob("*.yaml"):
        shutil.copy(yaml_path, directory / yaml_path.name)
    return directory


@pytest.fixture(autouse=True)
def restore_graph_personas():
    original = dict(graph_build.PERSONAS)
    yield
    graph_build.PERSONAS.clear()
    graph_build.PERSONAS.update(original)


@pytest.fixture
def app(monkeypatch, tmp_path, personas_dir):
    db_path = str(tmp_path / "tapestry.sqlite")
    monkeypatch.setenv("TAPESTRY_DB_PATH", db_path)
    monkeypatch.setenv("TAPESTRY_CHECKPOINT_PATH", str(tmp_path / "checkpoints.sqlite"))
    monkeypatch.setenv("TAPESTRY_PERSONAS_DIR", str(personas_dir))
    # create_app() itself does no real async work (graph-building happens
    # in the lifespan startup, driven by whatever loop actually serves the
    # app — see api.py's create_app docstring), so it's safe to run to
    # completion in a throwaway loop here.
    built = asyncio.run(api.create_app())
    yield built

    # storage.db.get_connection() caches one real sqlite3.Connection per
    # resolved path, process-wide, and never closes it on its own (see
    # that module's own comment: it's designed for one long-lived path in
    # the real app). Left open across 20+ tests each pointed at their own
    # tmp_path file, that's 20+ leaked file descriptors with no
    # corresponding cleanup. Close this test's connection explicitly
    # rather than relying on GC finalization timing.
    from tapestry.storage import db as db_module

    conn = db_module._connections.pop(db_path, None)
    if conn is not None:
        conn.close()


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def _plain_response(text: str) -> ModelResponse:
    return ModelResponse(text=text, tool_calls=None, model_used="test-model")


def _tool_call_response(text: str, name: str, arguments: dict, call_id: str = "call_1") -> ModelResponse:
    return ModelResponse(
        text=text, tool_calls=[_tool_call(name, arguments, call_id)], model_used="test-model"
    )


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------


def _test_persona(persona_id: str, status: str = "online") -> Persona:
    return Persona(
        id=persona_id,
        name=persona_id.title(),
        role="Tester",
        model="claude-opus-4-6",
        system_prompt="be helpful",
        tools=[],
        mcp_servers=[],
        status=status,
        color="#3B82F6",
    )


# ---------------------------------------------------------------------------
# _derive_persona_status -- live status, derived not written
# (tapestry_mentions_concurrency_status_spec.md §4)
# ---------------------------------------------------------------------------


def test_derive_persona_status_online_with_no_open_turn_passes_through():
    assert api._derive_persona_status(_test_persona("rex", "online"), {}) == "online"


def test_derive_persona_status_offline_with_no_open_turn_passes_through():
    assert api._derive_persona_status(_test_persona("rex", "offline"), {}) == "offline"


def test_derive_persona_status_busy_when_an_open_turn_exists():
    persona = _test_persona("rex", "online")
    open_turns = {"rex": events_module.TapestryEvent(
        id="t1", conversation_id="c1", type="turn/start", timestamp="2026-01-01T00:00:00Z",
        actor="rex", payload={},
    )}
    assert api._derive_persona_status(persona, open_turns) == "busy"


def test_derive_persona_status_paused_wins_over_an_open_turn():
    # nova.yaml ships status: paused deliberately (see personas/nova.yaml's
    # own system_prompt) -- an explicit human/config decision must never be
    # silently overridden by a transient open turn.
    persona = _test_persona("nova", "paused")
    open_turns = {"nova": events_module.TapestryEvent(
        id="t1", conversation_id="c1", type="turn/start", timestamp="2026-01-01T00:00:00Z",
        actor="nova", payload={},
    )}
    assert api._derive_persona_status(persona, open_turns) == "paused"


def test_derive_persona_status_unaffected_by_another_personas_open_turn():
    persona = _test_persona("rex", "online")
    open_turns = {"vex": events_module.TapestryEvent(
        id="t1", conversation_id="c1", type="turn/start", timestamp="2026-01-01T00:00:00Z",
        actor="vex", payload={},
    )}
    assert api._derive_persona_status(persona, open_turns) == "online"


def test_get_personas_reflects_a_currently_open_turn_as_busy(client, monkeypatch):
    hang = asyncio.Event()

    async def never_returns(*args, **kwargs):
        await hang.wait()
        return _plain_response("done")

    monkeypatch.setattr(graph_build, "call_model", never_returns)

    client.post("/api/conversations/dm-rex/messages", json={"text": "hi"})

    import time

    rex = None
    for _ in range(50):
        body = client.get("/api/personas").json()
        rex = next(p for p in body if p["id"] == "rex")
        if rex["status"] == "busy":
            break
        time.sleep(0.05)
    assert rex is not None and rex["status"] == "busy"
    ada = next(p for p in body if p["id"] == "ada")
    assert ada["status"] == "online", "an unrelated persona must not be affected"

    hang.set()  # let the background task unwind before the DB closes


def test_get_personas_returns_the_real_yaml_backed_roster(client, personas_dir):
    res = client.get("/api/personas")
    assert res.status_code == 200
    body = res.json()
    ids = {p["id"] for p in body}
    assert ids == {"ada", "rex", "vex", "nova"}
    ada = next(p for p in body if p["id"] == "ada")
    # camelCase on the wire; systemPrompt/mcp are the real field names,
    # not systemPrompt/mcpServers.
    assert ada["systemPrompt"].startswith("You are Ada")
    assert ada["role"] == "Architect"
    assert ada["mcp"] == []


def test_create_persona_writes_yaml_and_is_immediately_listable(client, personas_dir):
    res = client.post(
        "/api/personas",
        json={
            "name": "Zed",
            "role": "Release Manager",
            "model": "claude-sonnet-5",
            "systemPrompt": "You are Zed.",
            "tools": ["terminal_read_only"],
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["id"] == "zed"
    assert body["status"] == "online"  # default
    assert body["color"]  # default color populated

    yaml_path = personas_dir / "zed.yaml"
    assert yaml_path.exists()

    listed = client.get("/api/personas").json()
    assert any(p["id"] == "zed" for p in listed)

    # graph.build.PERSONAS was refreshed in place -- the new persona is
    # immediately usable by the graph, not just listable over the API.
    assert "zed" in graph_build.PERSONAS


def test_create_persona_never_touches_the_real_repo_personas_dir(client, personas_dir):
    client.post(
        "/api/personas",
        json={"name": "Leak Check", "role": "x", "model": "claude-sonnet-5"},
    )
    assert not (REAL_PERSONAS_DIR / "leak-check.yaml").exists()


def test_update_persona_patch_applies_partial_fields_only(client):
    res = client.patch("/api/personas/vex", json={"role": "Principal QA"})
    assert res.status_code == 200
    body = res.json()
    assert body["role"] == "Principal QA"
    assert body["name"] == "Vex"  # untouched field preserved
    assert body["tools"] == ["terminal_read_only", "test_runner"]  # untouched


def test_update_persona_put_alias_also_works(client):
    res = client.put("/api/personas/nova", json={"status": "online"})
    assert res.status_code == 200
    assert res.json()["status"] == "online"


def test_update_unknown_persona_404s(client):
    res = client.patch("/api/personas/does-not-exist", json={"role": "x"})
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def test_create_conversation_dm_then_list(client):
    res = client.post("/api/conversations", json={"kind": "dm", "personaIds": ["rex"]})
    assert res.status_code == 201
    body = res.json()
    assert body["id"] == "dm-rex"
    assert body["kind"] == "dm"
    assert body["personaIds"] == ["rex"]
    assert body["updatedAt"]

    listed = client.get("/api/conversations").json()
    assert any(c["id"] == "dm-rex" for c in listed)


def test_create_conversation_is_idempotent_for_the_same_dm_id(client):
    first = client.post("/api/conversations", json={"kind": "dm", "personaIds": ["ada"]})
    second = client.post("/api/conversations", json={"kind": "dm", "personaIds": ["ada"]})
    assert first.json()["id"] == second.json()["id"] == "dm-ada"


def test_create_group_conversation(client):
    res = client.post(
        "/api/conversations",
        json={"kind": "group", "name": "#auth-rework", "personaIds": ["ada", "rex", "vex"]},
    )
    assert res.status_code == 201
    body = res.json()
    assert body["kind"] == "group"
    assert body["name"] == "#auth-rework"
    assert set(body["personaIds"]) == {"ada", "rex", "vex"}


def test_create_conversation_rejects_unknown_persona(client):
    res = client.post("/api/conversations", json={"kind": "dm", "personaIds": ["ghost"]})
    assert res.status_code == 422


def test_get_messages_lazily_vivifies_a_dm_conversation_by_id_convention(client):
    # No prior POST /api/conversations at all -- matches
    # new-conversation/page.tsx's real behavior of linking straight to
    # /conversation/dm-<id>.
    res = client.get("/api/conversations/dm-vex/messages")
    assert res.status_code == 200
    assert res.json() == []

    listed = client.get("/api/conversations").json()
    assert any(c["id"] == "dm-vex" for c in listed)


def test_get_messages_404s_for_an_unknown_non_dm_conversation(client):
    res = client.get("/api/conversations/grp-nonexistent/messages")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# sendMessage: never blocks on the agent turn
# ---------------------------------------------------------------------------


def test_send_message_returns_immediately_with_the_user_message(client, monkeypatch):
    # call_model hangs "forever" (until the test ends) -- if sendMessage
    # blocked on the turn, this request would time out the test.
    hang = asyncio.Event()

    async def never_returns(*args, **kwargs):
        await hang.wait()
        raise AssertionError("should not be reached")

    monkeypatch.setattr(graph_build, "call_model", never_returns)

    res = client.post(
        "/api/conversations/dm-rex/messages", json={"text": "hello there"}
    )
    assert res.status_code == 201
    body = res.json()
    assert body["text"] == "hello there"
    assert body["actor"] == "you"
    assert body["conversationId"] == "dm-rex"

    hang.set()  # let the background task unwind before the DB closes


def test_send_message_persists_and_appears_in_get_messages(client, monkeypatch):
    monkeypatch.setattr(
        graph_build, "call_model", AsyncMock(return_value=_plain_response("hi from rex"))
    )
    client.post("/api/conversations/dm-rex/messages", json={"text": "ping"})

    # Give the background asyncio task a moment to run inside the
    # TestClient's own portal loop.
    import time

    for _ in range(50):
        messages = client.get("/api/conversations/dm-rex/messages").json()
        if any(m["actor"] == "rex" for m in messages):
            break
        time.sleep(0.05)
    else:
        messages = client.get("/api/conversations/dm-rex/messages").json()

    texts = [m["text"] for m in messages]
    assert "ping" in texts
    assert "hi from rex" in texts


# ---------------------------------------------------------------------------
# The critical end-to-end path: send -> WS "message" frames -> interrupt
# surfaces -> answer -> resume -> tool runs exactly once -> final reply
# arrives over the WS too.
# ---------------------------------------------------------------------------


def test_full_turn_over_websocket_with_approval_interrupt_and_resume(client, monkeypatch):
    call_count = {"file_editor": 0}

    async def fake_file_editor(arguments: dict) -> ToolResult:
        call_count["file_editor"] += 1
        return ToolResult(text="wrote the file", is_error=False)

    propose = _tool_call_response(
        "I'll create the file.",
        "file_editor",
        {"command": "create", "path": "/tmp/x.txt", "file_text": "hi"},
    )
    final = _plain_response("Done, file created.")
    call_model_mock = AsyncMock(side_effect=[propose, final])
    monkeypatch.setattr(graph_build, "call_model", call_model_mock)
    monkeypatch.setitem(graph_build.TOOL_REGISTRY, "file_editor", fake_file_editor)

    conversation_id = "dm-rex"

    with client.websocket_connect(f"/ws/conversations/{conversation_id}") as ws:
        send_res = client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"text": "please create /tmp/x.txt"},
        )
        assert send_res.status_code == 201

        # Drain frames until the approval prompt arrives as a "message"
        # frame carrying `.approval` (this is what
        # ConversationView.tsx's live subscription actually reacts to).
        approval_payload = None
        for _ in range(200):
            frame = ws.receive_json()
            if frame["type"] == "message" and frame["payload"].get("approval"):
                approval_payload = frame["payload"]["approval"]
                break
        assert approval_payload is not None, "approval prompt never arrived over the WS"
        assert call_count["file_editor"] == 0, "tool must not run while paused"

        question_id = approval_payload["id"]

        answer_res = client.post(
            f"/api/conversations/{conversation_id}/ask/{question_id}/answer",
            json={"selected": ["approve"]},
        )
        assert answer_res.status_code == 204

        final_text = None
        for _ in range(200):
            frame = ws.receive_json()
            if frame["type"] == "message" and frame["payload"].get("text") == "Done, file created.":
                final_text = frame["payload"]["text"]
                break
        assert final_text == "Done, file created."

    assert call_count["file_editor"] == 1, "tool must run exactly once, after resume"

    logged = events_module.read_events(conversation_id)
    assert sum(1 for e in logged if e.type == "ask/requested") == 1
    assert sum(1 for e in logged if e.type == "ask/answered") == 1
    assert sum(1 for e in logged if e.type == "tool/result") == 1


def test_second_message_rejected_while_first_turn_still_running(client, monkeypatch):
    """`tapestry_mentions_concurrency_status_spec.md` §1's concurrency bug,
    the "actively running" half: proven (before the fix) to silently
    overwrite the checkpoint rather than error. `call_model` hangs so the
    first turn's `turn/start` is durably logged and the background task is
    genuinely still executing when the second `send_message` call arrives.
    """
    hang = asyncio.Event()

    async def never_returns(*args, **kwargs):
        await hang.wait()
        return _plain_response("finally done")

    monkeypatch.setattr(graph_build, "call_model", never_returns)

    conversation_id = "dm-rex"
    first = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"text": "first"}
    )
    assert first.status_code == 201

    second = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"text": "second"}
    )
    assert second.status_code == 409
    assert "turn in progress" in second.json()["detail"]

    # only the first message was ever recorded -- the rejected send must
    # not have appended anything to the log.
    logged_texts = [
        e.payload.get("text") for e in events_module.read_events(conversation_id)
        if e.type == "user/message"
    ]
    assert logged_texts == ["first"]

    hang.set()  # let the background task unwind before the DB closes


def test_second_message_rejected_while_first_turn_paused_at_approval_and_original_still_resumable(
    client, monkeypatch
):
    """The exact clobber scenario this bug was found from: before the fix,
    a fresh `new_state()` while a turn is paused at a real approval
    `interrupt()` silently overwrote the checkpoint (`pending_tool_call` ->
    `None`, `interrupts` -> `[]`, no exception) and orphaned the original
    approval so resuming it later returned an unrelated turn's result
    instead of erroring or honoring the human's actual decision.
    """
    call_count = {"file_editor": 0}

    async def fake_file_editor(arguments: dict) -> ToolResult:
        call_count["file_editor"] += 1
        return ToolResult(text="wrote the file", is_error=False)

    propose = _tool_call_response(
        "I'll create the file.",
        "file_editor",
        {"command": "create", "path": "/tmp/x.txt", "file_text": "hi"},
    )
    final = _plain_response("Done, file created.")
    monkeypatch.setattr(graph_build, "call_model", AsyncMock(side_effect=[propose, final]))
    monkeypatch.setitem(graph_build.TOOL_REGISTRY, "file_editor", fake_file_editor)

    conversation_id = "dm-rex"
    send_res = client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"text": "please create /tmp/x.txt"},
    )
    assert send_res.status_code == 201

    import time

    pending_id = None
    for _ in range(50):
        messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
        approvals = [m for m in messages if m.get("approval")]
        if approvals:
            pending_id = approvals[0]["approval"]["id"]
            break
        time.sleep(0.05)
    assert pending_id is not None, "approval never appeared"
    assert call_count["file_editor"] == 0

    # A second, unrelated message arrives while that approval is still
    # pending -- must be rejected, not silently clobber the checkpoint.
    clobber_res = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"text": "never mind, ignore that"}
    )
    assert clobber_res.status_code == 409

    # The ORIGINAL approval must still be exactly where it was and still
    # correctly resumable -- proving nothing overwrote it.
    answer_res = client.post(
        f"/api/conversations/{conversation_id}/ask/{pending_id}/answer",
        json={"selected": ["approve"]},
    )
    assert answer_res.status_code == 204

    texts: list[str] = []
    for _ in range(50):
        messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
        texts = [m["text"] for m in messages]
        if "Done, file created." in texts:
            break
        time.sleep(0.05)
    assert call_count["file_editor"] == 1, "the original approval's tool call must actually run"
    assert "Done, file created." in texts
    # the rejected second send must never have been recorded or answered
    assert "never mind, ignore that" not in texts


def test_answer_ask_batch_endpoint_resumes_too(client, monkeypatch):
    call_count = {"terminal": 0}

    async def fake_terminal(arguments: dict) -> ToolResult:
        call_count["terminal"] += 1
        return ToolResult(text="ran it", is_error=False)

    propose = _tool_call_response("Running a command.", "terminal", {"command": "echo hi"})
    final = _plain_response("Ran it.")
    call_model_mock = AsyncMock(side_effect=[propose, final])
    monkeypatch.setattr(graph_build, "call_model", call_model_mock)
    monkeypatch.setitem(graph_build.TOOL_REGISTRY, "terminal", fake_terminal)

    conversation_id = "dm-rex"
    client.post(f"/api/conversations/{conversation_id}/messages", json={"text": "run echo hi"})

    import time

    pending_id = None
    for _ in range(50):
        messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
        approvals = [m for m in messages if m.get("approval")]
        if approvals:
            pending_id = approvals[0]["approval"]["id"]
            break
        time.sleep(0.05)
    assert pending_id is not None

    res = client.post(
        f"/api/conversations/{conversation_id}/ask/answers",
        json={"answers": [{"id": pending_id, "selected": ["approve"]}]},
    )
    assert res.status_code == 204

    for _ in range(50):
        if call_count["terminal"] == 1:
            break
        time.sleep(0.05)
    assert call_count["terminal"] == 1


def test_graph_thread_id_for_question_finds_the_recording_asks_own_thread(client):
    # `client` fixture used only for its DB isolation (TAPESTRY_DB_PATH),
    # not for any HTTP call -- append_event needs the isolated connection.
    events_module.append_event(
        "conv-1",
        "ask/requested",
        actor="system",
        payload={
            "questions": [{"id": "q1", "question": "approve?"}],
            "graph_thread_id": "conv-1::mention::rex::m1",
        },
    )
    assert api._graph_thread_id_for_question("conv-1", "q1") == "conv-1::mention::rex::m1"


def test_graph_thread_id_for_question_defaults_to_conversation_id_when_absent(client):
    # Predates graph_thread_id existing on this payload.
    events_module.append_event(
        "conv-1",
        "ask/requested",
        actor="system",
        payload={"questions": [{"id": "q1", "question": "approve?"}]},
    )
    assert api._graph_thread_id_for_question("conv-1", "q1") == "conv-1"


def test_graph_thread_id_for_question_defaults_to_conversation_id_when_unknown(client):
    assert api._graph_thread_id_for_question("conv-1", "does-not-exist") == "conv-1"


def test_answer_ask_with_no_pending_approval_returns_409(client, monkeypatch):
    # api._resume_with_answer polls briefly before giving up (a real
    # client can race the approval WS notification against the
    # checkpoint actually being persisted as paused -- see that
    # function's own docstring). Shorten the budget so this true-negative
    # case doesn't have to sit through the full poll window.
    monkeypatch.setattr(api, "_RESUME_POLL_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(api, "_RESUME_POLL_INTERVAL_SECONDS", 0.05)

    res = client.post(
        "/api/conversations/dm-rex/ask/answers",
        json={"answers": [{"id": "bogus", "selected": ["approve"]}]},
    )
    assert res.status_code == 409


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_finds_messages_and_personas(client, monkeypatch):
    monkeypatch.setattr(
        graph_build, "call_model", AsyncMock(return_value=_plain_response("the scope is narrowed"))
    )
    client.post(
        "/api/conversations/dm-rex/messages", json={"text": "please narrow the oauth scope"}
    )

    import time

    for _ in range(50):
        results = client.get("/api/search", params={"q": "narrow"}).json()
        if results["messages"]:
            break
        time.sleep(0.05)

    results = client.get("/api/search", params={"q": "narrow"}).json()
    assert any("narrow" in m["snippet"].lower() for m in results["messages"])

    persona_results = client.get("/api/search", params={"q": "Architect"}).json()
    assert any(p["persona"]["id"] == "ada" for p in persona_results["personas"])


def test_search_with_empty_query_returns_empty_results(client):
    res = client.get("/api/search", params={"q": ""})
    assert res.status_code == 200
    assert res.json() == {"messages": [], "personas": []}


# ---------------------------------------------------------------------------
# Pending asks
# ---------------------------------------------------------------------------


def test_pending_asks_lists_unanswered_approvals_across_conversations(client, monkeypatch):
    propose = _tool_call_response(
        "Creating a file.", "file_editor", {"command": "create", "path": "/tmp/y.txt"}
    )
    monkeypatch.setattr(graph_build, "call_model", AsyncMock(return_value=propose))
    monkeypatch.setitem(
        graph_build.TOOL_REGISTRY,
        "file_editor",
        AsyncMock(return_value=ToolResult(text="ok", is_error=False)),
    )

    client.post("/api/conversations/dm-rex/messages", json={"text": "create /tmp/y.txt"})

    import time

    for _ in range(50):
        pending = client.get("/api/asks/pending").json()
        if pending:
            break
        time.sleep(0.05)

    pending = client.get("/api/asks/pending").json()
    assert any(p["conversationId"] == "dm-rex" for p in pending)


# ---------------------------------------------------------------------------
# Agents pause-all
# ---------------------------------------------------------------------------


def test_pause_all_agents_sets_every_persona_paused_and_persists(client, personas_dir):
    res = client.post("/api/agents/pause-all")
    assert res.status_code == 204

    listed = client.get("/api/personas").json()
    assert all(p["status"] == "paused" for p in listed)
    assert all(p.status == "paused" for p in graph_build.PERSONAS.values())

    # Persisted to the (isolated) yaml files, not just in memory.
    import yaml as yaml_module

    for yaml_path in personas_dir.glob("*.yaml"):
        data = yaml_module.safe_load(yaml_path.read_text())
        assert data["status"] == "paused"


def test_pause_alias_path_also_works(client):
    res = client.post("/api/agents/pause")
    assert res.status_code == 204
    assert all(p["status"] == "paused" for p in client.get("/api/personas").json())


# ---------------------------------------------------------------------------
# Per-persona resume, and paused-persona gating
# (tapestry_mentions_concurrency_status_spec.md §4/§5 decision 2)
# ---------------------------------------------------------------------------


def test_resume_agent_sets_exactly_that_persona_online_and_persists(client, personas_dir):
    # nova.yaml ships status: paused by design (see its own system_prompt).
    res = client.post("/api/agents/nova/resume")
    assert res.status_code == 204

    listed = {p["id"]: p["status"] for p in client.get("/api/personas").json()}
    assert listed["nova"] == "online"
    assert listed["rex"] == "online", "every other persona's status must be untouched"

    import yaml as yaml_module

    data = yaml_module.safe_load((personas_dir / "nova.yaml").read_text())
    assert data["status"] == "online"


def test_resume_agent_on_an_already_online_persona_is_a_no_op(client):
    res = client.post("/api/agents/rex/resume")
    assert res.status_code == 204
    assert next(p for p in client.get("/api/personas").json() if p["id"] == "rex")["status"] == "online"


def test_resume_agent_unknown_persona_404s(client):
    res = client.post("/api/agents/does-not-exist/resume")
    assert res.status_code == 404


def test_pause_all_then_resume_one_leaves_the_rest_paused(client):
    client.post("/api/agents/pause-all")
    client.post("/api/agents/nova/resume")

    listed = {p["id"]: p["status"] for p in client.get("/api/personas").json()}
    assert listed["nova"] == "online"
    assert listed["rex"] == "paused"
    assert listed["ada"] == "paused"
    assert listed["vex"] == "paused"


def test_send_message_to_a_paused_persona_is_rejected(client):
    client.post("/api/agents/pause-all")

    res = client.post("/api/conversations/dm-rex/messages", json={"text": "hi rex"})

    assert res.status_code == 409
    assert "paused" in res.json()["detail"]
    # the rejected message must never have been recorded as a user/message
    # (conversation/created from lazy-vivifying the DM is fine/expected)
    assert not any(
        e.type == "user/message" for e in events_module.read_events("dm-rex")
    )


def test_send_message_works_again_after_resuming_the_paused_persona(client, monkeypatch):
    monkeypatch.setattr(
        graph_build, "call_model", AsyncMock(return_value=_plain_response("hi from rex"))
    )
    client.post("/api/agents/pause-all")
    client.post("/api/agents/rex/resume")

    res = client.post("/api/conversations/dm-rex/messages", json={"text": "hi rex"})

    assert res.status_code == 201


# ---------------------------------------------------------------------------
# related_task_id round-trips through an approval ask
# ---------------------------------------------------------------------------


def test_approval_question_carries_related_task_id(client, monkeypatch):
    propose = _tool_call_response(
        "I'll create the file.",
        "file_editor",
        {"command": "create", "path": "/tmp/z.txt", "file_text": "hi"},
    )
    monkeypatch.setattr(graph_build, "call_model", AsyncMock(return_value=propose))
    monkeypatch.setitem(
        graph_build.TOOL_REGISTRY,
        "file_editor",
        AsyncMock(return_value=ToolResult(text="wrote it", is_error=False)),
    )

    conversation_id = "dm-rex"
    client.post(
        f"/api/conversations/{conversation_id}/messages",
        json={"text": "please create /tmp/z.txt"},
    )

    import time

    approval = None
    for _ in range(50):
        messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
        approvals = [m for m in messages if m.get("approval")]
        if approvals:
            approval = approvals[0]["approval"]
            break
        time.sleep(0.05)
    assert approval is not None, "approval question never appeared"
    assert approval["relatedTaskId"]

    logged = events_module.read_events(conversation_id)
    task_started = next(e for e in logged if e.type == "task/started")
    assert approval["relatedTaskId"] == task_started.payload["task_id"]


# ---------------------------------------------------------------------------
# tool/result and task/diff_ready synthetic messages
# ---------------------------------------------------------------------------


def test_tool_result_event_becomes_a_synthetic_activity_message(client):
    conversation_id = "dm-rex"
    client.get(f"/api/conversations/{conversation_id}/messages")  # lazily vivify

    events_module.append_event(
        conversation_id,
        "tool/result",
        actor="rex",
        payload={
            "task_id": "task-1",
            "tool_name": "file_editor",
            "arguments": {"command": "create", "path": "/tmp/a.txt"},
            "text": "x" * 500,
            "is_error": False,
        },
    )

    messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
    activity_messages = [m for m in messages if m.get("activity")]
    assert len(activity_messages) == 1
    assert activity_messages[0]["text"] == ""
    activity = activity_messages[0]["activity"]
    assert activity["done"] is True
    assert "/tmp/a.txt" in activity["label"]
    assert len(activity["result"]) == 200


def test_tool_result_error_is_reflected_in_the_label(client):
    conversation_id = "dm-rex"
    client.get(f"/api/conversations/{conversation_id}/messages")

    events_module.append_event(
        conversation_id,
        "tool/result",
        actor="rex",
        payload={
            "task_id": "task-1",
            "tool_name": "terminal",
            "arguments": {"command": "false"},
            "text": "command failed",
            "is_error": True,
        },
    )

    messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
    activity = next(m["activity"] for m in messages if m.get("activity"))
    assert "(failed)" in activity["label"]


def test_diff_ready_with_real_capture_becomes_a_synthetic_diff_message(client):
    conversation_id = "dm-rex"
    client.get(f"/api/conversations/{conversation_id}/messages")

    events_module.append_event(
        conversation_id,
        "task/diff_ready",
        actor="rex",
        payload={
            "task_id": "task-2",
            "files_changed": ["a.py", "b.py"],
            "diff_summary": "changed things",
            "additions": 10,
            "deletions": 3,
            "truncated": False,
            "files": [
                {
                    "name": "a.py",
                    "additions": 10,
                    "deletions": 3,
                    "lines": [{"type": "add", "line_number": 1, "content": "x = 1"}],
                }
            ],
        },
    )

    messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
    diff_messages = [m for m in messages if m.get("diff")]
    assert len(diff_messages) == 1
    assert diff_messages[0]["text"] == ""
    diff = diff_messages[0]["diff"]
    assert diff["taskId"] == "task-2"
    assert diff["files"] == 2
    assert diff["add"] == 10
    assert diff["del"] == 3


def test_diff_ready_with_failed_capture_is_omitted_never_fabricated(client):
    conversation_id = "dm-rex"
    client.get(f"/api/conversations/{conversation_id}/messages")

    events_module.append_event(
        conversation_id,
        "task/diff_ready",
        actor="rex",
        payload={
            "task_id": "task-3",
            "files_changed": [],
            "diff_summary": "changed things",
            "additions": None,
            "deletions": None,
            "truncated": False,
            "files": [],
        },
    )

    messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
    assert all(m.get("diff") is None for m in messages)


# ---------------------------------------------------------------------------
# Diff detail endpoint
# ---------------------------------------------------------------------------


def _diff_ready_payload(task_id: str, file_name: str, **overrides) -> dict:
    payload = {
        "task_id": task_id,
        "files_changed": [file_name],
        "diff_summary": f"changed {file_name}",
        "additions": 2,
        "deletions": 1,
        "truncated": False,
        "files": [
            {
                "name": file_name,
                "additions": 2,
                "deletions": 1,
                "lines": [
                    {"type": "ctx", "line_number": 1, "content": "def f():"},
                    {"type": "add", "line_number": 2, "content": "    return 1"},
                    {"type": "del", "line_number": 2, "content": "    return 0"},
                ],
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_diff_detail_returns_full_per_file_line_content(client):
    conversation_id = "dm-rex"
    client.get(f"/api/conversations/{conversation_id}/messages")

    events_module.append_event(
        conversation_id,
        "task/diff_ready",
        actor="rex",
        payload=_diff_ready_payload("task-4", "a.py"),
    )

    res = client.get(f"/api/conversations/{conversation_id}/diff/task-4")
    assert res.status_code == 200
    body = res.json()
    assert body["taskId"] == "task-4"
    assert body["fileCount"] == 1
    assert body["additions"] == 2
    assert body["deletions"] == 1
    assert body["files"][0]["name"] == "a.py"
    assert body["files"][0]["lines"][1] == {
        "type": "add",
        "lineNumber": 2,
        "content": "    return 1",
    }


def test_diff_detail_uses_the_most_recent_matching_event(client):
    conversation_id = "dm-rex"
    client.get(f"/api/conversations/{conversation_id}/messages")

    events_module.append_event(
        conversation_id,
        "task/diff_ready",
        actor="rex",
        payload=_diff_ready_payload("task-5", "old.py"),
    )
    events_module.append_event(
        conversation_id,
        "task/diff_ready",
        actor="rex",
        payload=_diff_ready_payload("task-5", "new.py"),
    )

    res = client.get(f"/api/conversations/{conversation_id}/diff/task-5")
    assert res.status_code == 200
    assert res.json()["files"][0]["name"] == "new.py"


def test_diff_detail_404s_when_no_matching_event(client):
    conversation_id = "dm-rex"
    client.get(f"/api/conversations/{conversation_id}/messages")
    res = client.get(f"/api/conversations/{conversation_id}/diff/does-not-exist")
    assert res.status_code == 404


def test_diff_detail_404s_when_capture_failed(client):
    conversation_id = "dm-rex"
    client.get(f"/api/conversations/{conversation_id}/messages")
    events_module.append_event(
        conversation_id,
        "task/diff_ready",
        actor="rex",
        payload={
            "task_id": "task-6",
            "files_changed": [],
            "diff_summary": "x",
            "additions": None,
            "deletions": None,
            "truncated": False,
            "files": [],
        },
    )
    res = client.get(f"/api/conversations/{conversation_id}/diff/task-6")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Cross-conversation activity feed
# ---------------------------------------------------------------------------


def test_activity_recent_reflects_real_events_across_conversations_newest_first(client):
    client.post("/api/conversations", json={"kind": "dm", "personaIds": ["rex"]})
    client.post(
        "/api/conversations",
        json={"kind": "group", "name": "#launch", "personaIds": ["ada", "vex"]},
    )
    group_conv_id = next(
        c["id"] for c in client.get("/api/conversations").json() if c["kind"] == "group"
    )

    events_module.append_event(
        "dm-rex", "task/started", actor="rex", payload={"task_id": "t1", "description": "x"}
    )
    events_module.append_event(
        group_conv_id,
        "task/completed",
        actor="ada",
        payload={"task_id": "t2", "notes": "done"},
    )

    res = client.get("/api/activity")
    assert res.status_code == 200
    recent = res.json()["recent"]
    assert len(recent) == 2
    # Newest first.
    assert recent[0]["label"] == "ada completed a task"
    assert recent[0]["conversationLabel"] == "#launch"
    assert recent[0]["taskId"] == "t2"
    assert recent[1]["label"] == "rex started a task"
    assert recent[1]["conversationId"] == "dm-rex"
    assert recent[1]["taskId"] == "t1"


def test_activity_running_reflects_in_memory_state(client, app):
    client.get("/api/conversations/dm-rex/messages")  # ensure the row exists
    app.state.running_activity["dm-rex"] = {
        "actor": "rex",
        "label": "running file_editor",
        "timestamp": "2025-01-01T00:00:00.000000+00:00",
        "task_id": None,
    }

    res = client.get("/api/activity")
    body = res.json()
    running = [r for r in body["running"] if r["conversationId"] == "dm-rex"]
    assert len(running) == 1
    assert running[0]["label"] == "running file_editor"
    assert running[0]["actor"] == "rex"
    assert running[0]["conversationLabel"] == "dm-rex"


def test_activity_running_reflects_a_real_in_flight_tool_call(client, monkeypatch):
    """End-to-end proof of `_drive_turn` -> `_record_running_activity`
    itself (not just the endpoint's serialization of hand-set state, as in
    `test_activity_running_reflects_in_memory_state` above): a real
    `streaming.emit("tool/status", ...)` custom frame, produced by a real
    `execute_node` run, must be what populates and clears this entry.

    `test_runner` (vex's tool, not gated by `TOOLS_REQUIRING_APPROVAL`) so
    the turn goes straight persona -> execute with no interrupt to
    negotiate, keeping this test to one exchange.
    """
    hang = asyncio.Event()

    async def slow_test_runner(arguments: dict) -> ToolResult:
        await hang.wait()
        return ToolResult(text="all green", is_error=False)

    propose = _tool_call_response("Running the tests.", "test_runner", {"command": "pytest"})
    final = _plain_response("Tests passed.")
    monkeypatch.setattr(graph_build, "call_model", AsyncMock(side_effect=[propose, final]))
    monkeypatch.setitem(graph_build.TOOL_REGISTRY, "test_runner", slow_test_runner)

    conversation_id = "dm-vex"
    client.post(f"/api/conversations/{conversation_id}/messages", json={"text": "run the tests"})

    import time

    running_entry = None
    for _ in range(50):
        activity = client.get("/api/activity").json()
        matches = [r for r in activity["running"] if r["conversationId"] == conversation_id]
        if matches:
            running_entry = matches[0]
            break
        time.sleep(0.05)
    assert running_entry is not None, "running entry never appeared"
    assert running_entry["label"] == "running test_runner"
    assert running_entry["actor"] == "vex"

    hang.set()  # let the tool call (and the rest of the turn) finish

    for _ in range(50):
        activity = client.get("/api/activity").json()
        matches = [r for r in activity["running"] if r["conversationId"] == conversation_id]
        if not matches:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("running entry never cleared after the tool call finished")


# ---------------------------------------------------------------------------
# System status
# ---------------------------------------------------------------------------


def test_status_reflects_env_vars_and_live_metamcp_tools(client, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "d-token")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "o-key")

    tools = [
        {"name": "GitHub__list_repos"},
        {"name": "GitHub__create_pr"},
        {"name": "Deploy__trigger"},
    ]
    monkeypatch.setattr(api.MetaMCPClient, "list_tools", AsyncMock(return_value=tools))

    res = client.get("/api/status")
    assert res.status_code == 200
    body = res.json()

    platforms = {p["name"]: p for p in body["platforms"]}
    assert platforms["Discord"]["connected"] is True
    assert platforms["Telegram"]["connected"] is False
    assert platforms["Web"] == {
        "name": "Web",
        "detail": "Always on",
        "connected": True,
        "alwaysOn": True,
    }

    providers = {p["name"]: p["connected"] for p in body["providers"]}
    assert providers == {
        "Anthropic": True,
        "DeepSeek": False,
        "Gemini": False,
        "Qwen": False,
        "OpenRouter": True,
    }

    assert body["metamcp"] == {"running": True, "serverCount": 2}
    assert {s["name"] for s in body["mcpServers"]} == {"GitHub", "Deploy"}
    assert all(s["connected"] for s in body["mcpServers"])


def test_status_metamcp_failure_never_500s(client, monkeypatch):
    async def boom(self):
        raise api.MetaMCPConfigurationError("no key configured")

    monkeypatch.setattr(api.MetaMCPClient, "list_tools", boom)

    res = client.get("/api/status")
    assert res.status_code == 200
    body = res.json()
    assert body["metamcp"] == {"running": False, "serverCount": 0}
    assert body["mcpServers"] == []


# ---------------------------------------------------------------------------
# Persona new fields (tapestry_modes_models_personas_spec.md §3) round-trip
# ---------------------------------------------------------------------------


def test_create_persona_round_trips_all_new_fields(client, personas_dir):
    res = client.post(
        "/api/personas",
        json={
            "name": "Gale",
            "role": "Guardian",
            "model": "claude-sonnet-5",
            "fallbackModels": ["claude-opus-4-6", "gemini/gemini-3-pro"],
            "guardianModel": "claude-haiku-5",
            "reasoningEffort": "high",
            "defaultMode": "auto",
            "maxTurns": 12,
            "maxDelegationDepth": 3,
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert body["id"] == "gale"
    assert body["fallbackModels"] == ["claude-opus-4-6", "gemini/gemini-3-pro"]
    assert body["guardianModel"] == "claude-haiku-5"
    assert body["reasoningEffort"] == "high"
    assert body["defaultMode"] == "auto"
    assert body["maxTurns"] == 12
    assert body["maxDelegationDepth"] == 3

    fetched = next(p for p in client.get("/api/personas").json() if p["id"] == "gale")
    assert fetched == body

    patched = client.patch("/api/personas/gale", json={"maxTurns": 20})
    assert patched.status_code == 200
    patched_body = patched.json()
    assert patched_body["maxTurns"] == 20
    # Untouched new fields preserved across the partial update.
    assert patched_body["guardianModel"] == "claude-haiku-5"
    assert patched_body["defaultMode"] == "auto"
    assert patched_body["fallbackModels"] == ["claude-opus-4-6", "gemini/gemini-3-pro"]

    refetched = next(p for p in client.get("/api/personas").json() if p["id"] == "gale")
    assert refetched["maxTurns"] == 20


def test_update_persona_rejects_invalid_default_mode(client):
    """`default_mode` must be validated at request-parsing time (422), not
    let a bad literal reach `_update_persona`'s `model_copy(update=...)` --
    pydantic v2's `model_copy` does not re-validate, so an unvalidated bad
    value there would get written straight into the persona's YAML and
    permanently break every subsequent `load_personas()`/`GET /api/personas`
    call. This is a regression test for exactly that failure mode.
    """
    res = client.patch("/api/personas/rex", json={"defaultMode": "not-a-real-mode"})
    assert res.status_code == 422

    # The API must still be healthy afterward -- nothing was written.
    listed = client.get("/api/personas")
    assert listed.status_code == 200
    assert any(p["id"] == "rex" for p in listed.json())


def test_create_persona_defaults_new_fields_when_omitted(client, personas_dir):
    res = client.post(
        "/api/personas", json={"name": "Plain", "role": "x", "model": "claude-sonnet-5"}
    )
    assert res.status_code == 201
    body = res.json()
    assert body["fallbackModels"] == []
    assert body["guardianModel"] is None
    assert body["reasoningEffort"] is None
    assert body["defaultMode"] == "manual"
    assert body["maxTurns"] is None
    assert body["maxDelegationDepth"] is None


# ---------------------------------------------------------------------------
# Conversation.mode / .model -- default persona reflection with no
# mode/changed or persona/model_switched events yet.
# ---------------------------------------------------------------------------


def test_conversation_out_reflects_persona_defaults_with_no_events(client):
    res = client.get("/api/conversations/dm-rex/messages")  # lazily vivify
    assert res.status_code == 200

    listed = client.get("/api/conversations").json()
    conv = next(c for c in listed if c["id"] == "dm-rex")
    # rex.yaml sets no default_mode -> core.personas.Persona's own default.
    assert conv["mode"] == "manual"
    # rex.yaml's own model field, verbatim.
    assert conv["model"] == "deepseek/deepseek-chat"


# ---------------------------------------------------------------------------
# Mode switching (POST /api/conversations/{id}/mode)
# ---------------------------------------------------------------------------


def test_set_conversation_mode_rejects_invalid_mode(client):
    client.get("/api/conversations/dm-rex/messages")  # vivify
    res = client.post(
        "/api/conversations/dm-rex/mode", json={"mode": "not-a-real-mode", "personaId": "rex"}
    )
    assert res.status_code == 422


def test_set_conversation_mode_rejects_persona_not_in_conversation(client):
    client.get("/api/conversations/dm-rex/messages")  # vivify -- only rex is a participant
    res = client.post("/api/conversations/dm-rex/mode", json={"mode": "auto", "personaId": "ada"})
    assert res.status_code == 422


def test_set_conversation_mode_appends_event_and_reflected_in_list(client):
    client.get("/api/conversations/dm-rex/messages")  # vivify
    res = client.post("/api/conversations/dm-rex/mode", json={"mode": "auto", "personaId": "rex"})
    assert res.status_code == 204

    logged = events_module.read_events("dm-rex")
    mode_events = [e for e in logged if e.type == "mode/changed"]
    assert len(mode_events) == 1
    assert mode_events[0].payload == {"mode": "auto", "persona_id": "rex"}

    listed = client.get("/api/conversations").json()
    conv = next(c for c in listed if c["id"] == "dm-rex")
    assert conv["mode"] == "auto"


# ---------------------------------------------------------------------------
# Model switching (POST /api/conversations/{id}/model)
# ---------------------------------------------------------------------------


def test_set_conversation_model_rejects_persona_not_in_conversation(client):
    client.get("/api/conversations/dm-rex/messages")  # vivify
    res = client.post(
        "/api/conversations/dm-rex/model",
        json={"model": "claude-opus-4-6", "personaId": "ada", "scope": "session"},
    )
    assert res.status_code == 422


def test_set_conversation_model_session_scope_appends_event_and_reflected_in_list(client):
    client.get("/api/conversations/dm-rex/messages")  # vivify
    res = client.post(
        "/api/conversations/dm-rex/model",
        json={"model": "claude-opus-4-6", "personaId": "rex", "scope": "session"},
    )
    assert res.status_code == 204

    logged = events_module.read_events("dm-rex")
    switch_events = [e for e in logged if e.type == "persona/model_switched"]
    assert len(switch_events) == 1
    assert switch_events[0].payload == {"model": "claude-opus-4-6", "persona_id": "rex"}

    listed = client.get("/api/conversations").json()
    conv = next(c for c in listed if c["id"] == "dm-rex")
    assert conv["model"] == "claude-opus-4-6"


def test_set_conversation_model_once_scope_overrides_the_next_turns_model_call(
    client, monkeypatch
):
    call_model_mock = AsyncMock(return_value=_plain_response("hi"))
    monkeypatch.setattr(graph_build, "call_model", call_model_mock)

    conversation_id = "dm-rex"
    client.get(f"/api/conversations/{conversation_id}/messages")  # vivify

    res = client.post(
        f"/api/conversations/{conversation_id}/model",
        json={"model": "claude-opus-4-6", "personaId": "rex", "scope": "once"},
    )
    assert res.status_code == 204

    send_res = client.post(
        f"/api/conversations/{conversation_id}/messages", json={"text": "hello"}
    )
    assert send_res.status_code == 201

    import time

    for _ in range(50):
        if call_model_mock.await_count >= 1:
            break
        time.sleep(0.05)

    assert call_model_mock.await_count >= 1
    _, kwargs = call_model_mock.call_args_list[0]
    assert kwargs["model"] == "claude-opus-4-6"

    # A "once" override is genuinely ephemeral -- never durably logged as a
    # persona/model_switched event, unlike session scope above.
    logged = events_module.read_events(conversation_id)
    assert not any(e.type == "persona/model_switched" for e in logged)


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


def test_cors_allows_the_nextjs_dev_origin(client):
    res = client.get("/api/personas", headers={"Origin": "http://localhost:3000"})
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"
