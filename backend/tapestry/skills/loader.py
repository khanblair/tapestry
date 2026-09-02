"""Parses `SKILL.md` files (and flat `<name>.md` files) into `SkillDefinition`s.

Format verified against DeepSeek Harness's skill subsystem — see
`docs/vendor-research/ANALYSIS-deepseek-harness.md` §3. Adopted close to
verbatim: directory bundles (`<name>/SKILL.md`) or flat files (`<name>.md`),
`---`-delimited YAML frontmatter, kebab-case names enforced by regex, and a
deliberate fail-loud rejection of the legacy camelCase key spelling instead of
silently accepting a deprecated frontmatter shape.

This module only knows how to parse ONE file at a time, given its path.
Deciding *which* files exist, and in what rank order, is `registry.py`'s job.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

# Kebab-case: lowercase alphanumeric segments joined by single hyphens.
# No leading/trailing hyphen, no consecutive hyphens, no uppercase, no underscores.
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Legacy (deprecated) camelCase frontmatter keys -> their correct kebab-case
# spelling. Present as a deliberate DeepSeek Harness pattern: fail loud on the
# deprecated spelling rather than silently accepting it, so a stale skill file
# doesn't quietly lose its invocation-control settings.
_LEGACY_KEY_REPLACEMENTS = {
    "disableModelInvocation": "disable-model-invocation",
    "userInvocable": "user-invocable",
}

_TRUE_STRINGS = {"true", "1", "yes"}
_FALSE_STRINGS = {"false", "0", "no"}


class InvalidSkillNameError(Exception):
    """Raised when a skill's `name` frontmatter key is not kebab-case."""


class LegacySkillKeyError(Exception):
    """Raised when a skill file uses a deprecated camelCase frontmatter key
    (`disableModelInvocation` / `userInvocable`) instead of the current
    kebab-case spelling (`disable-model-invocation` / `user-invocable`).
    """


class SkillDefinition(BaseModel):
    """A fully-parsed skill: frontmatter metadata plus its markdown body."""

    name: str
    description: str
    when_to_use: str | None = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    body: str
    source_path: str


def _coerce_bool_ish(value: Any, *, field: str, path: str) -> bool:
    """Accept true/false/1/0/yes/no (as real bools, ints, or strings) for a
    boolean-ish frontmatter field. PyYAML's own YAML-1.1 bool resolution
    already turns bare `yes`/`no`/`true`/`false` into real Python bools before
    this runs; this also covers the quoted-string and 0/1-int spellings.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_STRINGS:
            return True
        if lowered in _FALSE_STRINGS:
            return False
    raise ValueError(f"{path}: invalid boolean-ish value for {field!r}: {value!r}")


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Split a `---`-delimited YAML frontmatter block from the markdown body.

    Only the FIRST closing `---` (on its own line) after the opening one is
    treated as the end of frontmatter — any later `---` lines are left alone
    in the body, since markdown uses that same token for horizontal rules.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL file must start with a '---' frontmatter delimiter")

    end_index = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_index = i
            break
    if end_index is None:
        raise ValueError("SKILL file frontmatter is never closed with a second '---'")

    frontmatter_text = "\n".join(lines[1:end_index])
    body = "\n".join(lines[end_index + 1 :]).lstrip("\n")
    return frontmatter_text, body


def parse_skill_file(path: str) -> SkillDefinition:
    """Read and parse one SKILL.md / <name>.md file into a SkillDefinition.

    Raises:
        LegacySkillKeyError: the frontmatter uses a deprecated camelCase key.
        InvalidSkillNameError: `name` is present but not kebab-case.
        ValueError: any other malformed-frontmatter condition (missing
            delimiters, missing required `name`/`description`, frontmatter
            that isn't a YAML mapping, an invalid boolean-ish value).
    """
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    frontmatter_text, body = _split_frontmatter(text)

    raw = yaml.safe_load(frontmatter_text)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: frontmatter must be a YAML mapping, got {type(raw).__name__}")

    for legacy_key, correct_key in _LEGACY_KEY_REPLACEMENTS.items():
        if legacy_key in raw:
            raise LegacySkillKeyError(
                f"{path}: frontmatter key {legacy_key!r} is deprecated — "
                f"use {correct_key!r} instead."
            )

    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError(f"{path}: frontmatter is missing required key 'name'")
    if not SKILL_NAME_PATTERN.match(name):
        raise InvalidSkillNameError(
            f"{path}: skill name {name!r} must be kebab-case "
            f"(match {SKILL_NAME_PATTERN.pattern!r})"
        )

    description = raw.get("description")
    if not isinstance(description, str) or not description:
        raise ValueError(f"{path}: frontmatter is missing required key 'description'")

    when_to_use = raw.get("whenToUse")
    if when_to_use is not None and not isinstance(when_to_use, str):
        raise ValueError(f"{path}: 'whenToUse' must be a string if present")

    disable_model_invocation = _coerce_bool_ish(
        raw.get("disable-model-invocation", False),
        field="disable-model-invocation",
        path=path,
    )
    user_invocable = _coerce_bool_ish(
        raw.get("user-invocable", True),
        field="user-invocable",
        path=path,
    )

    return SkillDefinition(
        name=name,
        description=description,
        when_to_use=when_to_use,
        disable_model_invocation=disable_model_invocation,
        user_invocable=user_invocable,
        body=body,
        source_path=str(file_path),
    )
