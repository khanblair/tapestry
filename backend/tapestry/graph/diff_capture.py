"""Real, structured diff data for `task/diff_ready` events — via `git diff`.

Before this module existed, `graph/build.py`'s `execute_node` put
`result.text[:2000]` (a tool's own prose return value, truncated) into
`task/diff_ready`'s payload as `diff_summary`, and `files_changed` was, at
best, the single path a `file_editor` call's own arguments happened to
carry. Neither is structured diff data: no per-file added/removed line
counts, no hunks, nothing `web/components/diff/DiffViewer.tsx` (or any
other consumer wanting a real "N files changed, +142 -8, here are the
lines" view) could render without inventing numbers.

This module gets that data from the one place it actually exists — the
git working tree itself — rather than trying to synthesize it from a
tool's return text. `capture_workspace_diff` shells out to `git diff`
scoped to the whole workspace (not just the path a single tool call
happened to touch): after a `file_editor`/`git` tool call, "what should a
human review" is honestly "everything currently uncommitted in this
workspace," not a guess reconstructed from one call's arguments — the
same notion of "diff" a `git diff`/`git status` run by a human would show.

Never raises on a missing/non-git workspace or a missing `git` binary —
diff capture is enrichment layered onto an already-succeeded tool call,
not something that call's success should depend on. Callers get `None`
back and fall back to whatever summary they already had.
"""

from __future__ import annotations

import asyncio
import re

from pydantic import BaseModel

# Bounds on how much a single task/diff_ready payload can grow to. Not
# arbitrary: an event log row holds this in `payload_json` forever (events
# are append-only, never trimmed), and the whole point of capturing real
# diff content is defeated if one huge generated-file diff makes every
# future read of this conversation's history slow to deserialize.
_MAX_FILES = 20
_MAX_LINES_PER_FILE = 500

_HUNK_HEADER_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@")


class DiffLine(BaseModel):
    type: str  # "add" | "del" | "ctx"
    line_number: int
    content: str


class DiffFileDetail(BaseModel):
    name: str
    additions: int
    deletions: int
    lines: list[DiffLine]


class WorkspaceDiff(BaseModel):
    files: list[DiffFileDetail]
    additions: int
    deletions: int
    truncated: bool = False


async def _run_git(workspace_root: str, *args: str) -> str | None:
    """Run `git <args>` in `workspace_root`; `None` on any failure
    (not a repo, `git` missing, non-zero exit) rather than raising.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            workspace_root,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _stderr = await proc.communicate()
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    return stdout.decode("utf-8", errors="replace")


def parse_unified_diff(diff_text: str) -> list[DiffLine]:
    """Parse one file's `git diff` unified-diff body into `DiffLine`s.

    Line numbering follows standard unified-diff convention: a context
    line and an added line are numbered on the NEW side (incrementing the
    new-side counter), a deleted line on the OLD side (incrementing the
    old-side counter) — matching how a side-by-side/inline diff viewer
    conventionally numbers lines a reader would actually navigate to.
    """
    lines: list[DiffLine] = []
    old_line = new_line = 0
    in_hunk = False
    for raw_line in diff_text.splitlines():
        header_match = _HUNK_HEADER_RE.match(raw_line)
        if header_match:
            old_line = int(header_match.group("old_start"))
            new_line = int(header_match.group("new_start"))
            in_hunk = True
            continue
        # Everything before the first hunk header (`diff --git`, `index`,
        # `--- a/...`, `+++ b/...`) is file-level metadata, not diff
        # content — critically, `--- a/x` and `+++ b/x` themselves start
        # with `-`/`+`, so without this gate they'd be misread as real
        # deleted/added lines.
        if not in_hunk or not raw_line:
            continue
        marker, content = raw_line[0], raw_line[1:]
        if marker == "+":
            lines.append(DiffLine(type="add", line_number=new_line, content=content))
            new_line += 1
        elif marker == "-":
            lines.append(DiffLine(type="del", line_number=old_line, content=content))
            old_line += 1
        elif marker == " ":
            lines.append(DiffLine(type="ctx", line_number=new_line, content=content))
            old_line += 1
            new_line += 1
        # Any other leading character (`\` for "No newline at end of
        # file") carries no line content — skipped.
        if len(lines) >= _MAX_LINES_PER_FILE:
            break
    return lines


def _parse_numstat(numstat_text: str) -> list[tuple[str, int, int]]:
    """Parse `git diff --numstat` output into `(path, additions, deletions)`
    triples. A binary file reports `-\t-\tpath` — recorded as `(path, 0, 0)`
    rather than dropped, so a binary change still shows up in the file
    list (with an empty line-level diff), instead of vanishing silently.
    """
    out: list[tuple[str, int, int]] = []
    for line in numstat_text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added_raw, deleted_raw, path = parts
        added = int(added_raw) if added_raw.isdigit() else 0
        deleted = int(deleted_raw) if deleted_raw.isdigit() else 0
        out.append((path, added, deleted))
    return out


async def capture_workspace_diff(workspace_root: str) -> WorkspaceDiff | None:
    """Real, structured diff of everything currently uncommitted in
    `workspace_root`, via `git diff --numstat` + one `git diff -- <path>`
    per changed file (capped at `_MAX_FILES`). Returns `None` if
    `workspace_root` isn't inside a git repo, `git` isn't on PATH, or
    there's simply nothing uncommitted.

    Diffed against `HEAD`, not a bare `git diff` — a tool call may or may
    not `git add` what it touched (the `file_editor` tool never stages
    anything; the metamcp-routed `git` tool might), and "everything
    different from the last commit" is the honest notion of "what should a
    human review" regardless of staging state. One known, accepted
    boundary: a brand new file that was created but never `git add`-ed is
    untracked, and `git diff` — against HEAD or otherwise — never reports
    untracked files (that's `git status`'s job, not `git diff`'s). Closing
    that gap would mean this function itself staging files as a side
    effect of merely trying to observe the diff, which is a bigger and
    more surprising behavior change than "capture what's there."
    """
    numstat = await _run_git(workspace_root, "diff", "HEAD", "--numstat")
    if numstat is None:
        return None
    changed = _parse_numstat(numstat)
    if not changed:
        return None

    truncated = len(changed) > _MAX_FILES
    files: list[DiffFileDetail] = []
    for path, additions, deletions in changed[:_MAX_FILES]:
        diff_text = await _run_git(workspace_root, "diff", "HEAD", "--", path)
        lines = parse_unified_diff(diff_text) if diff_text else []
        files.append(
            DiffFileDetail(name=path, additions=additions, deletions=deletions, lines=lines)
        )

    return WorkspaceDiff(
        files=files,
        additions=sum(a for _, a, _ in changed),
        deletions=sum(d for _, _, d in changed),
        truncated=truncated,
    )
