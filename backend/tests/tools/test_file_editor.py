"""Tests for tapestry.tools.file_editor.FileEditorTool.

These exercise the REAL openhands-tools FileEditorExecutor against a temp
directory (no mocking) — per ANALYSIS-openhands-tools.md, it was verified
to work completely standalone with no other OpenHands machinery. This
mirrors the exact verification steps the vendor-research agent ran by
hand: create a file, str_replace (including the uniqueness-check failure
case), undo, plus our own allowed_paths jail since that allow-list is the
actual security boundary this shim adds on top of the library.
"""

from __future__ import annotations

import os

from tapestry.tools.file_editor import FileEditorTool, ToolResult


def test_create_writes_a_new_file(tmp_path):
    tool = FileEditorTool(workspace_root=str(tmp_path))
    path = str(tmp_path / "hello.txt")

    result = tool.run("create", path, file_text="line1\nline2\nline3\n")

    assert isinstance(result, ToolResult)
    assert result.is_error is False
    assert os.path.exists(path)
    assert open(path).read() == "line1\nline2\nline3\n"


def test_str_replace_unique_match_succeeds(tmp_path):
    tool = FileEditorTool(workspace_root=str(tmp_path))
    path = str(tmp_path / "hello.txt")
    tool.run("create", path, file_text="line1\nline2\nline3\n")

    result = tool.run("str_replace", path, old_str="line2", new_str="line-two")

    assert result.is_error is False
    assert open(path).read() == "line1\nline-two\nline3\n"


def test_str_replace_ambiguous_match_fails(tmp_path):
    """old_str matching more than once must fail cleanly, not guess."""
    tool = FileEditorTool(workspace_root=str(tmp_path))
    path = str(tmp_path / "hello.txt")
    tool.run("create", path, file_text="line1\nline2\nline3\n")

    result = tool.run("str_replace", path, old_str="line", new_str="X")

    assert result.is_error is True
    assert "Multiple occurrences" in result.text
    # file must be untouched on a failed replace
    assert open(path).read() == "line1\nline2\nline3\n"


def test_str_replace_no_match_fails(tmp_path):
    tool = FileEditorTool(workspace_root=str(tmp_path))
    path = str(tmp_path / "hello.txt")
    tool.run("create", path, file_text="line1\nline2\nline3\n")

    result = tool.run("str_replace", path, old_str="does-not-exist", new_str="X")

    assert result.is_error is True
    assert open(path).read() == "line1\nline2\nline3\n"


def test_undo_reverts_last_edit(tmp_path):
    tool = FileEditorTool(workspace_root=str(tmp_path))
    path = str(tmp_path / "hello.txt")
    tool.run("create", path, file_text="line1\nline2\nline3\n")
    tool.run("str_replace", path, old_str="line2", new_str="line-two")
    assert open(path).read() == "line1\nline-two\nline3\n"

    result = tool.run("undo_edit", path)

    assert result.is_error is False
    assert open(path).read() == "line1\nline2\nline3\n"


def test_allowed_paths_blocks_files_not_on_the_allow_list(tmp_path):
    """Our own security boundary: the library ships no path sandboxing at
    all (see the module docstring), so allowed_paths is the only real
    restriction. Verify it actually blocks an out-of-allow-list path.
    """
    allowed_path = str(tmp_path / "allowed.txt")
    tool = FileEditorTool(workspace_root=str(tmp_path), allowed_paths=[allowed_path])

    disallowed_path = str(tmp_path / "not_allowed.txt")
    result = tool.run("create", disallowed_path, file_text="nope")

    assert result.is_error is True
    assert not os.path.exists(disallowed_path)


def test_allowed_paths_permits_files_on_the_allow_list(tmp_path):
    allowed_path = str(tmp_path / "allowed.txt")
    tool = FileEditorTool(workspace_root=str(tmp_path), allowed_paths=[allowed_path])

    result = tool.run("create", allowed_path, file_text="ok\n")

    assert result.is_error is False
    assert os.path.exists(allowed_path)


def test_view_reads_back_file_contents(tmp_path):
    tool = FileEditorTool(workspace_root=str(tmp_path))
    path = str(tmp_path / "hello.txt")
    tool.run("create", path, file_text="line1\nline2\n")

    result = tool.run("view", path)

    assert result.is_error is False
    assert "line1" in result.text
    assert "line2" in result.text
