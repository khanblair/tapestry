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
(`stream_mode="custom"` + `StreamWriter`). `persona_node`/`execute_node`
emit coarse status frames (`"persona/thinking"`, `"persona/responded"`,
`"tool/status"`) via `streaming.emit`, proving the wiring end-to-end, but
NOT per-token deltas.

UPDATE: the precondition this note originally flagged — "real token
streaming needs a `call_model(..., stream=True)` variant added to
`models/litellm_client.py` first" — is now met. `models.litellm_client.
call_model_stream` exists (same empty-completion-retry and
context-window-reclassification policies as `call_model`, adapted for a
stream; see its docstring). `persona_node` still calls the non-streaming
`call_model` below, deliberately: `call_model_stream` yields `StreamChunk`
fragments (`delta_text` / `tool_call_delta` / `finish_reason` / `usage`),
not a single `ModelResponse` object, and `persona_node` needs exactly one
`ModelResponse`-shaped thing — `response.text`, `response.tool_calls[0]`,
`response.cost`, `response.input_tokens/output_tokens` — to build the
durable `"model/response"` event that `budgets.measure_conversation_cost`
sums over. Wiring the streaming variant in here means first deciding, for
this node specifically: where the chunk-to-`ModelResponse` reconstruction
lives (recipe is in `call_model_stream`'s docstring), and whether the
durable event/tool-dispatch commits after the first attempt or only once
`call_model_stream` returns without raising (a retry-duplication question
`call_model_stream`'s docstring answers for the raw stream, but
`persona_node`'s own event-logging and tool-approval flow need their own
answer, since those are checkpointed, not transient like `streaming.emit`
frames). That's real design work belonging to whoever wires the
Discord/Telegram/web adapters — left as an explicit, actionable follow-up
rather than guessed at here.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Awaitable, Callable, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from tapestry.core import events
from tapestry.core.ask import AskQuestion
from tapestry.core.conversations import derive_messages
from tapestry.core.delegation import DelegationRoundLimitExceeded, delegate
from tapestry.core.personas import Persona, load_personas
from tapestry.graph import budgets, streaming, verify
from tapestry.graph.diff_capture import capture_workspace_diff
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
    # tapestry_modes_models_personas_spec.md §2.2: a "once" scope model
    # override, applying to exactly the next persona_node pass. Not event-
    # logged (unlike session/global scope) -- genuinely ephemeral, cleared
    # by persona_node itself immediately after use. Set externally via
    # `graph.aupdate_state(config, {"model_override_once": model})`, the
    # same mechanism session/global scope don't need since those live in
    # the event log instead.
    model_override_once: str | None


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

    `model_override_once` is deliberately ABSENT from this dict, not set
    to `None` -- every adapter calls `new_state()` fresh for EVERY
    external turn (not just the first; see the `starting_new_turn` fix in
    `persona_node`), and this return value becomes an `ainvoke` input that
    REPLACES checkpointed state for any key it sets. A once-scope model
    override is set by a separate action (the model-switch endpoint,
    via `graph.aupdate_state`) specifically to survive the gap until the
    NEXT `new_state()`-triggered turn consumes it -- if this function set
    `model_override_once=None` here, every such override would be wiped
    the instant the human's next message arrived, before persona_node
    ever got to read it. Omitting the key lets LangGraph's merge keep
    whatever was already checkpointed (a real override, or nothing at all
    for a conversation where one was never set).
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

# ---------------------------------------------------------------------------
# Modes — tapestry_modes_models_personas_spec.md §1. `TOOLS_REQUIRING_APPROVAL`
# above remains the OUTER gate (a tool not in that set never goes through
# approval_node, in ANY mode) — this tier map only distinguishes *within*
# that set, since a flat set can express Manual/Bypass but not "Accept
# edits" (gate everything except file edits) or Auto's tiered handling.
# ---------------------------------------------------------------------------

ToolRiskTier = Literal["safe", "edit", "mutate", "deploy"]

TOOL_RISK_TIER: dict[str, ToolRiskTier] = {
    "file_editor_read": "safe",
    "terminal_read_only": "safe",
    "test_runner": "safe",
    "skill_loader": "safe",
    "file_editor": "edit",
    "terminal": "mutate",
    "git": "mutate",
    "deploy_pipeline": "deploy",
}

# The tools Plan mode intersects a persona's own `tools:` list down to —
# everything else in TOOL_RISK_TIER ("edit"/"mutate"/"deploy") becomes
# unreachable for that turn, regardless of what the persona's YAML
# otherwise permits. See spec §1.4: this is deliberately an intersection,
# not a replacement, so a persona with an already-narrower toolset (Ada)
# keeps its own stronger, mode-independent guarantee.
_PLAN_MODE_TOOLS: frozenset[str] = frozenset(
    name for name, tier in TOOL_RISK_TIER.items() if tier == "safe"
)

VALID_MODES: frozenset[str] = frozenset({"manual", "accept_edits", "auto", "plan", "bypass"})

_GUARDIAN_MODEL_ENV_VAR = "TAPESTRY_GUARDIAN_MODEL"


def _effective_tools(persona_tools: list[str], mode: str) -> list[str]:
    """The tool names actually offered to the model this turn — `persona.
    tools` itself in every mode except Plan, which intersects it with the
    read-only ("safe" tier) set. `skill_loader`/`delegate`/`task_complete`
    are unaffected either way: `_build_tool_schemas` appends those
    unconditionally, they are orchestration primitives, not
    edit/mutate/deploy-tier tools Plan mode has any reason to block.
    """
    if mode == "plan":
        return [name for name in persona_tools if name in _PLAN_MODE_TOOLS]
    return persona_tools


def resolve_mode(conversation_id: str, persona: Persona) -> str:
    """The active mode for `persona` in `conversation_id`: the most recent
    `mode/changed` event scoped to this persona, falling back to `persona.
    default_mode` when none exists yet. Per spec §1.6, mode is neither
    purely static (baked into the persona) nor a purely global session
    toggle — a per-conversation override, scoped per-persona so a group
    conversation's personas can each be in a different mode.
    """
    for event in reversed(events.read_events(conversation_id)):
        if event.type == "mode/changed" and event.payload.get("persona_id") == persona.id:
            mode = event.payload.get("mode")
            if mode in VALID_MODES:
                return mode
    return persona.default_mode


def resolve_model(
    state: TapestryGraphState, conversation_id: str, persona: Persona
) -> tuple[str, bool]:
    """The effective model for this call: `state["model_override_once"]`
    (once-scope, checked first and highest priority) > the most recent
    `persona/model_switched` event scoped to this persona (session-scope)
    > `persona.model` (global). Returns `(model, consumed_once)` —
    `consumed_once` tells the caller to clear `model_override_once` from
    state after this call, since a once-scope override applies to exactly
    one `persona_node` pass, not durable history (spec §2.2).
    """
    once_override = state.get("model_override_once")
    if once_override:
        return once_override, True
    for event in reversed(events.read_events(conversation_id)):
        if event.type == "persona/model_switched" and event.payload.get("persona_id") == persona.id:
            model = event.payload.get("model")
            if model:
                return model, False
    return persona.model, False


async def _guardian_review(persona: Persona, tool_name: str, arguments: dict) -> str:
    """Auto mode's guardian check (spec §1.5) for an "edit"/"mutate"-tier
    tool proposal — a second, cheap model call that reviews one proposed
    tool call and returns `"approve"` or `"escalate"`. Deliberately never
    reached for "deploy"-tier tools (see `_mode_requires_approval`) or in
    any mode other than Auto.

    Fails closed on every error path — no configured guardian model, the
    model call itself raising, an unparseable response — all resolve to
    `"escalate"`, never a silent auto-approve. A misconfigured or failing
    guardian must never be MORE permissive than asking a human.
    """
    guardian_model = persona.guardian_model or os.environ.get(_GUARDIAN_MODEL_ENV_VAR)
    if not guardian_model:
        return "escalate"

    prompt = (
        "You are a safety reviewer screening ONE proposed tool call before it "
        "runs without further human review. Judge only whether this specific "
        "call is safe and reasonable to run unattended.\n\n"
        f"Tool: {tool_name}\n"
        f"Arguments: {json.dumps(arguments, sort_keys=True)}\n\n"
        "Respond with exactly one word: APPROVE if this is safe to run "
        "unattended, or ESCALATE if a human should review it first. When in "
        "doubt, ESCALATE."
    )
    try:
        response = await call_model(
            model=guardian_model, messages=[{"role": "user", "content": prompt}], tools=None
        )
    except Exception:
        return "escalate"

    decision = response.text.strip().upper()
    return "approve" if decision.startswith("APPROVE") else "escalate"


async def _mode_requires_approval(
    mode: str, tier: ToolRiskTier, persona: Persona, tool_name: str, arguments: dict
) -> bool:
    """Whether a proposed `tool_name` (already known to be in
    `TOOLS_REQUIRING_APPROVAL`, i.e. tier is "edit"/"mutate"/"deploy") must
    still go through `approval_node`'s `interrupt()`, under `mode`. Table
    is spec §1.4's, implemented exactly:

        Manual        -> always True
        Bypass        -> always False
        Accept edits  -> False for "edit", True otherwise
        Auto          -> True for "deploy" (never guardian-eligible,
                          matching Nova's own "most gated persona" design
                          intent); guardian-screened otherwise
        Plan          -> True (defensive fallback only — Plan's toolset
                          intersection already prevents this tier from
                          ever being proposed in the first place)
    """
    if mode == "bypass":
        return False
    if mode == "manual":
        return True
    if mode == "accept_edits":
        return tier != "edit"
    if mode == "auto":
        if tier == "deploy":
            return True
        decision = await _guardian_review(persona, tool_name, arguments)
        return decision != "approve"
    return True

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


def _build_tool_schemas(tool_names: list[str]) -> list[dict]:
    schemas = [TOOL_SCHEMAS[name] for name in tool_names if name in TOOL_SCHEMAS]
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


def _chat_messages_from_log(conversation_id: str, persona: Persona) -> list[dict]:
    """Cross-turn conversation history for a NEW turn, rebuilt from
    `core.conversations.derive_messages` (the event log's narrow,
    model-facing message projection — see that module's own docstring on
    why it's deliberately narrower than the human-facing timeline) rather
    than trusted from `state["messages"]`. See the `starting_new_turn`
    branch in `persona_node` for why this exists at all: every adapter
    passes a fresh `messages: []` on every external turn, which would
    otherwise wipe cross-turn memory entirely.

    `message.text` empty is skipped (an event that projected with no real
    text — shouldn't normally happen for `/message`-suffixed events, but
    an empty message would be actively wrong context, not just useless).
    A message from a DIFFERENT persona than the one about to respond (only
    possible in a group conversation) is still given `role: "user"` — from
    this persona's point of view another persona's message is exactly
    like another voice in the room, not this persona's own prior output —
    but prefixed with who actually said it, so the model isn't misled
    into thinking the human said it.
    """
    chat_messages: list[dict] = []
    for message in derive_messages(conversation_id):
        if not message.text:
            continue
        if message.actor == persona.id:
            chat_messages.append({"role": "assistant", "content": message.text})
        elif message.actor == "you":
            chat_messages.append({"role": "user", "content": message.text})
        else:
            chat_messages.append({"role": "user", "content": f"{message.actor}: {message.text}"})
    return chat_messages


# ---------------------------------------------------------------------------
# persona_node
# ---------------------------------------------------------------------------


async def persona_node(state: TapestryGraphState) -> dict:
    conversation_id = state["conversation_id"]
    turn_count = state.get("turn_count", 0)

    # persona lookup moved ahead of the turn-cap check (previously first)
    # specifically so persona.max_turns can override the global default —
    # _get_persona is a pure in-memory dict lookup, no side effects, so
    # reordering it earlier changes nothing about when anything actually
    # happens.
    persona = _get_persona(state["persona_id"])

    # Turn cap FIRST among anything with a side effect, before any other
    # work -- per the task brief's own placement instruction ("turn cap in
    # the persona node before proceeding").
    budgets.check_turn_budget(turn_count, max_turns=persona.max_turns or budgets.DEFAULT_MAX_TURNS)

    mode = resolve_mode(conversation_id, persona)
    effective_model, consumed_once = resolve_model(state, conversation_id, persona)

    # Every return path below must clear a consumed once-scope override
    # (spec §2.2: it applies to exactly this one persona_node pass, never
    # persisted history) — one choke point rather than repeating the same
    # conditional key at every return statement.
    def _finish(result: dict) -> dict:
        if consumed_once:
            return {**result, "model_override_once": None}
        return result

    starting_new_turn = state.get("turn_id") is None
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
    effective_tools = _effective_tools(persona.tools, mode)
    tool_schemas = _build_tool_schemas(effective_tools)

    if starting_new_turn:
        # Every adapter (web/Discord/Telegram) invokes the graph with a
        # FRESH new_state() — `messages: []` — for every external turn,
        # including the second, third, ... message of an ongoing
        # conversation, not just the first. Passed as `ainvoke` input
        # against an existing checkpointed thread, that `messages: []`
        # REPLACES the channel (confirmed empirically: `state["messages"]`
        # genuinely comes back empty on a second turn, not merged) --
        # without this branch, a persona would have zero memory of
        # anything before the current turn. So at the start of a new
        # external turn (turn_id was None), cross-turn history is rebuilt
        # from the event log instead of trusted from `state["messages"]`.
        # WITHIN a turn (the propose -> execute -> observe tool-call loop,
        # possibly spanning a delegation hand-off to another persona) this
        # branch never runs again -- turn_id is already set, so
        # `state["messages"]` keeps accumulating in memory exactly as it
        # already correctly does today.
        history = _chat_messages_from_log(conversation_id, persona)
    else:
        history = list(state.get("messages", []))
    messages = [{"role": "system", "content": system_prompt}] + history

    streaming.emit(
        "persona/thinking", {"persona_id": persona.id, "conversation_id": conversation_id}
    )
    response = await call_model(
        model=effective_model,
        messages=messages,
        tools=tool_schemas,
        fallback_models=persona.fallback_models,
    )
    streaming.emit(
        "persona/responded", {"persona_id": persona.id, "conversation_id": conversation_id}
    )

    if response.model_used != effective_model:
        # A fallback candidate answered instead of the requested model —
        # see litellm_client.call_model's docstring for exactly when this
        # happens. Logged so a human reviewing the conversation can see it,
        # not just infer it from which model's name shows up in `raw`.
        events.append_event(
            conversation_id,
            "model/fallback",
            actor=persona.id,
            payload=_with_thread(
                state,
                {"from_model": effective_model, "to_model": response.model_used, "reason": "primary_exhausted"},
            ),
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
        return _finish({"messages": new_messages, "turn_id": turn_id, "next_node": "end"})

    function = tool_call.get("function", {}) if isinstance(tool_call, dict) else {}
    tool_name = function.get("name", "")
    arguments = _parse_tool_arguments(function.get("arguments"))
    new_messages = messages[1:] + [
        {"role": "assistant", "content": response.text, "tool_calls": [tool_call]}
    ]

    if tool_name == DELEGATE_TOOL_NAME:
        return _finish(await _handle_delegate(state, persona, turn_id, arguments, new_messages))

    if tool_name == TASK_COMPLETE_TOOL_NAME:
        return _finish(
            await _handle_task_complete(state, persona, turn_id, arguments, new_messages)
        )

    if tool_name not in TOOL_REGISTRY or (
        tool_name != SKILL_LOADER_TOOL_NAME and tool_name not in effective_tools
    ):
        feedback = {
            "role": "tool",
            "tool_call_id": tool_call.get("id", ""),
            "content": f"Tool {tool_name!r} is not permitted for persona {persona.id!r}.",
        }
        return _finish(
            {
                "messages": new_messages + [feedback],
                "turn_id": turn_id,
                "turn_count": turn_count + 1,
                "next_node": "persona",
            }
        )

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
        tier = TOOL_RISK_TIER.get(tool_name, "mutate")
        needs_approval = await _mode_requires_approval(mode, tier, persona, tool_name, arguments)
        if needs_approval:
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
            # Mode bypassed a would-otherwise-be-gated tool call. Logged
            # (never silent) so the activity feed / conversation view can
            # show WHY a human was never asked — see spec §4.
            events.append_event(
                conversation_id,
                "mode/auto_approved",
                actor=persona.id,
                payload=_with_thread(
                    state, {"tool_name": tool_name, "mode": mode, "task_id": task_id}
                ),
            )
            next_node = "execute"
    else:
        next_node = "execute"

    return _finish(
        {
            "messages": new_messages,
            "pending_tool_call": pending_tool_call,
            "task_id": task_id,
            "turn_id": turn_id,
            "next_node": next_node,
        }
    )


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
        budgets.check_delegation_depth(
            delegation_depth,
            max_depth=persona.max_delegation_depth or budgets.DEFAULT_MAX_DELEGATION_DEPTH,
        )
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
        # Real, structured diff data via `git diff` — see
        # graph/diff_capture.py's module docstring for why this is scoped
        # to the whole workspace rather than reconstructed from `arguments`.
        # `None` (not a git repo, `git` missing, nothing uncommitted) falls
        # back to the old best-effort shape rather than emitting a
        # `task/diff_ready` with an empty files list — a tool call that
        # DID produce output should still leave the frontend something to
        # show, even in an environment where real diff capture can't run.
        workspace_diff = await capture_workspace_diff(_workspace_root())
        if workspace_diff is not None:
            diff_payload = {
                "task_id": task_id,
                "files_changed": [f.name for f in workspace_diff.files],
                "diff_summary": result.text[:2000],
                "additions": workspace_diff.additions,
                "deletions": workspace_diff.deletions,
                "truncated": workspace_diff.truncated,
                "files": [f.model_dump(by_alias=False) for f in workspace_diff.files],
            }
        else:
            diff_payload = {
                "task_id": task_id,
                "files_changed": [arguments["path"]] if "path" in arguments else [],
                "diff_summary": result.text[:2000],
                "additions": None,
                "deletions": None,
                "truncated": False,
                "files": [],
            }
        events.append_event(
            conversation_id,
            "task/diff_ready",
            actor=persona.id,
            payload=_with_thread(state, diff_payload),
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
