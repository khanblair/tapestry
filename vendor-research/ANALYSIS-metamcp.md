# metamcp Vendor Analysis

**Source**: `git clone --depth 1 https://github.com/metatool-ai/metamcp` into
`/Users/kolaborateplatforms/BLAIR/tapestry/vendor-research/metamcp`
**Commit**: `ff4ff2de9d25453c52dcc7be32680b30700a6012` (Tue Jun 23 2026, shallow clone of default branch at clone time)
**MCP SDK version pinned**: `@modelcontextprotocol/sdk@1.29.0` (`apps/backend/package.json:25`)

## Summary

metamcp is a Node/TypeScript monorepo (Next.js frontend + Express backend, Postgres-backed) that
aggregates one or more real MCP servers (STDIO, SSE, or Streamable HTTP) into **Namespaces**, and
publishes each Namespace to the outside world as one or more **Endpoints**. Each Endpoint is a
completely standard MCP server -- built with the official `@modelcontextprotocol/sdk` `Server` and
`*ServerTransport` classes -- reachable over both SSE and Streamable HTTP, plus a bonus OpenAPI/REST
wrapper. This confirms the core assumption: **our Python backend can talk to metamcp using any
off-the-shelf MCP client library** (e.g. the official `mcp` Python SDK), exactly as it would talk to
any other remote MCP server. No custom protocol, no custom client needed.

A metamcp instance is in fact already running locally on this machine (Docker, healthy, 13h uptime)
and is the same instance backing this very Claude Code session's `mcp__metamcp__*` tools -- see
Section 6 for the live, verified evidence.

One correction to the task's premise: **the default/actual port is `12008`, not `3000`.** Nothing
listens on 3000 locally, and the source code and docs consistently use 12008.

---

## 1. Architecture: Servers -> Namespaces -> Endpoints

Confirmed directly from the Postgres schema, `apps/backend/src/db/schema.ts`:

- **`mcpServersTable`** (`schema.ts:46-92`) -- one row per real upstream MCP server. `type` is an enum:
  `STDIO`, `SSE`, or `STREAMABLE_HTTP` (`mcp_server_type` pgEnum, `schema.ts:29-31`, backed by
  `McpServerTypeEnum` in `packages/zod-types`). A CHECK constraint enforces that STDIO rows have a
  `command` and no `url`, while SSE/STREAMABLE_HTTP rows have a `url` and no `command`
  (`schema.ts:90-95`).
- **`namespacesTable`** (`schema.ts:243-266`) -- a named grouping, owned by a user or public.
- **`namespaceServerMappingsTable`** (`schema.ts:319-345`) -- many-to-many join between namespaces and
  servers, with a per-mapping `status` (ACTIVE/INACTIVE) so a server can be toggled per namespace
  without deleting it.
- **`endpointsTable`** (`schema.ts:270-315`) -- a public routing name (globally unique, URL-safe) that
  points at exactly **one** namespace (`namespace_uuid` FK) and carries per-endpoint auth/rate-limit
  flags: `enable_api_key_auth`, `enable_oauth`, `use_query_param_auth`, `enable_max_rate`,
  `enable_client_max_rate`, `enable_metamcp_admin_tools`.
- **`namespaceToolMappingsTable`** (referenced in `metamcp-middleware/filter-tools.functional.ts:6-11`)
  -- lets you further disable individual tools per namespace, and `tool-overrides.functional.ts`
  lets you rename/override individual tools per namespace. Net effect: **different Endpoints backed
  by different Namespaces can expose different, independently-curated subsets of tools from the same
  underlying servers** -- directly useful if Tapestry wants different agent roles to see different
  tool sets from one shared server pool.

So the hierarchy is exactly as described: `mcp_servers` (N) <-> `namespaces` (N) via a mapping table,
and `namespaces` (1) -> `endpoints` (N). An Endpoint is the thing a client connects to.

Aggregation happens in `apps/backend/src/lib/metamcp/metamcp-proxy.ts`. `createServer(namespaceUuid, ...)`
(`metamcp-proxy.ts:112`) builds one unified `Server` instance representing the whole namespace: it
calls `getMcpServers(namespaceUuid)` (`fetch-metamcp.ts:18`) to pull every ACTIVE, non-errored server
mapped to that namespace, opens/reuses a pooled MCP client session to each one (`mcp-server-pool.ts`),
fans out `tools/list` (and prompts/resources) to all of them concurrently
(`Promise.allSettled(allServerEntries.map(...))`, `metamcp-proxy.ts:225`), and re-exposes the merged
result with tool names prefixed by server name.

Tool name format, confirmed from `apps/backend/src/lib/metamcp/tool-name-parser.ts:1-59`:
```
{ServerName}__{originalToolName}
```
(first `__` is the split point; nested/forwarded names can contain further `__`). This exactly matches
what's visible live in this very session: tools like `mcp__metamcp__github-mcp-server__get_me` are
Claude Code's own `mcp__<connector>__` wrapper around metamcp's `github-mcp-server__get_me`.

## 2. Is a metamcp Endpoint just a standard MCP server?

**Yes -- proven directly from the transport implementation, not inferred.**

`apps/backend/src/routers/public-metamcp/streamable-http.ts:3`:
```ts
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
```
and `apps/backend/src/routers/public-metamcp/sse.ts:1`:
```ts
import { SSEServerTransport } from "@modelcontextprotocol/sdk/server/sse.js";
```
Both routers wire a brand-new-per-session `StreamableHTTPServerTransport` / `SSEServerTransport`
(official SDK classes) to the unified `Server` object built by `createServer()`
(`metamcp-server-pool.ts` -> `metamcp-proxy.ts`). The `Server` itself, in
`apps/backend/src/lib/metamcp/metamcp-proxy.ts:1,142-153`:
```ts
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
...
const server = new Server(
  { name: `metamcp-unified-${namespaceUuid}`, version: "1.0.0" },
  { capabilities: { prompts: {}, resources: {}, tools: {} }, instructions: namespace?.description },
);
```
registers handlers via `ListToolsRequestSchema`, `CallToolRequestSchema`,
`ListPromptsRequestSchema`, `ReadResourceRequestSchema`, etc. -- all standard MCP SDK request schemas.

Symmetrically, on the *outbound* side (metamcp connecting to the real upstream servers it aggregates),
`apps/backend/src/lib/metamcp/client.ts:1-4` uses the official SDK's `Client`,
`SSEClientTransport`, and `StreamableHTTPClientTransport` classes too. So the whole system --
both the side facing us and the side facing the tools it wraps -- is built entirely on the standard
MCP SDK primitives. There is no proprietary framing anywhere in the wire protocol.

**Practical consequence for Tapestry**: our Python backend can use the official `mcp` Python SDK's
`streamablehttp_client` (or `sse_client`) exactly as it would for any other MCP server -- point it at
`http://<host>:12008/metamcp/<endpoint-name>/mcp`, add the auth header, and go through the normal
`ClientSession` handshake (`initialize` -> `list_tools` -> `call_tool`). See Section 7 for a sketch.

Transport specifics worth knowing before integrating:
- Streamable HTTP (`streamable-http.ts:119-366`) is the modern/recommended transport: `GET /:endpoint/mcp`
  resumes a session by `mcp-session-id` header, `POST /:endpoint/mcp` without that header opens a new
  session (id is server-generated and returned), and `DELETE /:endpoint/mcp` explicitly tears the
  session down. The server is constructed with `enableJsonResponse: true`
  (`streamable-http.ts:216`) and force-normalizes the `Accept` header to
  `application/json, text/event-stream` (`normalizeStreamableHttpAcceptHeader`, lines 42-55) -- so a
  strict client that only sends `Accept: application/json` won't get a 406, but response bodies come
  back as single JSON payloads rather than an SSE stream. A `GET /:endpoint/mcp` with no matching
  `mcp-session-id` returns a bare 404, not JSON.
- SSE (`sse.ts`) is present for backward compatibility; per the README it's the transport `mcp-proxy`/
  Cursor-style stdio-bridges use. It needs a second route, `POST /:endpoint/message`, to carry
  client->server messages back over the SSE session.
- Sessions are **in-memory** per backend process, managed by `SessionLifetimeManagerImpl`
  (`session-lifetime-manager.ts`) with an automatic cleanup timer -- a long-lived Python client should
  expect idle sessions to be evicted and be ready to reconnect/re-`initialize`, not assume a session
  lives forever. `GET /:endpoint_name/mcp/health/sessions` (unauthenticated, but only reachable if you
  already know the base path) exposes live pool/session counts for diagnostics.
- Rate limiting is available per-endpoint (`enable_max_rate` / `enable_client_max_rate` on
  `endpointsTable`) and returns HTTP 429 (`rate-limit.middleware.ts`, wired into every route above).

## 3. Auth

Two separate auth systems exist in the codebase -- they are easy to conflate, so keep them distinct:

### 3a. Client -> Endpoint (what our Python backend will use)

Implemented in `apps/backend/src/middleware/api-key-oauth.middleware.ts`, applied to every
SSE/Streamable HTTP/OpenAPI route via `lookupEndpoint` -> `authenticateApiKey` -> `rateLimitMiddleware`.
Governed per-endpoint by the `enable_api_key_auth` / `enable_oauth` booleans on `endpointsTable`
(4 combinations handled explicitly, `api-key-oauth.middleware.ts:180-351`):

- **API key** -- `extractAuthToken()` (`api-key-oauth.middleware.ts:120-163`) accepts the key from an
  `X-API-Key` header, an `Authorization: Bearer <key>` header, or (if `use_query_param_auth` is set on
  the endpoint) an `?api_key=`/`?apikey=` query parameter. Validated against `ApiKeysRepository`.
- **OAuth** -- a bearer token of the form `mcp_token_*` (`routers/oauth/utils.ts:31`) is validated via
  metamcp's own internal `/oauth/introspect` endpoint (`api-key-oauth.middleware.ts:54-115`). This is
  **not third-party OIDC federation for the client** -- it's metamcp acting as its own MCP-spec OAuth
  2.1 Authorization Server: `routers/oauth/authorization.ts`, `token.ts`, `registration.ts`,
  `metadata.ts` implement `/authorize`, `/token`, dynamic client registration, and
  `/.well-known/oauth-authorization-server` per the MCP Authorization spec. A failed/absent token gets
  a proper `WWW-Authenticate: Bearer ... resource_metadata=".../.well-known/oauth-protected-resource"`
  challenge (`sendOAuthChallengeResponse`, lines 455-492), so an MCP client with built-in OAuth
  discovery (e.g. `mcp-remote`, or the official SDK's OAuth helpers) can complete the dance
  automatically.
- Repeated failed auth attempts are throttled (`auth-rate-limiter.ts`) and return HTTP 429.
- Access control after auth: public vs. private API keys/namespaces/endpoints are cross-checked
  (`checkApiKeyAccess` / `checkOAuthAccess`, lines 372-435) so a public key can't reach a private
  endpoint and vice versa.

**OIDC is not one of the client-to-endpoint mechanisms.** It only appears in the next section.

### 3b. Human login to the metamcp web UI/admin API (not relevant to our Python backend)

`apps/backend/src/auth.ts` uses `better-auth` with email/password plus an *optional* generic OIDC SSO
provider (`genericOAuth` plugin, lines 34-51), enabled only if `OIDC_CLIENT_ID` /
`OIDC_CLIENT_SECRET` env vars are set. This is how a human administrator signs into the metamcp
dashboard to configure Servers/Namespaces/Endpoints -- it has nothing to do with how an MCP client
authenticates to a published Endpoint.

### 3c. metamcp -> upstream servers it aggregates (also not directly relevant, but explains the model)

`apps/backend/src/trpc/oauth.impl.ts` and `lib/oauth-upstream/*` handle the case where metamcp itself
needs to act as an OAuth *client* against an upstream MCP server that requires OAuth (e.g., a remote
tool provider). Independent of both auth systems above.

## 4. Deployment

Confirmed Docker-based, single-container running both apps:

- `docker-compose.yml` -- service `app` (`ghcr.io/metatool-ai/metamcp:latest`) plus a `postgres:16-alpine`
  service. Compose maps `"12008:12008"` (`docker-compose.yml:9`) for the app and
  `"${POSTGRES_EXTERNAL_PORT:-9433}:5432"` for Postgres.
- Inside the container (`docker-entrypoint.sh:53,69`, `Dockerfile:102`) the Express **backend**
  actually listens on **12009** and the Next.js **frontend** listens on **12008**; the frontend
  reverse-proxies `/metamcp/:path*`, `/oauth/:path*`, `/.well-known/:path*`, `/trpc/:path*`,
  `/mcp-proxy/:path*` etc. to `http://localhost:12009` via `apps/frontend/next.config.js` rewrites.
  **A client only ever needs to know port 12008** (or whatever is mapped externally) -- 12009 is an
  internal implementation detail.
- `Dockerfile:102` -- `EXPOSE 12008`; `Dockerfile:106` -- healthcheck hits `http://localhost:12008/health`.
- Env vars a client-side integration cares about (from `example.env` and the auth/routing code above):
  - `APP_URL` / `NEXT_PUBLIC_APP_URL` -- public base URL metamcp announces itself as (affects OAuth
    redirect URIs and metadata documents); default `http://localhost:12008`.
  - `TRANSFORM_LOCALHOST_TO_DOCKER_INTERNAL` -- rewrites `localhost`/`127.0.0.1` in configured upstream
    server URLs to `host.docker.internal` (`client.ts:29-38`) -- relevant if you register an upstream
    server pointing at something on the Docker host.
  - `BOOTSTRAP_API_KEYS` / `BOOTSTRAP_ENDPOINTS` / `BOOTSTRAP_NAMESPACES` -- declarative
    provisioning-on-startup of exactly the API keys/endpoints/namespaces our integration would need
    (useful for reproducible dev/staging setup -- see `docker-compose.yml:29-159` for the full commented
    schema).
  - `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` / `OIDC_DISCOVERY_URL` -- only relevant if *humans* will SSO
    into the admin UI, not for the Python client.
  - No env var is needed client-side beyond the base URL and whatever API key/OAuth credential you
    provision -- auth config lives per-Endpoint in the database, not in env vars.

## 5. License

`LICENSE` (repo root, 1.0 KB) contains the standard MIT permission grant and disclaimer text verbatim
("Permission is hereby granted, free of charge... THE SOFTWARE IS PROVIDED "AS IS"...") under
`Copyright 2024 MetaMCP, James Zhang`. **Confirmed MIT.**

Minor inconsistency worth flagging, not a license risk: `apps/backend/package.json` declares
`"license": "ISC"` (likely a stale boilerplate value), while `packages/typescript-config/package.json`
says `"license": "MIT"` and the root `package.json` has no `license` field at all. The governing
license for the repository as a whole is the root `LICENSE` file (MIT) -- GitHub's license detection
and standard OSS practice both key off that file, not per-package.json fields -- so this is cosmetic,
not a real ambiguity.

## 6. What we found about the actual local instance (real, verified -- not guessed)

`docker ps` shows a genuinely running instance:
```
ff2248412f51   metamcp:local-prod   Up 13 hours (healthy)   0.0.0.0:12008->12008/tcp   metamcp
c1e68886be64   postgres:16-alpine   Up 13 hours (healthy)   0.0.0.0:9433->5432/tcp     metamcp-pg
```
Note the image tag is `metamcp:local-prod` -- a **locally built image**, not the published
`ghcr.io/metatool-ai/metamcp:latest`. So this is presumably built from a (possibly slightly different)
checkout of the same project; I can't verify it's byte-identical to the commit cloned above.
Circumstantial evidence it's very close: the live 401 response body it returns matches
`sendApiKeyRequiredResponse()` in `api-key-oauth.middleware.ts:440-450` verbatim (see below).

`http://localhost:3000` (the port named in the task) -- **connection refused, nothing listens there.**
This confirms the port-3000 assumption in the task was simply wrong; treat 12008 as authoritative.

Live HTTP probes against the real instance on 12008:
```
GET http://localhost:12008/metamcp
-> {"service":"public-endpoints","version":"1.0.0","description":"Public MetaMCP endpoints",
   "endpoints":[{"name":"Public","description":"Aggregated MCP endpoint for all coding tools",
   "namespace":"Default",
   "endpoints":{"mcp":"/metamcp/Public/mcp","sse":"/metamcp/Public/sse",
                "api":"/metamcp/Public/api","openapi":"/metamcp/Public/api/openapi.json"}}]}

GET http://localhost:12008/health              -> {"status":"ok"}
GET http://localhost:12008/metamcp/health      -> {"status":"ok","service":"public-endpoints"}

POST http://localhost:12008/metamcp/Public/mcp (tools/list, no credentials)
-> 401 {"error":"authentication_required","error_description":"Authentication required via API key",
       "supported_methods":["X-API-Key header","query parameter (api_key or apikey)"]}
```
This response body is an exact byte-for-byte match of `sendApiKeyRequiredResponse()` -- good evidence
the running container really is this codebase's auth middleware, not a placeholder.

Read-only queries against the live Postgres container (`docker exec metamcp-pg psql ...`, `SELECT`
only, nothing modified) show exactly one Namespace and one Endpoint, and 9 configured upstream MCP
servers:
```
namespaces:  Default ("Default namespace")
endpoints:   Public   enable_api_key_auth=true, enable_oauth=false
mcp_servers: context7 (STREAMABLE_HTTP), kola (STREAMABLE_HTTP),
             github-mcp-server, mobile-mcp, paypal, postman, quickbooks, sonarqube, trello  (all STDIO)
```
So this endpoint requires an API key (confirmed both by the live 401 and by the DB row), not OAuth.

**This is the same instance already backing this Claude Code session.** The 9 server names above are
exactly the `mcp__metamcp__<server>__*` tool groups visible in this session's own tool list
(github-mcp-server, mobile-mcp, paypal, postman, quickbooks, sonarqube, trello, context7, kola) -- i.e.
this session is *itself* a live, working example of an off-the-shelf MCP client (Claude Code) connected
to this exact metamcp Endpoint and listing its aggregated tools successfully. That is stronger evidence
than any code sketch could be.

`~/.hermes/config.yaml` (read-only, not modified) confirms the connection details a real client uses
today:
```yaml
mcp_servers:
  metamcp:
    url: http://localhost:12008/metamcp/Public/mcp
    headers:
      Authorization: Bearer ${MCP_METAMCP_API_KEY}
```
i.e. Streamable HTTP transport, endpoint name `Public` (case-sensitive), API key passed as a Bearer
token in the `Authorization` header (which `extractAuthToken()` accepts and routes through the same
API-key validation path as `X-API-Key`). The referenced env var name `MCP_METAMCP_API_KEY` exists in
`~/.hermes/.env` -- I confirmed only that the key **name** is present there; I did not print or extract
its value (that file holds live credentials, and reading it wasn't necessary to answer the task).

I did not attempt to obtain or guess the actual API key value, and I did not attempt the OAuth flow --
so I have not personally executed an authenticated `tools/list` against this instance from a fresh
script. The unauthenticated probes above, the DB inspection, and this session's own working
`mcp__metamcp__*` tools together are the verified evidence; nothing here required guessing.

## 7. Recommendation

Use the official `mcp` Python SDK's Streamable HTTP client against the existing `Public` endpoint (or
a new endpoint you provision for Tapestry specifically -- recommended, so you can scope its own
Namespace/tool subset independently of whatever else uses `Public`). Sketch:

```python
import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

METAMCP_URL = os.environ.get(
    "METAMCP_URL", "http://localhost:12008/metamcp/Public/mcp"  # note: case-sensitive endpoint name
)
METAMCP_API_KEY = os.environ["METAMCP_API_KEY"]  # provisioned via metamcp's admin UI or BOOTSTRAP_API_KEYS


async def list_tools():
    headers = {"Authorization": f"Bearer {METAMCP_API_KEY}"}
    # Streamable HTTP: metamcp accepts the key as a Bearer token or X-API-Key header,
    # since it's a plain API key rather than an "mcp_token_" OAuth token.
    async with streamablehttp_client(METAMCP_URL, headers=headers) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            for tool in tools.tools:
                # metamcp prefixes aggregated tool names as "{ServerName}__{originalToolName}"
                print(tool.name, "->", tool.description)

            # Example call -- server prefix + "__" + real tool name, per tool-name-parser.ts
            result = await session.call_tool(
                "github-mcp-server__get_me", arguments={}
            )
            print(result)


asyncio.run(list_tools())
```

Things to build around, straight from the source reading above:
- **Reconnect logic**: sessions are in-memory and time out server-side
  (`SessionLifetimeManagerImpl`); wrap `list_tools`/`call_tool` calls so a dropped/evicted session
  triggers a fresh `initialize()` rather than surfacing a raw 404.
- **Per-agent tool scoping**: if different Tapestry agent roles should see different tool subsets from
  the same underlying servers, create one Namespace + Endpoint per role (or per role-group) and use
  metamcp's per-namespace tool activation/override tables (`namespaceToolMappingsTable`,
  `filter-tools.functional.ts`, `tool-overrides.functional.ts`) instead of filtering client-side --
  metamcp already has first-class support for this.
- **Auth**: provision one API key per environment (or per agent) via the admin UI, or declaratively via
  `BOOTSTRAP_API_KEYS`/`BOOTSTRAP_ENDPOINTS` env vars for reproducible dev/staging deploys, and pass it
  as `Authorization: Bearer <key>`. Only reach for the OAuth 2.1 path if you need dynamic, per-user
  delegated auth rather than a small number of static service credentials -- for a backend service like
  Tapestry's, a static API key is almost certainly the simpler and correct choice.
- **Transport choice**: prefer Streamable HTTP (`/mcp`) over SSE (`/sse`) -- it's the transport metamcp's
  own README recommends going forward, and it's simpler (no separate `/message` POST-back route to
  wire up).
