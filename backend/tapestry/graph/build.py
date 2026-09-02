"""The actual `StateGraph` — persona node -> approval node -> execute node.

Read this module top to bottom once before touching it; the node split
below is not incidental structure, it's the direct consequence of one
verified fact (`docs/vendor-research/ANALYSIS-langgraph.md` §2):
`interrupt()` re-executes its ENTIRE node from the top on every resume.
Anything with an external side effect placed before (or spanning) an
`interrupt()` call inside the same node will fire again on every resume,
not just once. That constraint shapes almost every judgment call in this
file — see the "Judgment calls" section below before assuming any piece of
this is arbitrary.

Three node roles, three separate functions, on purpose:

- `persona_node` — calls the model (`models.litellm_client.call_model`),
  decides what happens next. Never calls `interrupt()`, so LangGraph never
  re-executes it on resume; it's the one place in this file that's always
  safe to have side effects (event-log writes, skill-catalog sync,
  delegation) at the top of the function body, not just after some gate.
- `approval_node` — calls `interrupt()` when a persona's proposed tool call
  needs human sign-off. Nothing before `interrupt()` in this node writes to
  the event log or touches a tool — see "Judgment call: ask_user() vs.
  interrupt()" below for exactly why, and where the `ask/requested` /
  `ask/answered` bookkeeping actually happens instead.
- `execute_node` — actually runs the tool call, via `TOOL_REGISTRY`. Only
  ever reached after `approval_node` resumes with an approval (or directly
  from `persona_node` for a tool that doesn't need approval at all). Since
  it never calls `interrupt()` either, a tool call made here happens
  exactly once per proposal — the entire point of keeping it out of
  `approval_node`.

Judgment call: `ask_user()` vs. `interrupt()`
----------------------------------------------
The task brief says the approval node "calls `interrupt()` via `core.ask.
ask_user`/`core.approvals.request_approval`". Read literally as "call
`ask_user()`, which itself calls `interrupt()`" that's impossible — neither
function calls `interrupt()`; `ask_user()` is a 900-second `asyncio.sleep`
poll loop against the event log (see `core/ask.py`). Blocking a graph node
inside that poll loop for up to 15 minutes would keep the coroutine (and
the process) alive the whole time, which defeats the entire reason to use
LangGraph's checkpointed pause/resume in the first place — the process
could be killed and restarted while an approval is pending, and a resumed
run should pick up exactly where it left off, not re-enter a fresh
15-minute poll.

So the read this module implements: **`interrupt()` IS the pause
mechanism; `core.ask`'s types and event shapes are reused for the
QUESTION/ANSWER contract, not its polling implementation.** Concretely:
`approval_node` builds an `AskQuestion`-shaped value and passes it straight
to `interrupt()` (no `ask_user()` call); whatever answers it (an adapter
reacting to the `__interrupt__` value in a stream event, e.g.) resumes the
graph with `Command(resume=<answer>)`. The `ask/requested` and `ask/
answered` event-log entries still get written — for the exact same reason
every other surface already understands them — but from `persona_node`
(before the pause, exactly once, since that node never re-executes) and
from the code AFTER `interrupt()` returns inside `approval_node` (which
also only runs once per real resume). Nothing gets written before or
spanning the `interrupt()` call itself.

Judgment call: skills integration mechanism
--------------------------------------------
Two-tier discovery per the scoped spec: a cheap catalog always in context,
full body loaded on demand. Tier one is wired as system-prompt text —
`persona_node` calls `skills.catalog_sync.sync_catalog` (keeping the event
log coherent per its own content-hash-then-replace design) and appends a
`"- name: description"` line per skill to the system prompt sent to `call_
model`. Tier two — loading a skill's full body — is wired as an actual
tool: `TOOL_REGISTRY["skill_loader"]`, always available to every persona
regardless of its `tools:` permission list (it's a read-only knowledge
lookup, not a permissioned capability the way file/shell/git/deploy access
is), calling `SkillRegistry.load_body(name)` and returning the body as a
`ToolResult`. `bypass_invocation_check` is never passed — a model-initiated
`skill_loader` call correctly gets refused (via `SkillNotFoundError`) for
any skill with `disable_model_invocation=True`; that flag exists only for
the human `/skill-name` gesture, which lives above this graph, not inside
it.

Judgment call: TOOL_REGISTRY keys are NOT renamed
---------------------------------------------------
The task frames `personas/*.yaml`'s existing tool-name strings
(`file_editor_read`, `file_editor`, `terminal`, `terminal_read_only`,
`git`, `test_runner`, `deploy_pipeline`) as placeholders to reconcile.
Reconciled: these seven strings ARE the real, canonical `TOOL_REGISTRY`
keys below — they were already well-scoped, descriptive names; the actual
gap was that nothing backed them with real callables. `personas/*.yaml`
needed no key renames, only a doc comment pointing at this file as the
authoritative source (see those files). An eighth key, `"skill_loader"`,
is added here as core infrastructure available to every persona (see
above) rather than a ninth persona-YAML permission entry.

Judgment call: real per-token streaming is NOT wired here
-------------------------------------------------------------
`streaming.py` is built exactly per the verified research
(`stream_mode="custom"` + `StreamWriter`). But `models.litellm_client.
call_model` — "the only place LiteLLM gets called from," a tested policy
layer with empty-completion retry and context-window normalization — makes
a single non-streaming `litellm.acompletion()` call; it has no `stream=
True` code path to pull incremental tokens from. Bypassing it here to get
raw token streaming would silently drop that policy layer for every
persona call. So `persona_node`/`execute_node` emit coarse status frames
(`"persona/thinking"`, `"persona/responded"`, `"tool/status"`) via
`streaming.emit`, proving the wiring end-to-end, but NOT per-token deltas.
Real token streaming needs a `call_model(..., stream=True)` variant added
to `models/litellm_client.py` first — flagged here explicitly as a real
gap for whoever builds the adapters, not silently worked around.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Awaitable, Callable, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from tapestry.core import events
from tapestry.core.ask import AskQuestion
from tapestry.core.conversations import derive_messages
from tapestry.core.delegation import DelegationRoundLimitExceeded, delegate
from tapestry.core.personas import Persona, load_personas
from tapestry.graph import budgets, streaming, verify
from tapestry.graph.budgets import DelegationDepthExceeded
from tapestry.graph.checkpointer import get_checkpointer
from tapestry.models.litellm_client import call_model
from tapestry.skills.catalog_sync import sync_catalog
from tapestry.skills.registry import SkillNotFoundError, SkillRegistry, SkillSummary
from tapestry.tools.file_editor import FileEditorTool, ToolResult
from tapestry.tools.mcp_client import MetaMCPClient
from tapestry.tools.terminal import TerminalTool

__all__ = [
    "TapestryGraphState",
    "new_state",
    "TOOL_REGISTRY",
    "TOOL_SCHEMAS",
    "TOOLS_REQUIRING_APPROVAL",
    "DIFF_PRODUCING_TOOLS",
    "DELEGATE_TOOL_NAME",
    "TASK_COMPLETE_TOOL_NAME",
    "SKILL_LOADER_TOOL_NAME",
    "PERSONAS",
    "persona_node",
    "approval_node",
    "execute_node",
    "build_graph",
]

# ---------------------------------------------------------------------------
# Repo-root-anchored paths.
#
# `core.personas.load_personas(directory="personas")` and `skills.registry.
# SkillRegistry()` both default to CWD-relative resolution. That's fine for
# a process whose CWD happens to be the repo root, but `uv run pytest` (and
# most CI/editor setups) runs from `backend/`, where a bare "personas"
# resolves to a nonexistent `backend/personas/` -- silently returning an
# empty dict, not an error. Anchoring explicitly here means this module
# behaves identically regardless of the caller's CWD.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PERSONAS_DIR = _REPO_ROOT / "personas"
_SKILLS_DIR = _REPO_ROOT / "skills"

PERSONAS: dict[str, Persona] = load_personas(str(_PERSONAS_DIR))
_SKILL_REGISTRY = SkillRegistry(start_dir=_REPO_ROOT, bundled_skills_dir=_SKILLS_DIR)


def _get_persona(persona_id: str) -> Persona:
    try:
        return PERSONAS[persona_id]
    except KeyError as exc:
        raise KeyError(
            f"No persona named {persona_id!r} found under {_PERSONAS_DIR}"
        ) from exc


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------


class TapestryGraphState(TypedDict, total=False):
    conversation_id: str
    persona_id: str
    # No `Annotated[..., reducer]` on this key -> plain TypedDict merge
    # semantics apply: whatever a node RETURNS for `messages` fully
    # REPLACES the prior value, it is never appended to automatically.
    # Every node that touches conversation history must therefore read
    # `state.get("messages", [])` and return the complete new list itself
    # (read-then-append) -- returning a partial/fragment here silently
    # truncates history for the next node. `persona_node`/`approval_node`/
    # `execute_node` all follow this already; keep it that way.
    messages: list[dict]
    pending_tool_call: dict | None
    delegation_depth: int
    turn_count: int
    task_id: str | None
    # ADDITIVE beyond the literal minimum field list, both introduced for
    # documented reasons rather than left implicit:
    thread_id: str | None  # thread support (see core.conversations.Message)
    turn_id: str | None  # the open turn/start event id this state belongs to
    approved: bool | None  # last approval decision, consumed by execute_node
    next_node: str  # routing signal computed by whichever node just ran


def new_state(
    conversation_id: str,
    persona_id: str,
    *,
    thread_id: str | None = None,
    task_id: str | None = None,
) -> TapestryGraphState:
    """Build a fresh initial state dict with every field's documented
    default filled in. `TypedDict` itself can't carry runtime defaults
    (`delegation_depth: int = 0` in the task brief is aspirational for a
    TypedDict), so this is the actual place those defaults live.
    """
    return TapestryGraphState(
        conversation_id=conversation_id,
        persona_id=persona_id,
        messages=[],
        pending_tool_call=None,
        delegation_depth=0,
        turn_count=0,
        task_id=task_id,
        thread_id=thread_id,
        turn_id=None,
        approved=None,
        next_node="persona",
    )


def _with_thread(state: TapestryGraphState, payload: dict) -> dict:
    """Inject `thread_id` into an event payload iff the state carries one.

    Every event this module appends goes through this helper, per the
    thread-support requirement: `core.conversations.derive_messages` can
    only ever filter by thread once the data is actually present on
    events, so this is the one choke point that guarantees it is.
    """
    thread_id = state.get("thread_id")
    if thread_id:
        return {**payload, "thread_id": thread_id}
    return payload


# ---------------------------------------------------------------------------
# Tool registry — the real, canonical names resolving personas/*.yaml's
# placeholder gap. See the module docstring's judgment-call note.
#
# Every entry is an `async def(arguments: dict) -> ToolResult` so
# `execute_node` can dispatch uniformly regardless of whether the
# underlying shim is sync (`FileEditorTool`/`TerminalTool`, wrapped in
# `asyncio.to_thread` so a slow shell command doesn't block the event
# loop) or already async (`MetaMCPClient`). Nothing here is constructed at
# IMPORT time -- `TerminalExecutor`'s own docs are explicit that
# construction has real side effects (it can spawn a tmux server if
# misconfigured) and `TerminalTool.close()` is documented as mandatory
# cleanup, so every terminal-backed wrapper constructs its own `TerminalTool`
# per call and closes it in a `finally`.
# ---------------------------------------------------------------------------

_WORKSPACE_ROOT_ENV_VAR = "TAPESTRY_WORKSPACE_ROOT"
_ALLOWED_EDIT_PATHS_ENV_VAR = "TAPESTRY_ALLOWED_EDIT_PATHS"
_GIT_MCP_PREFIX_ENV_VAR = "TAPESTRY_GIT_MCP_TOOL_PREFIX"
_DEPLOY_MCP_PREFIX_ENV_VAR = "TAPESTRY_DEPLOY_MCP_TOOL_PREFIX"
_DEFAULT_GIT_MCP_PREFIX = "Git__"
_DEFAULT_DEPLOY_MCP_PREFIX = "Deploy__"

DELEGATE_TOOL_NAME = "delegate"
TASK_COMPLETE_TOOL_NAME = "task_complete"
SKILL_LOADER_TOOL_NAME = "skill_loader"

# Tools whose proposal must go through approval_node's interrupt() gate
# before execute_node ever runs them -- anything that mutates files,
# shell/repo state, or deploys. Deliberately excludes test_runner (running
# the test suite is expected, safe, read-mostly behavior for a QA persona
# per vex.yaml) and every read-only tool.
TOOLS_REQUIRING_APPROVAL: frozenset[str] = frozenset(
    {"file_editor", "terminal", "git", "deploy_pipeline"}
)

# Tools whose successful result should also produce a task/diff_ready event
# (files actually changed, so a frontend diff screen has something to show).
DIFF_PRODUCING_TOOLS: frozenset[str] = frozenset({"file_editor", "git"})

# Interim permission-wrapping guard, NOT a sandbox — the real sandbox
# boundary is the Phase 4 Docker tool-runner (see project_structure.md).
# Rejects the obvious ways a "read only"/"git only"/"test only" tool could
# be walked into running something else via shell metacharacters.
_SHELL_METACHARACTERS = (";", "&&", "||", "|", "`", "$(")

_READ_ONLY_COMMAND_PREFIXES = (
    "ls", "cat", "grep", "find", "pwd", "head", "tail", "wc", "echo",
    "git status", "git log", "git diff", "git show",
)
_TEST_COMMAND_PREFIXES = (
    "pytest", "python -m pytest", "uv run pytest",
    "npm test", "npm run test", "pnpm test", "pnpm run test",
)


def _workspace_root() -> str:
    return os.environ.get(_WORKSPACE_ROOT_ENV_VAR, os.getcwd())


def _allowed_edit_paths() -> list[str] | None:
    """Interim, env-driven allow-list for `file_editor`'s write access.

    `tools/file_editor.py`'s own security note is unambiguous: leaving
    `allowed_paths` unset means "edit anything this process can write to."
    Until a real per-conversation/per-workspace config layer exists
    (`config.py`, not built yet), this env var is the only lever an
    operator has to close that gap — documented here rather than silently
    left wide open with no way to restrict it at all.
    """
    raw = os.environ.get(_ALLOWED_EDIT_PATHS_ENV_VAR)
    if not raw:
        return None
    return [p for p in raw.split(os.pathsep) if p]


def _shell_command_is_safe(command: str) -> bool:
    return not any(token in command for token in _SHELL_METACHARACTERS)


async def _tool_file_editor_read(arguments: dict) -> ToolResult:
    """Read-only file view. Hardcodes `command="view"` unconditionally —
    any `"command"` the model tries to pass is ignored, not merely
    rejected, so this wrapper structurally cannot do anything else.
    """
    tool = FileEditorTool(workspace_root=_workspace_root())
    path = str(arguments.get("path", ""))
    kwargs = {k: v for k, v in arguments.items() if k == "view_range"}
    return await asyncio.to_thread(tool.run, "view", path, **kwargs)


async def _tool_file_editor(arguments: dict) -> ToolResult:
    """Full file create/edit access, gated by approval_node upstream and
    by the (currently interim, env-driven) `allowed_paths` allow-list.
    """
    tool = FileEditorTool(workspace_root=_workspace_root(), allowed_paths=_allowed_edit_paths())
    command = str(arguments.get("command", "view"))
    path = str(arguments.get("path", ""))
    kwargs = {k: v for k, v in arguments.items() if k not in ("command", "path")}
    return await asyncio.to_thread(tool.run, command, path, **kwargs)


async def _tool_terminal(arguments: dict) -> ToolResult:
    """Unrestricted shell access, gated by approval_node upstream."""
    command = str(arguments.get("command", ""))
    tool = TerminalTool(working_dir=_workspace_root())
    try:
        return await asyncio.to_thread(tool.run, command, arguments.get("timeout"))
    finally:
        tool.close()


async def _run_restricted_terminal(
    command: str, timeout: float | None, allowed_prefixes: tuple[str, ...], label: str
) -> ToolResult:
    command = command.strip()
    if not command:
        return ToolResult(text=f"{label} requires a non-empty 'command' argument.", is_error=True)
    if not _shell_command_is_safe(command):
        return ToolResult(
            text=f"{label} rejected: command contains disallowed shell metacharacters: {command!r}",
            is_error=True,
        )
    if not command.startswith(allowed_prefixes):
        return ToolResult(
            text=f"{label} rejected: {command!r} is not an allow-listed command.",
            is_error=True,
        )
    tool = TerminalTool(working_dir=_workspace_root())
    try:
        return await asyncio.to_thread(tool.run, command, timeout)
    finally:
        tool.close()


async def _tool_terminal_read_only(arguments: dict) -> ToolResult:
    return await _run_restricted_terminal(
        str(arguments.get("command", "")), arguments.get("timeout"),
        _READ_ONLY_COMMAND_PREFIXES, "terminal_read_only",
    )


async def _tool_test_runner(arguments: dict) -> ToolResult:
    command = str(arguments.get("command") or "pytest")
    return await _run_restricted_terminal(
        command, arguments.get("timeout"), _TEST_COMMAND_PREFIXES, "test_runner"
    )


async def _tool_git(arguments: dict) -> ToolResult:
    """Git operations via the metamcp Git server — per the scoped spec's
    engine-layer list ("anything not covered [by openhands-tools] — web
    search, browser, git, etc. — comes in as MCP tools via [metamcp]"),
    not a raw `git` shell wrapper. `tool_name` must be namespaced under the
    configured git-server prefix (metamcp's own convention is `{ServerName}
    __{originalToolName}`, per `tools/mcp_client.py`); the exact server
    name is an operator/deployment concern this module intentionally
    leaves configurable rather than hardcoding a guess.
    """
    prefix = os.environ.get(_GIT_MCP_PREFIX_ENV_VAR, _DEFAULT_GIT_MCP_PREFIX)
    tool_name = str(arguments.get("tool_name", ""))
    if not tool_name.startswith(prefix):
        return ToolResult(
            text=f"git tool rejected: {tool_name!r} is not under the configured "
            f"git MCP prefix {prefix!r}",
            is_error=True,
        )
    client = MetaMCPClient()
    return await client.call_tool(tool_name, arguments.get("arguments") or {})


async def _tool_deploy_pipeline(arguments: dict) -> ToolResult:
    """Deployment via the metamcp Deploy server — same reasoning as
    `_tool_git` above. Always in `TOOLS_REQUIRING_APPROVAL`; combined with
    Nova (the only persona holding this tool) starting `status: paused` by
    default, this is deliberately the most gated path in the registry.
    """
    prefix = os.environ.get(_DEPLOY_MCP_PREFIX_ENV_VAR, _DEFAULT_DEPLOY_MCP_PREFIX)
    tool_name = str(arguments.get("tool_name", ""))
    if not tool_name.startswith(prefix):
        return ToolResult(
            text=f"deploy_pipeline rejected: {tool_name!r} is not under the configured "
            f"deploy MCP prefix {prefix!r}",
            is_error=True,
        )
    client = MetaMCPClient()
    return await client.call_tool(tool_name, arguments.get("arguments") or {})


async def _tool_skill_loader(arguments: dict) -> ToolResult:
    """Tier-two skill discovery: load one skill's full body by name.

    Never passes `bypass_invocation_check=True` — that bypass is reserved
    exclusively for the human `/skill-name` gesture path, which lives
    above this graph. A model-initiated call for a
    `disable_model_invocation=True` skill correctly comes back as an
    error result, not a crash.
    """
    name = str(arguments.get("name", ""))
    try:
        body = _SKILL_REGISTRY.load_body(name)
        return ToolResult(text=body, is_error=False)
    except SkillNotFoundError as exc:
        return ToolResult(text=str(exc), is_error=True)


TOOL_REGISTRY: dict[str, Callable[[dict], Awaitable[ToolResult]]] = {
    "file_editor_read": _tool_file_editor_read,
    "file_editor": _tool_file_editor,
    "terminal": _tool_terminal,
    "terminal_read_only": _tool_terminal_read_only,
    "git": _tool_git,
    "test_runner": _tool_test_runner,
    "deploy_pipeline": _tool_deploy_pipeline,
    SKILL_LOADER_TOOL_NAME: _tool_skill_loader,
}


def _function_schema(name: str, description: str, parameters: dict) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


# Function-calling schemas passed to `models.litellm_client.call_model`'s
# `tools=` argument (LiteLLM/OpenAI function-calling shape). Every
# TOOL_REGISTRY key has a matching schema here; DELEGATE_TOOL_NAME and
# TASK_COMPLETE_TOOL_NAME are intercepted directly inside persona_node
# (never dispatched through TOOL_REGISTRY/execute_node) but still need
# schemas so the model can actually call them.
TOOL_SCHEMAS: dict[str, dict] = {
    "file_editor_read": _function_schema(
        "file_editor_read",
        "Read-only view of a file's contents (or a directory listing).",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "view_range": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["path"],
        },
    ),
    "file_editor": _function_schema(
        "file_editor",
        "Create or edit a file. Requires human approval before it runs.",
        {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "enum": ["create", "str_replace", "insert", "undo_edit", "view"],
                },
                "path": {"type": "string"},
                "file_text": {"type": "string"},
                "old_str": {"type": "string"},
                "new_str": {"type": "string"},
                "insert_line": {"type": "integer"},
            },
            "required": ["command", "path"],
        },
    ),
    "terminal": _function_schema(
        "terminal",
        "Run any shell command. Requires human approval before it runs.",
        {
            "type": "object",
            "properties": {"command": {"type": "string"}, "timeout": {"type": "number"}},
            "required": ["command"],
        },
    ),
    "terminal_read_only": _function_schema(
        "terminal_read_only",
        "Run a read-only shell command (ls/cat/grep/find/pwd/head/tail/wc/echo/"
        "git status/git log/git diff/git show only). No approval needed.",
        {
            "type": "object",
            "properties": {"command": {"type": "string"}, "timeout": {"type": "number"}},
            "required": ["command"],
        },
    ),
    "git": _function_schema(
        "git",
        "Run a git operation via the metamcp Git server. Requires human approval.",
        {
            "type": "object",
            "properties": {"tool_name": {"type": "string"}, "arguments": {"type": "object"}},
            "required": ["tool_name"],
        },
    ),
    "test_runner": _function_schema(
        "test_runner",
        "Run the project's test suite (pytest / npm test / pnpm test only). "
        "No approval needed.",
        {
            "type": "object",
            "properties": {"command": {"type": "string"}, "timeout": {"type": "number"}},
            "required": [],
        },
    ),
    "deploy_pipeline": _function_schema(
        "deploy_pipeline",
        "Trigger a deployment via the metamcp Deploy server. Requires human approval.",
        {
            "type": "object",
            "properties": {"tool_name": {"type": "string"}, "arguments": {"type": "object"}},
            "required": ["tool_name"],
        },
    ),
    SKILL_LOADER_TOOL_NAME: _function_schema(
        SKILL_LOADER_TOOL_NAME,
        "Load the full procedure body of a named skill from the catalog. "
        "No approval needed.",
        {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    ),
    DELEGATE_TOOL_NAME: _function_schema(
        DELEGATE_TOOL_NAME,
        "Delegate part of this task to another named persona.",
        {
            "type": "object",
            "properties": {"to_persona": {"type": "string"}, "text": {"type": "string"}},
            "required": ["to_persona", "text"],
        },
    ),
    TASK_COMPLETE_TOOL_NAME: _function_schema(
        TASK_COMPLETE_TOOL_NAME,
        "Declare the current task complete. Triggers mandatory self-verification "
        "(graph.verify) before it actually closes — it may come back rejected.",
        {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
    ),
}


def _build_system_prompt(persona: Persona, catalog: list[SkillSummary]) -> str:
    lines = [persona.system_prompt.strip(), ""]
    if catalog:
        lines.append(
            f"Available skills (call the {SKILL_LOADER_TOOL_NAME!r} tool with "
            '{"name": "<skill-name>"} to read the full procedure):'
        )
        lines.extend(f"- {summary.name}: {summary.description}" for summary in catalog)
    else:
        lines.append("No skills are currently available.")
    return "\n".join(lines)


def _build_tool_schemas(persona: Persona) -> list[dict]:
    schemas = [TOOL_SCHEMAS[name] for name in persona.tools if name in TOOL_SCHEMAS]
    # Core orchestration capabilities every persona has, regardless of its
    # own permissioned `tools:` list.
    schemas.append(TOOL_SCHEMAS[SKILL_LOADER_TOOL_NAME])
    schemas.append(TOOL_SCHEMAS[DELEGATE_TOOL_NAME])
    schemas.append(TOOL_SCHEMAS[TASK_COMPLETE_TOOL_NAME])
    return schemas


def _parse_tool_arguments(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)  # type: ignore[arg-type]
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _original_ask(conversation_id: str) -> str:
    """The first `user/message` in the conversation, used as `verify.
    verify_before_completion`'s `task_description` — the best available
    proxy for "the original ask" without a dedicated task-description
    field anywhere upstream yet.
    """
    for message in derive_messages(conversation_id):
        if message.event_type == "user/message":
            return message.text
    return ""


# ---------------------------------------------------------------------------
# persona_node
# ---------------------------------------------------------------------------


async def persona_node(state: TapestryGraphState) -> dict:
    conversation_id = state["conversation_id"]
    turn_count = state.get("turn_count", 0)

    # Turn cap FIRST, before any other work -- per the task brief's own
    # placement instruction ("turn cap in the persona node before
    # proceeding").
    budgets.check_turn_budget(turn_count)

    persona = _get_persona(state["persona_id"])

    turn_id = state.get("turn_id")
    if turn_id is None:
        turn_start_event = events.append_event(
            conversation_id, "turn/start", actor=persona.id, payload=_with_thread(state, {})
        )
        turn_id = turn_start_event.id

    # Two-tier skill discovery, tier one: keep the catalog coherent in the
    # log (content-hash-then-replace, idempotent — safe to call every
    # cycle), then read it back for the system prompt.
    sync_catalog(conversation_id, _SKILL_REGISTRY)
    catalog = _SKILL_REGISTRY.discover()

    system_prompt = _build_system_prompt(persona, catalog)
    tool_schemas = _build_tool_schemas(persona)
    messages = [{"role": "system", "content": system_prompt}] + list(state.get("messages", []))

    streaming.emit(
        "persona/thinking", {"persona_id": persona.id, "conversation_id": conversation_id}
    )
    response = await call_model(model=persona.model, messages=messages, tools=tool_schemas)
    streaming.emit(
        "persona/responded", {"persona_id": persona.id, "conversation_id": conversation_id}
    )

    # Cost/token measurement source of truth (budgets.measure_conversation_cost
    # sums these back up later) — appended right where the model call
    # actually happens, per the task brief.
    events.append_event(
        conversation_id,
        "model/response",
        actor=persona.id,
        payload=_with_thread(
            state,
            {
                "cost": response.cost,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "text_preview": response.text[:500],
            },
        ),
    )

    tool_call = response.tool_calls[0] if response.tool_calls else None

    if tool_call is None:
        # Plain reply: no tool proposed, no delegation, no completion
        # claim. The turn ends here.
        events.append_event(
            conversation_id,
            "assistant/message",
            actor=persona.id,
            payload=_with_thread(state, {"text": response.text}),
        )
        events.append_event(
            conversation_id,
            "turn/end",
            actor=persona.id,
            payload={"turn_id": turn_id, "reason": "assistant_reply"},
        )
        new_messages = messages[1:] + [{"role": "assistant", "content": response.text}]
        return {"messages": new_messages, "turn_id": turn_id, "next_node": "end"}

    function = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
    tool_name = function.get("name", "")
    arguments = _parse_tool_arguments(function.get("arguments"))
    new_messages = messages[1:] + [
        {"role": "assistant", "content": response.text, "tool_calls": [tool_call]}
    ]

    if tool_name == DELEGATE_TOOL_NAME:
        return await _handle_delegate(state, persona, turn_id, arguments, new_messages)

    if tool_name == TASK_COMPLETE_TOOL_NAME:
        return await _handle_task_complete(state, persona, turn_id, arguments, new_messages)

    if tool_name not in TOOL_REGISTRY or (
        tool_name != SKILL_LOADER_TOOL_NAME and tool_name not in persona.tools
    ):
        feedback = {
            "role": "tool",
            "tool_call_id": tool_call.get("id", ""),
            "content": f"Tool {tool_name!r} is not permitted for persona {persona.id!r}.",
        }
        return {
            "messages": new_messages + [feedback],
            "turn_id": turn_id,
            "turn_count": turn_count + 1,
            "next_node": "persona",
        }

    task_id = state.get("task_id")
    if task_id is None:
        task_id = uuid.uuid4().hex
        events.append_event(
            conversation_id,
            "task/started",
            actor=persona.id,
            payload=_with_thread(state, {"task_id": task_id, "description": response.text[:500]}),
        )

    pending_tool_call = {
        "tool_name": tool_name,
        "arguments": arguments,
        "call_id": tool_call.get("id") or uuid.uuid4().hex,
    }

    if tool_name in TOOLS_REQUIRING_APPROVAL:
        ask_request_id = uuid.uuid4().hex
        question = AskQuestion(
            id=ask_request_id,
            question=f"Approve {persona.name} running {tool_name!r}?",
            detail=json.dumps(arguments, sort_keys=True),
            options=["approve", "reject"],
            intent="approval",
            related_task_id=task_id,
        )
        # Written here, exactly once — persona_node never re-executes due
        # to approval_node's interrupt(), unlike anything inside
        # approval_node itself. See module docstring.
        events.append_event(
            conversation_id,
            "ask/requested",
            actor="system",
            payload=_with_thread(state, {"questions": [question.model_dump()]}),
        )
        pending_tool_call["ask_request_id"] = ask_request_id
        next_node = "approval"
    else:
        next_node = "execute"

    return {
        "messages": new_messages,
        "pending_tool_call": pending_tool_call,
        "task_id": task_id,
        "turn_id": turn_id,
        "next_node": next_node,
    }


async def _handle_delegate(
    state: TapestryGraphState,
    persona: Persona,
    turn_id: str,
    arguments: dict,
    new_messages: list[dict],
) -> dict:
    conversation_id = state["conversation_id"]
    delegation_depth = state.get("delegation_depth", 0)
    turn_count = state.get("turn_count", 0)
    to_persona = str(arguments.get("to_persona", ""))
    text = str(arguments.get("text", ""))

    if to_persona not in PERSONAS:
        feedback = {
            "role": "tool",
            "tool_call_id": "",
            "content": f"Cannot delegate: unknown persona {to_persona!r}.",
        }
        return {
            "messages": new_messages + [feedback],
            "turn_id": turn_id,
            "turn_count": turn_count + 1,
            "next_node": "persona",
        }

    try:
        # Persisted, checkpointed recursion-depth cap — checked BEFORE the
        # (separate) round cap that core.delegation.delegate() itself
        # enforces, so a depth violation is reported as exactly that and
        # never masked by a round-limit error instead.
        budgets.check_delegation_depth(delegation_depth)
        await delegate(conversation_id, from_persona=persona.id, to_persona=to_persona, text=text)
    except (DelegationDepthExceeded, DelegationRoundLimitExceeded) as exc:
        # A hard cap is a real stopping decision, not a crash to hide —
        # close the turn with a real reason before letting it propagate.
        # NEVER "interrupted" -- that reason string is reserved exclusively
        # for events.close_orphaned_turns's crash-recovery repair path.
        events.append_event(
            conversation_id,
            "turn/end",
            actor=persona.id,
            payload={"turn_id": turn_id, "reason": f"delegation_budget_exceeded:{type(exc).__name__}"},
        )
        raise

    return {
        "messages": new_messages,
        "persona_id": to_persona,
        "delegation_depth": delegation_depth + 1,
        "turn_count": turn_count + 1,
        "turn_id": turn_id,
        "next_node": "persona",
    }


async def _handle_task_complete(
    state: TapestryGraphState,
    persona: Persona,
    turn_id: str,
    arguments: dict,
    new_messages: list[dict],
) -> dict:
    conversation_id = state["conversation_id"]
    turn_count = state.get("turn_count", 0)
    task_id = state.get("task_id")
    task_description = _original_ask(conversation_id) or str(arguments.get("summary", ""))

    result = await verify.verify_before_completion(conversation_id, task_description, persona)

    if result.passed:
        events.append_event(
            conversation_id,
            "task/completed",
            actor=persona.id,
            payload=_with_thread(state, {"task_id": task_id, "notes": result.notes}),
        )
        events.append_event(
            conversation_id,
            "turn/end",
            actor=persona.id,
            payload={"turn_id": turn_id, "reason": "task_completed"},
        )
        return {"messages": new_messages, "turn_id": turn_id, "next_node": "end"}

    events.append_event(
        conversation_id,
        "task/verification_failed",
        actor=persona.id,
        payload=_with_thread(state, {"task_id": task_id, "notes": result.notes}),
    )
    feedback = {
        "role": "user",
        "content": f"Self-verification failed — this task is not actually complete yet:\n{result.notes}",
    }
    return {
        "messages": new_messages + [feedback],
        "turn_id": turn_id,
        "turn_count": turn_count + 1,
        "next_node": "persona",
    }


def _route_from_persona(state: TapestryGraphState) -> str:
    return state.get("next_node", "end")


# ---------------------------------------------------------------------------
# approval_node — pure gate. See module docstring's judgment-call note for
# why nothing here writes to the event log before `interrupt()` returns.
# ---------------------------------------------------------------------------


def _decode_decision(decision: object) -> bool:
    """Normalize whatever a resumer passed via `Command(resume=...)` into a
    plain approve/reject bool. Accepts a bare bool, an `AskAnswer`-shaped
    dict (`{"selected": [...]}` or `{"custom": "..."}`), or a plain string
    — matching the several reasonable shapes a Discord button click, a
    Telegram inline-keyboard callback, or a web form submit might produce.
    """
    if isinstance(decision, bool):
        return decision
    if isinstance(decision, dict):
        selected = decision.get("selected")
        if selected:
            return selected[0] == "approve"
        custom = decision.get("custom")
        if isinstance(custom, str):
            return custom.strip().lower() == "approve"
        return False
    if isinstance(decision, str):
        return decision.strip().lower() == "approve"
    return False


def approval_node(state: TapestryGraphState) -> dict:
    pending = state.get("pending_tool_call") or {}
    request_id = pending.get("ask_request_id", "unknown")

    # Pure, deterministic recomputation from already-committed state — safe
    # to re-execute on every resume. No event-log write, no tool call,
    # nothing here that isn't idempotent.
    interrupt_value = {
        "request_id": request_id,
        "tool_name": pending.get("tool_name"),
        "arguments": pending.get("arguments"),
        "related_task_id": state.get("task_id"),
    }
    decision = interrupt(interrupt_value)

    # Everything below this line runs exactly once per real resume — the
    # pausing execution never reaches here (interrupt() raised instead).
    approved = _decode_decision(decision)
    conversation_id = state["conversation_id"]
    events.append_event(
        conversation_id,
        "ask/answered",
        actor="human",
        payload=_with_thread(
            state,
            {
                "request_id": request_id,
                "answers": [
                    {
                        "id": request_id,
                        "selected": ["approve"] if approved else ["reject"],
                        "custom": None,
                    }
                ],
            },
        ),
    )

    if approved:
        return {"approved": True, "next_node": "execute"}

    turn_count = state.get("turn_count", 0)
    messages = list(state.get("messages", []))
    feedback = {
        "role": "tool",
        "tool_call_id": pending.get("call_id", ""),
        "content": "Rejected by human reviewer.",
    }
    return {
        "approved": False,
        "pending_tool_call": None,
        "messages": messages + [feedback],
        "turn_count": turn_count + 1,
        "next_node": "persona",
    }


def _route_from_approval(state: TapestryGraphState) -> str:
    return state.get("next_node", "persona")


# ---------------------------------------------------------------------------
# execute_node — the only place a tool call actually runs. Only ever
# reached after approval_node resumes approved, or directly from
# persona_node for a tool outside TOOLS_REQUIRING_APPROVAL. Never calls
# interrupt(), so a tool call made here runs exactly once per proposal.
# ---------------------------------------------------------------------------


async def execute_node(state: TapestryGraphState) -> dict:
    conversation_id = state["conversation_id"]
    persona = _get_persona(state["persona_id"])
    pending = state.get("pending_tool_call") or {}
    tool_name = pending.get("tool_name", "")
    arguments = pending.get("arguments", {})
    task_id = state.get("task_id")
    turn_count = state.get("turn_count", 0)

    streaming.emit("tool/status", {"tool_name": tool_name, "status": "running"})
    tool_fn = TOOL_REGISTRY[tool_name]
    result = await tool_fn(arguments)
    streaming.emit(
        "tool/status", {"tool_name": tool_name, "status": "done", "is_error": result.is_error}
    )

    events.append_event(
        conversation_id,
        "tool/result",
        actor=persona.id,
        payload=_with_thread(
            state,
            {
                "task_id": task_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "text": result.text,
                "is_error": result.is_error,
            },
        ),
    )

    if tool_name in DIFF_PRODUCING_TOOLS and not result.is_error:
        events.append_event(
            conversation_id,
            "task/diff_ready",
            actor=persona.id,
            payload=_with_thread(
                state,
                {
                    "task_id": task_id,
                    "files_changed": [arguments["path"]] if "path" in arguments else [],
                    "diff_summary": result.text[:2000],
                },
            ),
        )

    messages = list(state.get("messages", []))
    tool_message = {
        "role": "tool",
        "tool_call_id": pending.get("call_id", ""),
        "content": result.text,
    }

    return {
        "messages": messages + [tool_message],
        "pending_tool_call": None,
        "approved": None,
        "turn_count": turn_count + 1,
        "next_node": "persona",
    }


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


async def build_graph(checkpoint_path: str | None = None) -> CompiledStateGraph:
    """Compile the full persona -> approval -> execute graph.

    `async def`, not the task brief's literal `-> CompiledGraph` sync
    signature — forced by `checkpointer.get_checkpointer` also needing to
    be async (see that module's own docstring for why). `CompiledGraph`
    doesn't exist as an importable name in this version of LangGraph; the
    real compiled-graph type is `langgraph.graph.state.CompiledStateGraph`,
    used here.
    """
    saver = await get_checkpointer(checkpoint_path)

    builder = StateGraph(TapestryGraphState)
    builder.add_node("persona", persona_node)
    builder.add_node("approval", approval_node)
    builder.add_node("execute", execute_node)

    builder.add_edge(START, "persona")
    builder.add_conditional_edges(
        "persona",
        _route_from_persona,
        {"approval": "approval", "execute": "execute", "persona": "persona", "end": END},
    )
    builder.add_conditional_edges(
        "approval", _route_from_approval, {"execute": "execute", "persona": "persona"}
    )
    builder.add_edge("execute", "persona")

    return builder.compile(checkpointer=saver)
