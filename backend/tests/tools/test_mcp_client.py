"""Tests for tapestry.tools.mcp_client.MetaMCPClient.

Not in the original project tree (backend/tests/tools/ only listed
test_file_editor.py and test_terminal.py) — added because there is no
guarantee a live metamcp instance is reachable during CI/local test runs,
so the official `mcp` SDK's client pieces are mocked here rather than
exercised against a real server. (A real, live round trip against a
locally spun-up mcp.server.mcpserver.MCPServer was used once, by hand,
to verify the exact API shape mcp_client.py is written against — see
that module's docstring — but that's not repeated here as an automated
test to avoid a spurious extra dependency/flakiness source in CI.)

Patches the SDK names as imported into tapestry.tools.mcp_client (not the
mcp package itself), since that's what the module actually calls.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tapestry.tools.file_editor import ToolResult
from tapestry.tools.mcp_client import MetaMCPClient


class _FakeTool:
    def __init__(self, name: str, description: str, input_schema: dict):
        self.name = name
        self.description = description
        self.input_schema = input_schema

    def model_dump(self):
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


class _FakeListToolsResult:
    def __init__(self, tools):
        self.tools = tools


class _FakeTextContent:
    def __init__(self, text: str):
        self.text = text


class _FakeCallToolResult:
    def __init__(self, content, is_error, structured_content=None):
        self.content = content
        self.is_error = is_error
        self.structured_content = structured_content


def _make_session_mock(list_tools_result=None, call_tool_result=None):
    """A fake ClientSession instance usable as `async with ... as session`."""
    session = MagicMock(name="ClientSession-instance")
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.initialize = AsyncMock(return_value=None)
    session.list_tools = AsyncMock(return_value=list_tools_result)
    session.call_tool = AsyncMock(return_value=call_tool_result)
    return session


def _make_transport_cm():
    """A fake object usable as `async with streamable_http_client(...) as (read, write)`."""
    cm = MagicMock(name="transport-cm")
    cm.__aenter__ = AsyncMock(return_value=("fake-read-stream", "fake-write-stream"))
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.fixture
def patched_sdk():
    """Patches streamable_http_client, ClientSession, and httpx2.AsyncClient
    as imported into tapestry.tools.mcp_client, and returns the mocks so a
    test can configure return values / assert on call args.
    """
    with (
        patch("tapestry.tools.mcp_client.streamable_http_client") as fake_transport_factory,
        patch("tapestry.tools.mcp_client.ClientSession") as fake_session_cls,
        patch("tapestry.tools.mcp_client.httpx2.AsyncClient") as fake_async_client_cls,
    ):
        transport_cm = _make_transport_cm()
        fake_transport_factory.return_value = transport_cm
        fake_async_client_cls.return_value = MagicMock(name="httpx2-async-client-instance")

        yield {
            "transport_factory": fake_transport_factory,
            "transport_cm": transport_cm,
            "session_cls": fake_session_cls,
            "async_client_cls": fake_async_client_cls,
        }


def test_init_defaults_from_env_vars(monkeypatch):
    monkeypatch.setenv("METAMCP_URL", "http://example.test/metamcp/tapestry/mcp")
    monkeypatch.setenv("METAMCP_API_KEY", "env-key-123")

    client = MetaMCPClient()

    assert client.url == "http://example.test/metamcp/tapestry/mcp"
    assert client.api_key == "env-key-123"


def test_init_explicit_args_override_env_vars(monkeypatch):
    monkeypatch.setenv("METAMCP_URL", "http://example.test/metamcp/tapestry/mcp")
    monkeypatch.setenv("METAMCP_API_KEY", "env-key-123")

    client = MetaMCPClient(url="http://explicit.test/mcp", api_key="explicit-key")

    assert client.url == "http://explicit.test/mcp"
    assert client.api_key == "explicit-key"


@pytest.mark.asyncio
async def test_list_tools_calls_initialize_then_list_tools_and_returns_dicts(patched_sdk):
    fake_tools = [
        _FakeTool("server__toolA", "does A things", {"type": "object"}),
        _FakeTool("server__toolB", "does B things", {"type": "object"}),
    ]
    session = _make_session_mock(list_tools_result=_FakeListToolsResult(fake_tools))
    patched_sdk["session_cls"].return_value = session

    client = MetaMCPClient(url="http://example.test/mcp", api_key="k")
    result = await client.list_tools()

    session.initialize.assert_awaited_once()
    session.list_tools.assert_awaited_once()
    assert result == [
        {"name": "server__toolA", "description": "does A things", "input_schema": {"type": "object"}},
        {"name": "server__toolB", "description": "does B things", "input_schema": {"type": "object"}},
    ]


@pytest.mark.asyncio
async def test_list_tools_connects_with_bearer_auth_header(patched_sdk):
    session = _make_session_mock(list_tools_result=_FakeListToolsResult([]))
    patched_sdk["session_cls"].return_value = session

    client = MetaMCPClient(url="http://example.test/mcp", api_key="my-secret-key")
    await client.list_tools()

    patched_sdk["async_client_cls"].assert_called_once_with(
        headers={"Authorization": "Bearer my-secret-key"}
    )
    patched_sdk["transport_factory"].assert_called_once()
    call_args = patched_sdk["transport_factory"].call_args
    assert call_args.args[0] == "http://example.test/mcp"


@pytest.mark.asyncio
async def test_call_tool_passes_name_and_arguments_through(patched_sdk):
    call_result = _FakeCallToolResult(content=[_FakeTextContent("42")], is_error=False)
    session = _make_session_mock(call_tool_result=call_result)
    patched_sdk["session_cls"].return_value = session

    client = MetaMCPClient(url="http://example.test/mcp", api_key="k")
    result = await client.call_tool("server__add", {"a": 1, "b": 2})

    session.call_tool.assert_awaited_once_with("server__add", {"a": 1, "b": 2})
    assert isinstance(result, ToolResult)
    assert result.text == "42"
    assert result.is_error is False


@pytest.mark.asyncio
async def test_call_tool_maps_is_error_true(patched_sdk):
    call_result = _FakeCallToolResult(content=[_FakeTextContent("boom")], is_error=True)
    session = _make_session_mock(call_tool_result=call_result)
    patched_sdk["session_cls"].return_value = session

    client = MetaMCPClient(url="http://example.test/mcp", api_key="k")
    result = await client.call_tool("server__fails", {})

    assert result.is_error is True
    assert result.text == "boom"


@pytest.mark.asyncio
async def test_call_tool_joins_multiple_text_content_blocks(patched_sdk):
    call_result = _FakeCallToolResult(
        content=[_FakeTextContent("hello "), _FakeTextContent("world")],
        is_error=False,
    )
    session = _make_session_mock(call_tool_result=call_result)
    patched_sdk["session_cls"].return_value = session

    client = MetaMCPClient(url="http://example.test/mcp", api_key="k")
    result = await client.call_tool("server__concat", {})

    assert result.text == "hello world"


@pytest.mark.asyncio
async def test_call_tool_falls_back_to_structured_content_when_no_text(patched_sdk):
    """A tool result can carry only structured_content with an empty
    content list — the client must not silently return an empty string.
    """
    call_result = _FakeCallToolResult(
        content=[], is_error=False, structured_content={"result": 5}
    )
    session = _make_session_mock(call_tool_result=call_result)
    patched_sdk["session_cls"].return_value = session

    client = MetaMCPClient(url="http://example.test/mcp", api_key="k")
    result = await client.call_tool("server__structured_only", {})

    assert json.loads(result.text) == {"result": 5}
    assert result.is_error is False


@pytest.mark.asyncio
async def test_each_call_opens_and_tears_down_its_own_session(patched_sdk):
    """Session-per-call design: no session/transport is cached across
    calls, so metamcp's server-side idle session eviction (see
    ANALYSIS-metamcp.md §2) is sidestepped rather than worked around.
    """
    session = _make_session_mock(
        list_tools_result=_FakeListToolsResult([]),
        call_tool_result=_FakeCallToolResult(content=[], is_error=False),
    )
    patched_sdk["session_cls"].return_value = session

    client = MetaMCPClient(url="http://example.test/mcp", api_key="k")
    await client.list_tools()
    await client.call_tool("server__noop", {})

    assert session.__aenter__.await_count == 2
    assert session.__aexit__.await_count == 2
    assert patched_sdk["transport_cm"].__aenter__.await_count == 2
    assert patched_sdk["transport_cm"].__aexit__.await_count == 2
