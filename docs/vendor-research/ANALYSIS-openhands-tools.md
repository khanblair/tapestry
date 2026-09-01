# openhands-tools — Analysis

Repo: `github.com/OpenHands/software-agent-sdk`, cloned to `vendor-research/openhands-sdk`, commit `a9e0a8a1aab2164b46bae00a18157a343aaa94c9`.

## Summary

**Not tightly coupled — the tools are cleanly usable standalone.** This is a monorepo (`openhands-sdk`, `openhands-tools`, `openhands-workspace`, `openhands-agent-server` — no `openhands-aci` anywhere in it). `FileEditorTool`/`TerminalTool`/`TaskTrackerTool` all exist, plus 25 more tool classes.

The real, invokable logic (`FileEditorExecutor`, `TerminalExecutor`, and a bare `file_editor()` function) is plain Python: construct with keyword args, call with a pydantic `Action`, get a pydantic `Observation` back. **This was proven by actually installing `openhands-tools` and running it** — created a file, did a `str_replace` (including triggering its uniqueness-check failure), ran `undo_edit`, and ran a real shell command via `TerminalExecutor`, all with zero `Agent`/`Conversation`/`EventStream` objects. The `conversation` parameter every executor accepts is optional everywhere (`if conversation is not None: ...`), used only for secret masking.

The only genuine coupling is `FileEditorTool.create(conv_state)`/`TerminalTool.create(conv_state)` — that's the SDK's own Agent-registration convenience path, not the only way in; skip it and call the executors directly.

**Real costs, not coupling:**
1. Heavy transitive install — 186 packages including litellm, browser-use/Playwright, fastmcp, fakeredis, lmnr, even though we want 2 tools.
2. **Version pinning is mandatory** — `pip install openhands-tools` unpinned resolved a mismatched `openhands-sdk` and broke on import. Pinning both to `==1.44.1` fixed it.
3. **No path sandboxing exists** — `FileEditor` will happily edit `/etc/hosts`; we need our own jail.
4. `TerminalExecutor` spawns a real tmux server at construction unless you pass `terminal_type="subprocess"`.

License: MIT, confirmed by reading `LICENSE` directly.

## 1. Monorepo layout (confirmed from actual directory tree)

```
openhands-sdk/           → PyPI: openhands-sdk       (Agent, Conversation, LLM, Tool base classes, events)
openhands-tools/         → PyPI: openhands-tools     (all runtime tools — what we need)
openhands-workspace/     → PyPI: openhands-workspace (Docker/local/remote workspace backends)
openhands-agent-server/  → REST/WebSocket server wrapping the SDK
clients/typescript/      → TS client for the agent server
```

No `openhands-aci` package anywhere in this repo (`grep -ril "openhands-aci\|openhands_aci\|openhands\.aci"` across the whole clone → zero hits).

Full authoritative tool-class list (`grep -rn 'ToolDefinition\[' openhands-tools/openhands/tools/`, 28 classes across 13 families): `FileEditorTool` (file_editor), `TerminalTool` (terminal), `TaskTrackerTool` (task_tracker), `TaskTool`/`TaskToolSet` (task), `WorkflowTool`/`WorkflowToolSet` (workflow), `GlobTool`, `GrepTool`, `ApplyPatchTool` (GPT-5-style patches), `AskOracleTool`, `TomConsultTool`, nine `Browser*Tool` classes + `BrowserToolSet` (browser_use, needs Playwright), and Gemini-CLI-compatible `EditTool`/`ReadFileTool`/`WriteFileTool`/`ListDirectoryTool`.

## 2. FileEditorTool / FileEditorExecutor / file_editor() — signatures and return types

File: `openhands-tools/openhands/tools/file_editor/`

- **`file_editor()`** (`impl.py:79-101`) — plain module function, no class: `file_editor(command, path, file_text=None, view_range=None, old_str=None, new_str=None, insert_line=None) -> FileEditorObservation`. Lazily builds a global `FileEditor()` on first call.
- **`FileEditor`** (`editor.py:56-159`) — the real implementation. `__init__(workspace_root=None, max_file_size_mb=None)`; `__call__(*, command, path, file_text=None, view_range=None, old_str=None, new_str=None, insert_line=None) -> FileEditorObservation`.
- **`FileEditorExecutor`** (`impl.py:21-77`) — the `ToolExecutor` adapter: `__init__(workspace_root=None, allowed_edits_files=None)`; `__call__(action: FileEditorAction, conversation: LocalConversation | None = None) -> FileEditorObservation`. `conversation` is accepted only to satisfy the base-class signature and is never read (`# noqa: ARG002`).
- **`FileEditorTool`** (`definition.py:193-266`) — the `ToolDefinition` wrapper for OpenHands' own Agent. `.create(conv_state: ConversationState)` is the one method that genuinely needs an OpenHands object (reads `conv_state.workspace.working_dir`, `conv_state.agent.llm.vision_is_active()`). Not required if you bypass this wrapper.

`FileEditorAction`/`FileEditorObservation` (`definition.py:29-152`) are plain pydantic models (subclass `openhands.sdk.tool.Action`/`.Observation`). `Observation.text` / `.is_error` give you everything an LLM loop needs.

**Verified live** (installed `openhands-tools==1.44.1` + `openhands-sdk==1.44.1` into a clean Python 3.12 venv and ran it):

```
CREATE -> is_error=False  ("File created successfully at: .../hello.txt")
STR_REPLACE -> is_error=False (unique match replaced, snippet shown)
STR_REPLACE (bad old_str) -> is_error=True ("No replacement was performed, old_str `...` did not appear verbatim...")
UNDO -> is_error=False (file content correctly reverted)
```

No `Agent`, `Conversation`, or `EventStream` object was ever constructed.

## 3. TerminalTool / TerminalExecutor — signatures and return types

File: `openhands-tools/openhands/tools/terminal/`

```python
TerminalExecutor(
    working_dir: str, username=None, no_change_timeout_seconds=None,
    terminal_type: Literal["tmux","subprocess","powershell"]|None=None,
    shell_path=None, env=None, full_output_save_dir=None, max_panes=DEFAULT_MAX_PANES,
)
executor(action: TerminalAction, conversation: LocalConversation|None=None) -> TerminalObservation
```

`TerminalAction` fields: `command`, `is_input`, `timeout`, `reset`. `TerminalObservation` fields: `command`, `exit_code`, `timeout`, `metadata` (working dir, PS1 info), `.text`.

`conversation` is optional end-to-end — `_export_envs()`/`_mask_observation()` (`impl.py:308-357`) both wrap the only reads in `if conversation is not None: try/except`.

**Important side effect**: if tmux is available and `terminal_type` isn't forced to `"subprocess"`, `TerminalExecutor.__init__` spawns a real tmux server/session immediately (`TmuxPanePool(...).initialize()`, `impl.py:100-149`) — not inert state, and `executor.close()` must be called to clean it up. Passing `terminal_type="subprocess"` avoids this entirely.

**Verified live**: `TerminalExecutor(working_dir=..., terminal_type="subprocess")`, ran `echo hello_from_terminal_executor && pwd` → `exit_code=0`, correct stdout, no tmux, no Agent/Conversation.

## 4. Safety/correctness logic worth reusing (cited, not reproduced)

- **str_replace uniqueness check** (`editor.py:204-241`): regex-escapes `old_str`, finds all occurrences, fails cleanly on 0 or >1 matches (reporting line numbers), with a strip-and-retry fallback for incidental whitespace.
- **Atomic writes** (`editor.py:482-531`): write to a sibling temp file, `Path.replace()` into place; temp file unlinked on any failure — never a truncated file.
- **Encoding detection + UTF-8 fallback** (`editor.py:482-499`, `utils/encoding.py`) so adding non-ASCII content to a legacy-encoded file doesn't corrupt it.
- **Output truncation** (`utils/constants.py`, `maybe_truncate`) with a `<response clipped>` notice; terminal output can additionally spill to disk (`full_output_save_dir`).
- **Disk-backed, bounded undo history** (`utils/history.py`, `FileHistoryManager`, cap 10/file, `FileCache`-backed in a temp dir — not unbounded RAM).
- **Path/command validation** (`editor.py:626-671`): absolute-path requirement with a "did you mean...?" hint, `create`-on-existing / non-`create`-on-missing / non-`view`-on-directory rejections.
- **File-type/size guards** (`editor.py:696-731`): 10MB cap, binary-file rejection except recognized image types (returned as base64 for vision LLMs).
- **Terminal timeouts**: soft no-output timeout (30s default) plus a separate runtime-idle-aware cap on foreground command timeouts (`timeout_policy.py`).
- **Malformed-call detection** (`terminal/definition.py:53-79`): catches an LLM stuffing a Python/JSON literal into `command` and returns a corrective hint instead of a confusing shell error.
- **Secret masking/injection** (`terminal/impl.py:296-357`): optional, only active if you pass a real `conversation`.

**What's explicitly NOT provided — don't assume it:** no path sandboxing. `validate_path` only checks absoluteness/existence; `workspace_root` is used purely for a suggestion string. The only real restriction is the off-by-default `allowed_edits_files` allow-list on `FileEditorExecutor`. As shipped, it will edit anything the OS process can write to.

## 5. openhands-aci relationship

`openhands-aci` **is** a real, separately-published PyPI package (confirmed live: latest `0.3.3`) — but it belongs to the **older**, separate `OpenHands/OpenHands` monorepo (this repo's own `AGENTS.md:32` calls that repo out as the owner of "Agent Canvas UI... local-stack orchestration," a different product line). It is **not a dependency of `openhands-tools`** and has zero references in `software-agent-sdk`. This SDK reimplemented file-editing from scratch (`FileEditor`'s docstring cites Anthropic's own computer-use quickstart as its lineage, `editor.py:65`), not `openhands-aci`. Don't chase it.

## 6. License

Top-level `LICENSE`: **MIT**, "Copyright (c) 2026 OpenHands contributors" — standard MIT text, no restrictions. Only other LICENSE in the repo is `clients/typescript/LICENSE` (also MIT, cosmetically different boilerplate/year, applies only to the unrelated TS client). No subdirectory under `openhands-tools/`, `openhands-sdk/`, or `openhands-workspace/` carries its own license. Confirmed clean.

## 7. Package names / install footprint

Both live on PyPI at matching version **1.44.1** (verified via `pip index versions`). `openhands-tools`'s `pyproject.toml` hard-depends on `openhands-sdk` (unpinned) plus `tree-sitter`, `binaryornot`, `cachetools`, `libtmux`, `browser-use>=0.8.0` (→ Playwright), `func-timeout`, `tom-swe`. `openhands-sdk` itself pulls `litellm`, `fastmcp`, `fakeredis`, `httpx`, `jsonschema`, `lmnr`, `agent-client-protocol`, etc.

**Actually installed it**: `pip install openhands-tools` pulled **186 packages** total. **Critical finding from doing this for real**: an unpinned install resolved `openhands-sdk==1.20.0` alongside `openhands-tools==1.44.1`, and `import openhands.tools` immediately broke — `ImportError: cannot import name 'default_condenser' from openhands.sdk.context.condenser` — because the two packages' internal APIs had drifted apart. Explicitly pinning `openhands-sdk==1.44.1 openhands-tools==1.44.1` together fixed it completely and all smoke tests passed. **Always pin both packages to the identical version.**

## Recommendation

Depend on `openhands-tools==1.44.1` / `openhands-sdk==1.44.1` directly and call the executors, not `FileEditorTool.create()`/`TerminalTool.create()`:

```python
from openhands.tools.file_editor.impl import FileEditorExecutor
from openhands.tools.file_editor.definition import FileEditorAction

editor = FileEditorExecutor(workspace_root="/workspaces/session-123")
# no sandboxing built in — add allowed_edits_files=[...] or run per-session in a container

def run_file_edit(command, path, **kwargs):
    obs = editor(FileEditorAction(command=command, path=path, **kwargs))  # conversation=None
    if obs.is_error:
        raise ToolExecutionError(obs.text)
    return obs.text

from openhands.tools.terminal.impl import TerminalExecutor
from openhands.tools.terminal.definition import TerminalAction

terminal = TerminalExecutor(working_dir="/workspaces/session-123", terminal_type="subprocess")  # avoids tmux
def run_shell(command, timeout=None):
    return terminal(TerminalAction(command=command, timeout=timeout)).text
def close_session():
    terminal.close()  # required
```

Don't vendor the file — the logic (uniqueness matching, atomic writes, encoding fallback, undo history, tmux lifecycle) is exactly the tedious-to-reproduce correctness code we wanted to avoid writing ourselves, it's MIT, and it's what backs OpenHands Cloud/CLI in production. The costs are install weight (isolate to whichever service runs tools) and version-pinning discipline (proven necessary above) — not architectural coupling.
