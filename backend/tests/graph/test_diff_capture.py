from __future__ import annotations

import subprocess

import pytest

from tapestry.graph.diff_capture import (
    DiffLine,
    capture_workspace_diff,
    parse_unified_diff,
)


def test_parse_unified_diff_classifies_add_del_ctx_lines():
    diff_text = (
        "diff --git a/x.py b/x.py\n"
        "index abc..def 100644\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,3 +1,4 @@\n"
        " def f():\n"
        "-    return 1\n"
        "+    return 2\n"
        "+    # comment\n"
        " \n"
    )

    lines = parse_unified_diff(diff_text)

    assert [line.type for line in lines] == ["ctx", "del", "add", "add", "ctx"]
    assert all(isinstance(line, DiffLine) for line in lines)


def test_parse_unified_diff_numbers_add_and_ctx_on_new_side_del_on_old_side():
    diff_text = "@@ -10,2 +10,2 @@\n" " ctx line\n" "-old line\n" "+new line\n"

    lines = parse_unified_diff(diff_text)

    ctx, deletion, addition = lines
    assert ctx.line_number == 10
    assert deletion.line_number == 11
    assert addition.line_number == 11


def test_parse_unified_diff_on_empty_text_returns_empty_list():
    assert parse_unified_diff("") == []


@pytest.fixture
def git_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "a.py").write_text("line1\nline2\nline3\n")
    subprocess.run(["git", "add", "a.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=root, check=True)
    return root


@pytest.mark.asyncio
async def test_capture_workspace_diff_returns_none_when_nothing_uncommitted(git_repo):
    result = await capture_workspace_diff(str(git_repo))
    assert result is None


@pytest.mark.asyncio
async def test_capture_workspace_diff_returns_none_for_non_git_directory(tmp_path):
    result = await capture_workspace_diff(str(tmp_path))
    assert result is None


@pytest.mark.asyncio
async def test_capture_workspace_diff_reflects_real_uncommitted_change(git_repo):
    (git_repo / "a.py").write_text("line1\nCHANGED\nline3\nline4\n")

    result = await capture_workspace_diff(str(git_repo))

    assert result is not None
    assert result.additions == 2
    assert result.deletions == 1
    assert len(result.files) == 1
    assert result.files[0].name == "a.py"
    assert any(line.content == "CHANGED" and line.type == "add" for line in result.files[0].lines)
    assert any(line.content == "line2" and line.type == "del" for line in result.files[0].lines)


@pytest.mark.asyncio
async def test_capture_workspace_diff_covers_every_changed_file(git_repo):
    (git_repo / "a.py").write_text("line1\nCHANGED\nline3\n")
    (git_repo / "b.py").write_text("new file\n")
    subprocess.run(["git", "add", "b.py"], cwd=git_repo, check=True)

    result = await capture_workspace_diff(str(git_repo))

    assert result is not None
    names = {f.name for f in result.files}
    assert names == {"a.py", "b.py"}
