"""Tests for tapestry.skills.registry — rank-ordered discovery and precedence.

Covers: discovery across tiers, precedence-by-rank on name collisions, the
v1 project==bundled simplification, repo-root walk-up, and load_body's
not-found / invocation-gating behavior.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tapestry.skills import registry as registry_module
from tapestry.skills.registry import (
    RANK_BUNDLED,
    RANK_PROJECT,
    RANK_USER,
    SkillNotFoundError,
    SkillRegistry,
)


def _write_skill(
    root: Path,
    name: str,
    description: str,
    *,
    body: str = "body\n",
    flat: bool = False,
    extra_frontmatter: str = "",
) -> Path:
    frontmatter = f"name: {name}\ndescription: {description}\n{extra_frontmatter}"
    content = f"---\n{frontmatter}---\n{body}"
    if flat:
        path = root / f"{name}.md"
    else:
        path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_repo(tmp_path: Path, subdir: str = "repo") -> Path:
    repo_root = tmp_path / subdir
    (repo_root / ".git").mkdir(parents=True)
    return repo_root


# --- basic discovery -------------------------------------------------------


def test_discover_finds_project_tier_directory_bundle(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    _write_skill(repo_root / "skills", "foo-skill", "Does foo things.")

    reg = SkillRegistry(start_dir=repo_root, user_skills_dir=tmp_path / "no-such-user-dir")

    summaries = reg.discover()

    assert len(summaries) == 1
    assert summaries[0].name == "foo-skill"
    assert summaries[0].description == "Does foo things."
    assert summaries[0].rank == RANK_PROJECT


def test_discover_finds_flat_file_form(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    _write_skill(repo_root / "skills", "flat-skill", "A flat skill.", flat=True)

    reg = SkillRegistry(start_dir=repo_root, user_skills_dir=tmp_path / "no-such-user-dir")

    summaries = reg.discover()

    assert [s.name for s in summaries] == ["flat-skill"]


def test_discover_never_returns_bodies(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    _write_skill(repo_root / "skills", "foo-skill", "Desc.", body="SECRET BODY CONTENT\n")

    reg = SkillRegistry(start_dir=repo_root, user_skills_dir=tmp_path / "no-such-user-dir")

    summaries = reg.discover()

    for summary in summaries:
        assert not hasattr(summary, "body")
    assert "SECRET BODY CONTENT" not in str(summaries[0].model_dump())


def test_discover_sorts_summaries_by_name(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    _write_skill(repo_root / "skills", "zebra", "Z.")
    _write_skill(repo_root / "skills", "alpha", "A.")
    _write_skill(repo_root / "skills", "mid", "M.")

    reg = SkillRegistry(start_dir=repo_root, user_skills_dir=tmp_path / "no-such-user-dir")

    names = [s.name for s in reg.discover()]

    assert names == ["alpha", "mid", "zebra"]


# --- multi-tier discovery and precedence-by-rank ----------------------------


def test_discover_merges_all_three_tiers_with_correct_ranks(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    user_dir = tmp_path / "user-skills"
    bundled_dir = tmp_path / "bundled-skills"

    _write_skill(repo_root / "skills", "project-only", "Project tier skill.")
    _write_skill(user_dir, "user-only", "User tier skill.")
    _write_skill(bundled_dir, "bundled-only", "Bundled tier skill.")

    reg = SkillRegistry(start_dir=repo_root, user_skills_dir=user_dir, bundled_skills_dir=bundled_dir)

    by_name = {s.name: s for s in reg.discover()}

    assert by_name["project-only"].rank == RANK_PROJECT
    assert by_name["user-only"].rank == RANK_USER
    assert by_name["bundled-only"].rank == RANK_BUNDLED
    assert set(by_name) == {"project-only", "user-only", "bundled-only"}


def test_collision_project_beats_user_and_bundled(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    user_dir = tmp_path / "user-skills"
    bundled_dir = tmp_path / "bundled-skills"

    _write_skill(repo_root / "skills", "shared", "Project version.")
    _write_skill(user_dir, "shared", "User version.")
    _write_skill(bundled_dir, "shared", "Bundled version.")

    reg = SkillRegistry(start_dir=repo_root, user_skills_dir=user_dir, bundled_skills_dir=bundled_dir)

    summaries = reg.discover()

    assert len(summaries) == 1
    assert summaries[0].description == "Project version."
    assert summaries[0].rank == RANK_PROJECT


def test_collision_user_beats_bundled(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    user_dir = tmp_path / "user-skills"
    bundled_dir = tmp_path / "bundled-skills"

    # No project-tier version this time — only user and bundled collide.
    _write_skill(user_dir, "shared", "User version.")
    _write_skill(bundled_dir, "shared", "Bundled version.")

    reg = SkillRegistry(start_dir=repo_root, user_skills_dir=user_dir, bundled_skills_dir=bundled_dir)

    summaries = reg.discover()

    assert len(summaries) == 1
    assert summaries[0].description == "User version."
    assert summaries[0].rank == RANK_USER


def test_v1_simplification_bundled_defaults_to_project_directory(tmp_path: Path) -> None:
    """The documented v1 judgment call: with no explicit bundled_skills_dir
    override, rank 600 (bundled) resolves to the SAME physical directory as
    rank 100 (project) — so a skill placed there is discovered once, and any
    winner is reported at the project rank (100), never the bundled rank,
    since project's lower rank number always wins an identical-content tie.
    """
    repo_root = _make_repo(tmp_path)
    _write_skill(repo_root / "skills", "only-skill", "Same dir either way.")

    reg = SkillRegistry(start_dir=repo_root, user_skills_dir=tmp_path / "no-such-user-dir")

    summaries = reg.discover()

    assert len(summaries) == 1
    assert summaries[0].rank == RANK_PROJECT


# --- repo-root walk-up -------------------------------------------------------


def test_repo_root_found_by_walking_up_from_nested_directory(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    _write_skill(repo_root / "skills", "foo-skill", "Found via walk-up.")
    nested_start = repo_root / "some" / "deeply" / "nested" / "cwd"

    reg = SkillRegistry(start_dir=nested_start, user_skills_dir=tmp_path / "no-such-user-dir")

    names = [s.name for s in reg.discover()]

    assert names == ["foo-skill"]


def test_no_repo_root_only_user_tier_is_scanned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Force "no repo root found" deterministically, rather than relying on
    # the ambient filesystem's real ancestry having no .git anywhere above
    # tmp_path (which is usually but not guaranteedly true).
    monkeypatch.setattr(registry_module, "_find_repo_root", lambda start: None)

    user_dir = tmp_path / "user-skills"
    _write_skill(user_dir, "user-only", "Only reachable tier.")

    reg = SkillRegistry(start_dir=tmp_path, user_skills_dir=user_dir)

    summaries = reg.discover()

    assert [s.name for s in summaries] == ["user-only"]
    assert summaries[0].rank == RANK_USER


def test_no_repo_root_and_no_bundled_override_yields_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(registry_module, "_find_repo_root", lambda start: None)

    reg = SkillRegistry(start_dir=tmp_path, user_skills_dir=tmp_path / "no-such-user-dir")

    assert reg.discover() == []


# --- load_body ---------------------------------------------------------------


def test_load_body_returns_winning_tiers_body(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    user_dir = tmp_path / "user-skills"

    _write_skill(repo_root / "skills", "shared", "Project.", body="PROJECT BODY\n")
    _write_skill(user_dir, "shared", "User.", body="USER BODY\n")

    reg = SkillRegistry(start_dir=repo_root, user_skills_dir=user_dir)

    assert reg.load_body("shared") == "PROJECT BODY\n"


def test_load_body_raises_not_found_for_unknown_name(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)

    reg = SkillRegistry(start_dir=repo_root, user_skills_dir=tmp_path / "no-such-user-dir")

    with pytest.raises(SkillNotFoundError):
        reg.load_body("does-not-exist")


def test_load_body_disabled_skill_requires_bypass(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    _write_skill(
        repo_root / "skills",
        "gated-skill",
        "Not model-invocable.",
        body="GATED BODY\n",
        extra_frontmatter="disable-model-invocation: true\n",
    )

    reg = SkillRegistry(start_dir=repo_root, user_skills_dir=tmp_path / "no-such-user-dir")

    with pytest.raises(SkillNotFoundError):
        reg.load_body("gated-skill")

    # The explicit human /skill-name gesture path bypasses the gate.
    assert reg.load_body("gated-skill", bypass_invocation_check=True) == "GATED BODY\n"


def test_load_body_non_gated_skill_ignores_bypass_flag(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    _write_skill(repo_root / "skills", "open-skill", "Fine either way.", body="OPEN BODY\n")

    reg = SkillRegistry(start_dir=repo_root, user_skills_dir=tmp_path / "no-such-user-dir")

    assert reg.load_body("open-skill") == "OPEN BODY\n"
    assert reg.load_body("open-skill", bypass_invocation_check=True) == "OPEN BODY\n"
