from __future__ import annotations

from pathlib import Path

import pytest

from tapestry.core.personas import Persona, load_personas

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_PERSONAS_DIR = REPO_ROOT / "personas"


def _write_persona_yaml(directory: Path, filename: str, content: str) -> None:
    (directory / filename).write_text(content, encoding="utf-8")


def test_load_personas_reads_every_yaml_file_in_directory(tmp_path):
    _write_persona_yaml(
        tmp_path,
        "ada.yaml",
        """
        name: Ada
        role: Architect
        model: claude-opus-4-6
        system_prompt: "plan before you code"
        tools: [file_editor_read]
        mcp_servers: []
        status: online
        color: "#3B82F6"
        """,
    )
    _write_persona_yaml(
        tmp_path,
        "rex.yaml",
        """
        name: Rex
        role: Developer
        model: deepseek/deepseek-chat
        system_prompt: "implement things"
        tools: [file_editor, terminal, git]
        mcp_servers: []
        status: online
        color: "#8B5CF6"
        """,
    )

    personas = load_personas(str(tmp_path))

    assert set(personas.keys()) == {"ada", "rex"}
    assert all(isinstance(p, Persona) for p in personas.values())
    assert personas["rex"].role == "Developer"


def test_load_personas_defaults_id_to_filename_stem(tmp_path):
    _write_persona_yaml(
        tmp_path,
        "vex.yaml",
        """
        name: Vex
        role: Security & QA
        model: claude-sonnet-5
        system_prompt: "review everything"
        tools: [terminal_read_only]
        mcp_servers: []
        status: online
        color: "#F43F5E"
        """,
    )

    personas = load_personas(str(tmp_path))

    assert personas["vex"].id == "vex"


def test_load_personas_respects_explicit_id_over_filename(tmp_path):
    _write_persona_yaml(
        tmp_path,
        "some-file.yaml",
        """
        id: custom-id
        name: Custom
        role: Tester
        model: claude-sonnet-5
        system_prompt: "test"
        tools: []
        mcp_servers: []
        status: offline
        color: "#000000"
        """,
    )

    personas = load_personas(str(tmp_path))

    assert list(personas.keys()) == ["custom-id"]


def test_load_personas_rejects_invalid_status(tmp_path):
    _write_persona_yaml(
        tmp_path,
        "broken.yaml",
        """
        name: Broken
        role: Nope
        model: claude-sonnet-5
        system_prompt: "x"
        tools: []
        mcp_servers: []
        status: not-a-real-status
        color: "#000000"
        """,
    )

    with pytest.raises(Exception):
        load_personas(str(tmp_path))


def test_load_personas_on_empty_directory_returns_empty_dict(tmp_path):
    assert load_personas(str(tmp_path)) == {}


def test_real_roster_files_parse_and_match_the_scoped_spec():
    personas = load_personas(str(REAL_PERSONAS_DIR))

    assert set(personas.keys()) == {"ada", "rex", "vex", "nova", "sage", "kai"}

    assert personas["ada"].role == "Architect"
    assert personas["ada"].status == "online"
    assert personas["ada"].color == "#3B82F6"

    assert personas["rex"].role == "Developer"
    assert personas["rex"].model == "deepseek/deepseek-chat"
    assert personas["rex"].color == "#8B5CF6"

    assert personas["vex"].role == "Security & QA"
    assert personas["vex"].color == "#F43F5E"

    assert personas["nova"].role == "DevOps"
    assert personas["nova"].status == "paused"
    assert personas["nova"].color == "#14B8A6"

    # Non-dev, general-chat personas -- no dev tools, bypass by default so
    # the (already-existing) lead-persona-only mode dropdown bug can't
    # leave them stuck asking for approval they'll never need.
    assert personas["sage"].tools == []
    assert personas["sage"].default_mode == "bypass"
    assert personas["sage"].proactive is True

    assert personas["kai"].tools == []
    assert personas["kai"].default_mode == "bypass"
    assert personas["kai"].proactive is False


def test_persona_proactive_defaults_false():
    # Every persona.yaml written before this field existed must still load
    # with the same behavior it always had: never initiating on its own.
    personas = load_personas(str(REAL_PERSONAS_DIR))
    assert personas["ada"].proactive is False
    assert personas["rex"].proactive is False
