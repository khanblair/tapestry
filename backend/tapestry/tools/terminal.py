"""Thin shim over ``openhands.tools.terminal.impl.TerminalExecutor``.

Verified live against ``openhands-sdk==1.44.1`` / ``openhands-tools==1.44.1``
(see ``docs/vendor-research/ANALYSIS-openhands-tools.md``): construct with
keyword args, call with a ``TerminalAction``, get a ``TerminalObservation``
back (``.text`` / ``.exit_code``). No ``Agent``/``Conversation``/``EventStream``
object is required.

``terminal_type="subprocess"`` is not a style preference — it is required.
If tmux is available on the host and ``terminal_type`` is left unset,
``TerminalExecutor.__init__`` spawns a **real tmux server/session**
immediately as a side effect of construction (``TmuxPanePool(...).initialize()``
per the verified research). That is a live background process this shim
does not want to be responsible for; forcing ``"subprocess"`` avoids
creating it in the first place.
"""

from __future__ import annotations

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.impl import TerminalExecutor

from tapestry.tools.file_editor import ToolResult

__all__ = ["ToolResult", "TerminalTool"]


class TerminalTool:
    """Shim over ``TerminalExecutor``, forced to the non-tmux backend."""

    def __init__(self, working_dir: str) -> None:
        self.working_dir = working_dir
        self._executor = TerminalExecutor(working_dir=working_dir, terminal_type="subprocess")

    def run(self, command: str, timeout: float | None = None) -> ToolResult:
        """Run one shell command and wrap the result.

        ``is_error`` is True whenever ``exit_code != 0`` — this includes
        the case where ``exit_code`` comes back ``None`` (e.g. a soft
        no-output timeout with no process exit yet), which we deliberately
        treat as an error rather than silently reporting success.
        """
        action = TerminalAction(command=command, timeout=timeout)
        observation = self._executor(action)
        return ToolResult(text=observation.text, is_error=observation.exit_code != 0)

    def close(self) -> None:
        """Tear down the underlying executor.

        Required, not optional cleanup: even with ``terminal_type="subprocess"``
        the executor owns live OS-level resources (the subprocess/pty
        backing the session); with the default tmux backend it owns an
        actual tmux server. Callers MUST call this when done with a
        ``TerminalTool`` instance — nothing else in this shim's lifecycle
        does it for you.
        """
        self._executor.close()
