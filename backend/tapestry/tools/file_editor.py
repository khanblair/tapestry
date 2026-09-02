"""Thin shim over ``openhands.tools.file_editor.impl.FileEditorExecutor``.

Verified live against ``openhands-sdk==1.44.1`` / ``openhands-tools==1.44.1``
(see ``docs/vendor-research/ANALYSIS-openhands-tools.md``): construct with
keyword args, call with a ``FileEditorAction``, get a ``FileEditorObservation``
back (``.text`` / ``.is_error``). No ``Agent``/``Conversation``/``EventStream``
object is required — ``FileEditorExecutor.__call__`` accepts ``conversation``
only to satisfy its base-class signature and never reads it when it is
``None``, so we always omit it.

SECURITY — READ THIS BEFORE WIRING THIS INTO ``graph/``
=========================================================
``openhands-tools`` ships **no path sandboxing whatsoever**. Its own
``validate_path`` only checks that a path is absolute and exists/doesn't
exist as appropriate for the command — ``workspace_root`` is used purely to
build a "did you mean...?" hint string, never to restrict what can be
touched. Left to its own devices, ``FileEditorExecutor`` will happily
``create``/``str_replace``/``undo_edit`` any file the OS process can write
to, e.g. ``/etc/hosts``, a sibling project, or ``~/.ssh/authorized_keys``.

The **only** real restriction the library offers is the off-by-default
``allowed_edits_files`` allow-list constructor arg. That allow-list is
this shim's whole security boundary until the Phase 4 Docker tool-runner
sandbox exists (see ``project_structure.md``'s ``docker/tool-runner/``
section) — every caller that lets a model choose ``path`` MUST pass
``allowed_paths`` explicitly. There is no default deny; an unset
``allowed_paths`` means "edit anything this process can write to."

Verified live: ``allowed_edits_files`` is matched against the exact
resolved path passed in each ``FileEditorAction`` (not a directory-prefix
jail) — a path not present verbatim in the list is rejected with
``is_error=True`` and a message naming the disallowed operation. Callers
that want to permit "anything under this directory" must expand the
allow-list to the specific file paths they intend to allow, not just pass
the directory itself.
"""

from __future__ import annotations

from pydantic import BaseModel

from openhands.tools.file_editor.definition import FileEditorAction
from openhands.tools.file_editor.impl import FileEditorExecutor


class ToolResult(BaseModel):
    """Uniform result shape shared by every shim in ``tapestry.tools``."""

    text: str
    is_error: bool


class FileEditorTool:
    """Shim over ``FileEditorExecutor`` with our own path allow-list.

    ``workspace_root`` is passed straight through to the underlying
    executor — per the verified research, it only affects hint text in
    error messages, it is NOT a sandbox boundary by itself.

    ``allowed_paths``, if given, is passed as ``allowed_edits_files`` to
    the underlying executor and is the actual enforcement point: any
    ``path`` not in this list is rejected. Pass the specific absolute
    file paths a caller is allowed to touch. Leaving this ``None``
    disables the allow-list entirely (matches the upstream default) —
    only do that for trusted, non-model-driven callers.
    """

    def __init__(self, workspace_root: str, allowed_paths: list[str] | None = None) -> None:
        self.workspace_root = workspace_root
        self.allowed_paths = allowed_paths
        self._executor = FileEditorExecutor(
            workspace_root=workspace_root,
            allowed_edits_files=allowed_paths,
        )

    def run(self, command: str, path: str, **kwargs: object) -> ToolResult:
        """Run one file-editor command (``create``, ``str_replace``, ``view``,
        ``insert``, ``undo_edit``, ...) and wrap the result.

        ``**kwargs`` forwards the rest of ``FileEditorAction``'s fields
        (``file_text``, ``old_str``, ``new_str``, ``view_range``,
        ``insert_line``) as needed for the given ``command``.
        """
        action = FileEditorAction(command=command, path=path, **kwargs)
        # `conversation` defaults to None and is safe to omit: the executor
        # only reads it (for secret masking) when it is not None — see
        # module docstring / ANALYSIS-openhands-tools.md.
        observation = self._executor(action)
        return ToolResult(text=observation.text, is_error=observation.is_error)
