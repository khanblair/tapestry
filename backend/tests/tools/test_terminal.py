"""Tests for tapestry.tools.terminal.TerminalTool.

Exercises the REAL openhands-tools TerminalExecutor with
terminal_type="subprocess" against a real subprocess command — per
ANALYSIS-openhands-tools.md, verified to work standalone with zero
Agent/Conversation/EventStream machinery. Every test must call
tool.close() (or use the fixture below) — per the module docstring,
close() is required cleanup, not optional.
"""

from __future__ import annotations

import os

import pytest

from tapestry.tools.terminal import TerminalTool
from tapestry.tools.file_editor import ToolResult


@pytest.fixture
def terminal(tmp_path):
    tool = TerminalTool(working_dir=str(tmp_path))
    yield tool
    tool.close()


def test_run_successful_command(terminal, tmp_path):
    result = terminal.run("echo hello_from_terminal_executor")

    assert isinstance(result, ToolResult)
    assert result.is_error is False
    assert "hello_from_terminal_executor" in result.text


def test_run_uses_working_dir(terminal, tmp_path):
    result = terminal.run("pwd")

    assert result.is_error is False
    # macOS may resolve tmp_path through a symlink (/private/tmp vs /tmp) —
    # compare resolved paths rather than raw string equality.
    assert os.path.realpath(str(tmp_path)) in os.path.realpath(result.text.strip())


def test_run_nonzero_exit_code_is_an_error(terminal):
    result = terminal.run("exit 1")

    assert result.is_error is True


def test_run_command_not_found_is_an_error(terminal):
    result = terminal.run("this_command_does_not_exist_anywhere")

    assert result.is_error is True


def test_close_can_be_called_and_is_idempotent_enough_to_not_raise(tmp_path):
    tool = TerminalTool(working_dir=str(tmp_path))
    tool.run("echo one_command_before_close")

    tool.close()  # required cleanup per the module docstring — must not raise
