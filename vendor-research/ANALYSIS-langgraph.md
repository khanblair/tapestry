# LangGraph Verification — Ground Truth from Source

**Repo cloned:** `https://github.com/langchain-ai/langgraph` (shallow, `main`)
**Local path:** `/Users/kolaborateplatforms/BLAIR/tapestry/vendor-research/langgraph`
**Commit:** `11ee185999b86bfea2d8c0e69cef9a5e37acf686` (2026-08-28)
**Method:** read source directly (not docs site), cross-checked against PyPI package metadata and downloaded wheels for the license claims.

## Summary

The core bet — `interrupt()` + a checkpointer for "pause, ask a human, resume with state intact" — holds up. It's real, it's simple to use, and it's MIT-licensed all the way down for what we need. Two things we assumed from web summaries turn out to need correction before we design around them:

1. **`stream_mode="messages"` does not do what we want for a LiteLLM node.** It's a LangChain **callback handler** (`langgraph/pregel/_messages.py`) that hooks LangChain chat-model streaming events. A plain `async def` node calling `litellm.acompletion()` fires no LangChain callbacks, so token-by-token deltas from it will **not** appear on `"messages"`. It does have a secondary path — it also emits whole `BaseMessage` objects it finds in a node's return value once the node finishes (via `on_chain_end`) — but that's one message at completion, not incremental tokens. For real per-token streaming from a raw LiteLLM call, the answer is `stream_mode="custom"` plus the injected `StreamWriter` (or `get_stream_writer()`), which is provider-agnostic and exactly matches "arbitrary async callable as a node."
2. **`langgraph-api` (the server product) is genuinely a separate, Elastic License 2.0 package, and it isn't in this repo at all.** No `libs/langgraph-api` directory exists in the OSS monorepo — it's developed elsewhere and only referenced as an optional pip extra. This confirms the monorepo's MIT libraries (`langgraph`, `langgraph-checkpoint*`, `langgraph-prebuilt`, `langgraph-sdk`, `langgraph-cli` itself) are cleanly separated from the Elastic-licensed hosted-server pieces, and we simply never need to touch the latter.

Also: for local-first single-user v1, use `AsyncSqliteSaver` (async, file-based, in the ecosystem's own recommended tier below Postgres), not `InMemorySaver`, whose own docstring says "debugging or testing purposes" only, and not `SqliteSaver` (sync-only, not for concurrent use).

---

## 1. StateGraph API: nodes, edges, conditional routing

Core builder lives in `libs/langgraph/langgraph/graph/state.py`; re-exported (with `START`/`END`) from `libs/langgraph/langgraph/graph/__init__.py:1-11`.

- `StateGraph(state_schema, context_schema=None, *, input_schema=None, output_schema=None)` — constructor, `graph/state.py:216-270`. `state_schema` is a `TypedDict`/dataclass/Pydantic model; per-key reducers via `Annotated[T, reducer_fn]`.
- `add_node(node, action=None, *, input_schema=None, retry_policy=None, cache_policy=None, error_handler=None, destinations=None, timeout=None, trace_policy=None, ...)` — `graph/state.py:667-682`. Accepts `(name, callable)` or just `callable` (name inferred).
- `add_edge(start_key, end_key)` — `graph/state.py:928`.
- `add_conditional_edges(source, path, path_map=None)` — `graph/state.py:982-1030`. `path` is a callable/coroutine returning one or more node names (or `Send` objects); `path_map` optionally maps returned values to node names.
- `add_sequence(nodes)` — sugar for a linear chain, `graph/state.py:1032-1076`.
- `set_entry_point(key)` / `set_finish_point(key)` — `graph/state.py:1079`, `:1116` (older sugar for `add_edge(START, key)` / `add_edge(key, END)`).
- `compile(checkpointer=None, *, cache=None, store=None, interrupt_before=None, interrupt_after=None, ...)` → `CompiledStateGraph` — `graph/state.py:1177-1188`. `checkpointer=None` inherits from a parent graph if used as a subgraph; `False` explicitly disables; an actual `BaseCheckpointSaver` instance turns on persistence + `interrupt()` support.

**Minimal skeleton** (verified against `graph/state.py:159-199`'s own example plus `graph/__init__.py` exports):

```python
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END

class State(TypedDict):
    topic: str
    draft: str

def write(state: State) -> dict:
    return {"draft": f"draft about {state['topic']}"}

def route(state: State) -> str:
    return "end" if len(state["draft"]) > 10 else "write"

builder = StateGraph(State)
builder.add_node("write", write)
builder.add_edge(START, "write")
builder.add_conditional_edges("write", route, {"write": "write", "end": END})
graph = builder.compile()

graph.invoke({"topic": "cats", "draft": ""})
```

## 2. `interrupt()`, resume, and checkpointers

**Signature:** `interrupt(value: Any) -> Any`, `libs/langgraph/langgraph/types.py:851`.

- First call inside a node with no matching resume value raises `GraphInterrupt` (an internal control-flow exception), carrying an `Interrupt(value, id)` (`types.py:573-618`) that the graph surfaces to the caller and then **pauses** — the current checkpoint is left with that node pending.
- Return value: on the invocation that actually resumes, `interrupt()` returns whatever the human/client supplied as `Command(resume=...)`. Docstring explicitly warns: **"The graph resumes from the start of the node, re-executing all logic"** (`types.py:864`). This is a real trap for our tool-execution nodes — any side effect (shell command, file write, git push) placed *before* the `interrupt()` call in the same node will re-run on every resume. **Design rule for us: keep the side-effecting action and the `interrupt()` approval gate in separate nodes, gate-node first, action-node second**, so re-execution on resume only re-runs the (idempotent) gate check, not the risky action.
- **Resuming:** call `.invoke()` / `.stream()` / `.ainvoke()` / `.astream()` again with `Command(resume=<value>)` in place of the normal input, using the **same `config`** (same `thread_id`). Verified directly in the test suite, not just docs: `libs/langgraph/tests/test_graph_callbacks.py:125` (`graph.invoke(Command(resume="done"), config)`) and `:154` (async equivalent). A run that hit an interrupt returns/streams a dict containing an `"__interrupt__"` key (`tests/test_graph_callbacks.py:189`) holding the `Interrupt` object(s) — that's what you show the human for approve/reject.
- `Command` (`types.py:798-848`) can also carry `update` (patch state directly) and `goto` (jump to a node), useful for a "reject → route back to persona for revision" flow instead of blind resume.
- **Checkpointer requirement:** `interrupt()`'s docstring is explicit — "To use an `interrupt`, you must enable a checkpointer, as the feature relies on persisting the graph state" (`types.py:870-871`). Without one, pausing mid-graph has nothing to resume from.

**Checkpointer backends that actually exist in this repo** (all `BaseCheckpointSaver` subclasses):

| Class | Package (dir) | Backing store | Notes |
|---|---|---|---|
| `InMemorySaver` | `libs/checkpoint/langgraph/checkpoint/memory/__init__.py:33` | in-process dict (optionally dumped to a pickle file via a `PersistentDict` factory, `:628`) | Docstring: *"Only use `InMemorySaver` for debugging or testing purposes... For production... install `langgraph-checkpoint-postgres`"* (`:40-44`). Ships with `langgraph` itself (a dep of `langgraph-checkpoint`), no extra install. |
| `SqliteSaver` | `libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/__init__.py:45` | SQLite file, sync | Own docstring: *"meant for lightweight, synchronous use cases (demos and small projects) and does not scale to multiple threads"* (`:47-49`). |
| `AsyncSqliteSaver` | `libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/aio.py:38` | SQLite file, async (`aiosqlite`) | **This is the one we want for v1.** No server to stand up, real transactional persistence across process restarts (unlike `InMemorySaver`), and matches our async stack. `AsyncSqliteSaver.from_conn_string("checkpoints.sqlite")` (async context manager, `aio.py:133`). |
| `PostgresSaver` / `AsyncPostgresSaver` | `libs/checkpoint-postgres/langgraph/checkpoint/postgres/__init__.py:40`, `aio.py:40` | Postgres | What the `InMemorySaver` docstring recommends for "production." Not needed for v1 single-user/local-first. |

**Answer to "is there one that doesn't need Postgres": yes — `AsyncSqliteSaver`**, in the separate `langgraph-checkpoint-sqlite` package. It's a first-class, tested backend (not a hack), file-based, and survives process restarts, which `InMemorySaver` does not do reliably (its docstring says testing-only, and its file-dump path is an undocumented convenience, not a supported production feature).

## 3. Arbitrary async callable as a node (e.g., LiteLLM's `acompletion`)

Yes, directly — no LangChain wrapper required. `add_node` accepts any `Callable`; internally it's normalized by `coerce_to_runnable()` (`libs/langgraph/langgraph/_internal/_runnable.py:550-574`):

```python
elif callable(thing):
    if is_async_callable(thing):
        return RunnableCallable(None, thing, name=name, trace=trace)
    else:
        return RunnableCallable(thing, wraps(thing)(partial(run_in_executor, None, thing)), ...)
```

`is_async_callable` (`_internal/_runnable.py:528-536`) does `inspect.iscoroutinefunction(func)`. So a plain `async def node(state): ...` is auto-detected and run via `RunnableCallable.ainvoke` (`_internal/_runnable.py:452-525`), which calls `await self.afunc(*args, **kwargs)` (`:522`) when the graph is invoked with `.ainvoke()`/`.astream()`. A *sync* callable is still supported too — it gets punted to a thread pool via `run_in_executor`.

```python
import litellm

async def persona_node(state: State) -> dict:
    resp = await litellm.acompletion(
        model="anthropic/claude-sonnet-5",
        messages=[{"role": "user", "content": state["topic"]}],
    )
    return {"draft": resp.choices[0].message.content}

builder.add_node("persona", persona_node)
```

No special decoration needed — just use `graph.ainvoke(...)` / `graph.astream(...)` (not the sync `.invoke()`, which would fail or block trying to run a coroutine function synchronously outside an event loop for some call paths).

## 4. Streaming to a chat UI: `.stream()` / `.astream()`

Signature: `stream(input, config=None, *, stream_mode=None, subgraphs=False, ...)` (`libs/langgraph/langgraph/pregel/main.py:2616-2712`); `astream(...)` is the async-generator twin (`pregel/main.py:3063`). `StreamMode` is `Literal["values", "updates", "checkpoints", "tasks", "debug", "messages", "custom"]` (`types.py:122-136`).

**Event shape (v1 default API, what you get from `.stream()`/`.astream()` today):**
- Single `stream_mode` (default `"values"`): yields the mode's data directly, once per step — e.g. `"updates"` yields `{node_name: {...partial state...}}`, `"messages"` yields **2-tuples `(message, metadata)`**.
- List of `stream_mode`s: yields `(mode, data)` tuples.
- `subgraphs=True`: adds a namespace element — `(namespace, data)` or `(namespace, mode, data)`.
- (There's also a `version="v2"` overload returning uniform typed dicts — `ValuesStreamPart` / `UpdatesStreamPart` / `MessagesStreamPart` / `CustomStreamPart` / etc., each with `type`/`ns`/`data` keys, `types.py:264-341` — worth knowing about but not required for v1.)

**`stream_mode="messages"` — read the fine print.** It's implemented as a LangChain `BaseCallbackHandler` (`libs/langgraph/langgraph/pregel/_messages.py:49`, docstring: *"A callback handler that implements stream_mode=messages. Collects messages from: (1) chat model stream events; and (2) node outputs."*). Mechanism (1) hooks `on_llm_new_token` (`_messages.py:151`) — fires only for LangChain `Runnable` chat models (`ChatOpenAI`, etc.) that emit LangChain callbacks. **A raw `litellm.acompletion()` call inside a plain node never triggers this — no LangChain Runnable is involved, so no callback fires, so no tokens are emitted on `"messages"` for that node.** Mechanism (2) hooks `on_chain_end` (`_messages.py:222-236`) and scans whatever the node *returns* for LangChain `BaseMessage` objects, emitting them whole once the node completes — so if you wrap your LiteLLM response as `AIMessage(content=...)` in the node's return value, `"messages"` mode will surface it, but only as one complete message at the end, not as incremental deltas.

**What actually gets us real per-token streaming from a LiteLLM node: `stream_mode="custom"`.** Every node can request a `writer` kwarg (`libs/langgraph/langgraph/graph/_node.py:30-31`, `_NodeWithWriter` protocol) or call `get_stream_writer()` (`libs/langgraph/langgraph/config.py:126`) to get a `StreamWriter = Callable[[Any], None]` (`types.py:138`). Anything passed to it is emitted immediately as a `CustomStreamPart` (`types.py:301-309`) on `stream_mode="custom"`, fully provider-agnostic:

```python
async def persona_node(state: State, *, writer) -> dict:
    chunks = []
    async for part in await litellm.acompletion(model=..., messages=[...], stream=True):
        token = part.choices[0].delta.content or ""
        chunks.append(token)
        writer({"node": "persona", "token": token})   # -> pushed live to the UI
    return {"draft": "".join(chunks)}

for event in graph.stream(inputs, config, stream_mode="custom"):
    ws.send(event)   # event == {"node": "persona", "token": "..."}
```

This is the pattern to design around for Tapestry's live chat UI, not `"messages"`.

## 5. LICENSE — exact, per package, from the files themselves

Root `LICENSE` (`/LICENSE`): MIT, `Copyright (c) 2024 LangChain, Inc.` — full standard MIT text.

Every package directory in this monorepo has its **own** copy of the identical MIT license (checked byte-for-byte start; all begin `MIT License`):

| Path | `pyproject.toml` `name` | License |
|---|---|---|
| `libs/langgraph/LICENSE` | `langgraph` | MIT |
| `libs/checkpoint/LICENSE` | `langgraph-checkpoint` | MIT |
| `libs/checkpoint-sqlite/LICENSE` | `langgraph-checkpoint-sqlite` | MIT |
| `libs/checkpoint-postgres/LICENSE` | `langgraph-checkpoint-postgres` | MIT |
| `libs/prebuilt/LICENSE` | `langgraph-prebuilt` | MIT |
| `libs/sdk-py/LICENSE` | `langgraph-sdk` | MIT |
| `libs/cli/LICENSE` | `langgraph-cli` | MIT |

**Important correction to the brief's premise: there is no `libs/langgraph-api` directory in this repository at all.** `grep -ri "elastic\|business source"` across the entire checked-out monorepo (all `*.md`, `*.toml`, `LICENSE*`) returns zero hits. The Elastic-licensed "hosted server" piece (LangGraph Platform / `langgraph dev` / LangGraph Studio backend) is developed in a **separate, non-public-source repo** and only shows up here as an optional pip dependency:

```
# libs/cli/pyproject.toml
[project.optional-dependencies]
inmem = [
    "langgraph-api>=0.5.35,<1.0.0 ; python_version >= '3.11'",
    "langgraph-runtime-inmem>=0.7 ; python_version >= '3.11'",
]
```

I pulled the actual wheels from PyPI to check, rather than trust the dependency name alone:

- `langgraph-api==0.13.3` wheel ships a real `LICENSE` file at `langgraph_api-0.13.3.dist-info/licenses/LICENSE`, and its `METADATA` says `License: Elastic-2.0`. Quoting the shipped LICENSE file directly:

  > **Elastic License 2.0**
  > ...
  > ## Limitations
  > You may not provide the software to third parties as a hosted or managed service, where the service provides users with access to any substantial set of the features or functionality of the software.
  > You may not move, change, disable, or circumvent the license key functionality in the software...

- `langgraph-runtime-inmem==0.33.3`: `METADATA` also declares `License: Elastic-2.0`, but this wheel does **not** bundle a `LICENSE` file (no `License-File` field, and none present in the wheel contents) — an honest gap to note, not something to paper over.

**Practical takeaway:** `langgraph-api` / `langgraph-runtime-inmem` are pulled in *only* via `pip install "langgraph-cli[inmem]"` (the local dev server / Studio backend for `langgraph dev`). **We should not install `langgraph-cli` at all** — we're embedding the library in our own service, not running their dev server or hosted platform, so we never touch Elastic-licensed code. Everything we actually need (`langgraph`, `langgraph-checkpoint`, `langgraph-checkpoint-sqlite`) is MIT, confirmed both from the repo's own `LICENSE` files and independently from each package's PyPI `license_expression` metadata (`MIT` for all of `langgraph`, `langgraph-checkpoint`, `langgraph-checkpoint-sqlite`, `langgraph-checkpoint-postgres`, `langgraph-prebuilt`, `langgraph-sdk`, `langgraph-cli`; `Elastic-2.0` confirmed for `langgraph-api` and `langgraph-runtime-inmem` only).

## 6. Packages to install, current latest versions (checked against PyPI + confirmed matching this checkout's `pyproject.toml` versions)

```
pip install langgraph langgraph-checkpoint-sqlite
```

- `langgraph` — **1.2.11** (also pulls `langgraph-checkpoint`, `langgraph-sdk`, `langgraph-prebuilt`, `langchain-core` as deps; `libs/langgraph/pyproject.toml:7`)
- `langgraph-checkpoint` — **4.2.0** (base checkpoint interfaces + `InMemorySaver`; pulled in transitively, no need to list explicitly)
- `langgraph-checkpoint-sqlite` — **3.1.1** (gives `AsyncSqliteSaver`; pulls `aiosqlite`, `sqlite-vec`)
- `langgraph-checkpoint-postgres` — **3.1.2** (not needed for v1; note for later if we outgrow SQLite/single-process)
- Do **not** install `langgraph-cli` or its `[inmem]` extra — that's the only path that pulls Elastic-2.0 code (`langgraph-api`, `langgraph-runtime-inmem`) into our tree.

All version numbers above are live PyPI data (`license_expression` field / wheel metadata), independently confirming the versions pinned in this checkout's `pyproject.toml` files.

---

## Recommendation

Use LangGraph as planned — `interrupt()` + a checkpointer is real and maps onto our approval flow exactly as assumed, and the license surface for what we need (`langgraph` + `langgraph-checkpoint-sqlite`) is clean MIT. Two changes to how we build:

1. **Streaming:** design persona nodes to push tokens via `writer`/`get_stream_writer()` and consume `stream_mode="custom"`, not `stream_mode="messages"` (which silently does nothing for raw LiteLLM calls).
2. **Node boundaries around `interrupt()`:** never put a side-effecting action (shell/file/git) in the same node as the `interrupt()` call — the whole node re-executes on resume. Split "propose action" → "interrupt (approve/reject)" → "execute action" into three nodes (or at minimum, gate-node then action-node), so only the harmless gate re-runs.

### Code sketch — persona node + human-approval gate + resume, sized for v1 (SQLite, single-user)

```python
import asyncio
from typing import TypedDict
import litellm

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.config import get_stream_writer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


class State(TypedDict):
    task: str
    proposed_action: str       # e.g. a shell command the persona wants to run
    approved: bool
    result: str


# --- Node 1: persona calls the model, streams tokens live, proposes an action ---
async def persona_node(state: State) -> dict:
    writer = get_stream_writer()
    chunks: list[str] = []
    stream = await litellm.acompletion(
        model="anthropic/claude-sonnet-5",
        messages=[{"role": "user", "content": state["task"]}],
        stream=True,
    )
    async for part in stream:
        token = part.choices[0].delta.content or ""
        if token:
            chunks.append(token)
            writer({"type": "token", "node": "persona", "text": token})
    return {"proposed_action": "".join(chunks)}


# --- Node 2: pure approval gate. No side effects here -- safe to re-execute. ---
def approval_node(state: State) -> dict:
    decision = interrupt({
        "question": "Approve this action?",
        "action": state["proposed_action"],
    })
    # decision is whatever the human sent back via Command(resume=...)
    return {"approved": decision is True}


# --- Node 3: the actual risky action, only reached after approval ---
def execute_node(state: State) -> dict:
    if not state["approved"]:
        return {"result": "rejected by human"}
    # real shell/file/git tool call would go here
    return {"result": f"executed: {state['proposed_action']}"}


def route_after_approval(state: State) -> str:
    return "execute" if state["approved"] else END


builder = StateGraph(State)
builder.add_node("persona", persona_node)
builder.add_node("approval", approval_node)
builder.add_node("execute", execute_node)
builder.add_edge(START, "persona")
builder.add_edge("persona", "approval")
builder.add_conditional_edges("approval", route_after_approval, {"execute": "execute", END: END})
builder.add_edge("execute", END)


async def main():
    async with AsyncSqliteSaver.from_conn_string("tapestry_checkpoints.sqlite") as checkpointer:
        graph = builder.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "conversation-42"}}

        # Run until it pauses at the approval gate
        async for event in graph.astream(
            {"task": "list files in /tmp", "proposed_action": "", "approved": False, "result": ""},
            config,
            stream_mode=["custom", "updates"],
        ):
            mode, payload = event
            if mode == "custom":
                pass  # forward payload to Discord/Telegram/web-socket as a live token
            elif mode == "updates" and "__interrupt__" in payload:
                interrupt_info = payload["__interrupt__"][0].value
                # -> render an "Approve / Reject" prompt to the human, with interrupt_info["action"]

        # ... later, human clicks Approve in Discord/Telegram/web UI ...
        async for event in graph.astream(Command(resume=True), config, stream_mode=["custom", "updates"]):
            mode, payload = event
            if mode == "updates":
                print(payload)  # {'execute': {'result': 'executed: ...'}}

asyncio.run(main())
```

Notes on the sketch:
- `thread_id` is the unit of "conversation" — reuse it to keep resuming the same paused approval; a new one starts a fresh conversation with its own state history.
- The SQLite file (`tapestry_checkpoints.sqlite`) is the entire durability story for v1 — no server process, no Postgres, survives app restarts (unlike `InMemorySaver`).
- If we later need multiple personas talking to each other and to the human in the same graph, this pattern composes: add more persona nodes and route between them with `add_conditional_edges`; the approval gate pattern above is reusable per risky-action type.
- If we outgrow single-process SQLite (multiple worker processes needing the same checkpoint store), swap in `AsyncPostgresSaver` from `langgraph-checkpoint-postgres` — the `BaseCheckpointSaver` interface means the graph code above doesn't change, only the `compile(checkpointer=...)` call site.
