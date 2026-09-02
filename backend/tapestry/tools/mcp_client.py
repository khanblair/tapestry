"""Standard-MCP client for the already-running metamcp aggregator.

Per ``docs/vendor-research/ANALYSIS-metamcp.md``: a metamcp Endpoint is a
completely standard MCP server built on the official
``@modelcontextprotocol/sdk`` (proven from metamcp's own source, not
inferred), reachable over Streamable HTTP. So this shim is a plain use of
the official Python ``mcp`` SDK's Streamable HTTP client — no custom
protocol, no metamcp-specific client code.

IMPORTANT — the ANALYSIS file's connection sketch is stale against the
``mcp`` version this project actually resolves
--------------------------------------------------------------------------
``pyproject.toml`` pins ``mcp>=1.2.0`` with no upper bound. As of this
writing that resolves to ``mcp==2.1.1`` (verified: pip-installed it
alongside ``openhands-sdk``/``openhands-tools`` in a clean venv), which is
a major-version jump from whatever 1.x line the ANALYSIS sketch was
written against. Trying the sketch literally as written fails immediately:

    >>> from mcp.client.streamable_http import streamablehttp_client
    ImportError: cannot import name 'streamablehttp_client' from
    'mcp.client.streamable_http' (...). Did you mean: 'streamable_http_client'?

Verified (empirically, not guessed) differences from the sketch, by
inspecting the real installed source and then running a full live round
trip against a locally spun-up ``mcp.server.mcpserver.MCPServer`` (mcp 2.x
renamed ``FastMCP`` to ``MCPServer``) over real HTTP with
``initialize()`` -> ``list_tools()`` -> ``call_tool()`` all succeeding:

1. The function is ``streamable_http_client``, not ``streamablehttp_client``.
2. It no longer takes a ``headers=`` kwarg. Its signature is
   ``streamable_http_client(url, *, http_client=None, terminate_on_close=True)``;
   per its own docstring, "*To configure headers, authentication, or other
   HTTP settings, create an httpx2.AsyncClient and pass it here.*" So auth
   is passed via a pre-built HTTP client, not a headers dict.
3. That HTTP client must be an ``httpx2.AsyncClient``, not a plain
   ``httpx.AsyncClient`` — ``httpx2`` is a real, separate PyPI package
   (verified: ``mcp``'s own metadata declares ``Requires-Dist: httpx2>=2.5.0``)
   that this SDK version depends on directly, not a typo for ``httpx``.
   It is declared as an explicit dependency in ``pyproject.toml`` here
   rather than relied on transitively, since we import it directly.
4. The async context manager yields a **2-tuple** ``(read, write)``, not
   the 3-tuple ``(read, write, get_session_id)`` the sketch unpacks.

``mcp`` is tightened from the project's original ``>=1.2.0`` to
``>=2.0,<3`` in ``pyproject.toml`` as part of this shim, since ``tools/``
is the only consumer of this dependency and an unbounded ``>=1.2.0`` would
happily resolve back to a 1.x line this code does not speak.

Session lifecycle
------------------
Per ANALYSIS-metamcp.md §2, metamcp sessions are in-memory per backend
process and get evicted by an idle timer — a long-lived client should not
assume a session lives forever. Rather than cache a session and add
reconnect-on-eviction logic, this client opens a brand new transport +
``ClientSession`` (and calls ``initialize()``) for every ``list_tools``/
``call_tool``, then tears it down. Simpler, and sidesteps the eviction
problem entirely rather than working around it.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tapestry.tools.file_editor import ToolResult

__all__ = ["ToolResult", "MetaMCPClient"]

# Matches the project's own placeholder in backend/.env.example — a
# Tapestry-scoped namespace/endpoint, not metamcp's shared "Public" one.
_DEFAULT_METAMCP_URL = "http://localhost:12008/metamcp/tapestry/mcp"


class MetaMCPClient:
    """Client for one metamcp Endpoint, using the official ``mcp`` SDK.

    ``url``/``api_key`` fall back to the ``METAMCP_URL``/``METAMCP_API_KEY``
    env vars (read at construction time, not import time) when omitted or
    passed as ``None`` — convenient for the normal case of one client per
    process talking to the metamcp instance configured for this deployment,
    while still letting tests/callers point at a different instance
    explicitly.
    """

    def __init__(self, url: str | None = None, api_key: str | None = None) -> None:
        self.url = url if url is not None else os.environ.get("METAMCP_URL", _DEFAULT_METAMCP_URL)
        self.api_key = api_key if api_key is not None else os.environ.get("METAMCP_API_KEY", "")

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {self.api_key}"})
        async with streamable_http_client(self.url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def list_tools(self) -> list[dict]:
        """List every tool exposed by this metamcp Endpoint's namespace.

        Returns the full ``mcp.types.Tool`` payload per tool (``name``,
        ``description``, ``input_schema``, ...) as a plain dict via
        pydantic's ``model_dump()``, rather than a curated subset — cheaper
        than guessing which fields a future caller in ``graph/`` will need.
        """
        async with self._session() as session:
            result = await session.list_tools()
        return [tool.model_dump() for tool in result.tools]

    async def call_tool(self, name: str, arguments: dict) -> ToolResult:
        """Call one tool by its metamcp-prefixed name
        (``{ServerName}__{originalToolName}``, per
        ``tool-name-parser.ts`` — see ANALYSIS-metamcp.md §1) and wrap
        the result as a ``ToolResult``.
        """
        async with self._session() as session:
            result = await session.call_tool(name, arguments)
        text = "".join(getattr(item, "text", "") for item in result.content)
        if not text and result.structured_content is not None:
            # A tool can return only structured_content with no text
            # content block at all — don't silently report an empty result.
            text = json.dumps(result.structured_content)
        return ToolResult(text=text, is_error=bool(result.is_error))
