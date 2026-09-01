# Phase 4 fast-follow, not v1 — see project_structure.md's Docker section.
#
# This is where FileEditorExecutor/TerminalExecutor tool calls get shelled into for real
# sandboxing, closing the gap openhands-tools ships with none of (verified in
# docs/vendor-research/ANALYSIS-openhands-tools.md: no path sandboxing exists in the library
# itself). Only the target worktree gets bind-mounted in — nothing else the host process can
# reach. The interim measure until this is built is the allow-list on FileEditorExecutor
# (backend/tapestry/tools/file_editor.py).
#
# Stubbed intentionally empty until Phase 4.

FROM python:3.12-slim
WORKDIR /workspace
