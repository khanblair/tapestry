"""Tests for tapestry.skills.loader — SKILL.md / <name>.md frontmatter parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from tapestry.skills.loader import (
    InvalidSkillNameError,
    LegacySkillKeyError,
    SkillDefinition,
    parse_skill_file,
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --- happy paths -------------------------------------------------------


def test_parses_directory_bundle_form(tmp_path: Path) -> None:
    skill_path = _write(
        tmp_path / "my-skill" / "SKILL.md",
        """---
name: my-skill
description: Does a thing.
whenToUse: When you need the thing done.
disable-model-invocation: false
user-invocable: true
---
# My Skill

Step one. Step two.
""",
    )

    definition = parse_skill_file(str(skill_path))

    assert isinstance(definition, SkillDefinition)
    assert definition.name == "my-skill"
    assert definition.description == "Does a thing."
    assert definition.when_to_use == "When you need the thing done."
    assert definition.disable_model_invocation is False
    assert definition.user_invocable is True
    assert definition.body == "# My Skill\n\nStep one. Step two.\n"
    assert definition.source_path == str(skill_path)


def test_parses_flat_file_form(tmp_path: Path) -> None:
    skill_path = _write(
        tmp_path / "flat-skill.md",
        """---
name: flat-skill
description: A flat-file skill.
---
Body content here.
""",
    )

    definition = parse_skill_file(str(skill_path))

    assert definition.name == "flat-skill"
    assert definition.description == "A flat-file skill."
    assert definition.body == "Body content here.\n"


def test_defaults_when_optional_fields_absent(tmp_path: Path) -> None:
    skill_path = _write(
        tmp_path / "minimal.md",
        """---
name: minimal
description: Bare minimum frontmatter.
---
body
""",
    )

    definition = parse_skill_file(str(skill_path))

    assert definition.when_to_use is None
    assert definition.disable_model_invocation is False
    assert definition.user_invocable is True


def test_body_preserves_markdown_horizontal_rules(tmp_path: Path) -> None:
    # A '---' inside the body (used as a markdown <hr>) must NOT be mistaken
    # for a second frontmatter delimiter.
    skill_path = _write(
        tmp_path / "with-hr.md",
        """---
name: with-hr
description: Has a horizontal rule in the body.
---
Intro.

---

More content after the rule.
""",
    )

    definition = parse_skill_file(str(skill_path))

    assert "Intro." in definition.body
    assert "---" in definition.body
    assert "More content after the rule." in definition.body


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("Yes", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("No", False),
    ],
)
def test_bool_ish_string_coercion(tmp_path: Path, raw_value: str, expected: bool) -> None:
    skill_path = _write(
        tmp_path / "bool-ish.md",
        f"""---
name: bool-ish
description: Exercises boolean-ish coercion.
disable-model-invocation: "{raw_value}"
---
body
""",
    )

    definition = parse_skill_file(str(skill_path))

    assert definition.disable_model_invocation is expected


def test_bool_ish_yaml_native_bool_and_yes_no_tokens(tmp_path: Path) -> None:
    # Unquoted yes/no/true/false are resolved to real bools by PyYAML itself
    # (YAML 1.1 bool resolution) before our own coercion ever sees them.
    skill_path = _write(
        tmp_path / "native-bool.md",
        """---
name: native-bool
description: Exercises native YAML bool resolution.
disable-model-invocation: yes
user-invocable: no
---
body
""",
    )

    definition = parse_skill_file(str(skill_path))

    assert definition.disable_model_invocation is True
    assert definition.user_invocable is False


# --- error paths ---------------------------------------------------------


def test_invalid_kebab_case_name_raises(tmp_path: Path) -> None:
    skill_path = _write(
        tmp_path / "bad.md",
        """---
name: NotKebabCase
description: Invalid name.
---
body
""",
    )

    with pytest.raises(InvalidSkillNameError):
        parse_skill_file(str(skill_path))


@pytest.mark.parametrize(
    "bad_name",
    ["Has-Upper", "trailing-", "-leading", "double--hyphen", "has_underscore", "has space"],
)
def test_various_invalid_names_all_rejected(tmp_path: Path, bad_name: str) -> None:
    skill_path = _write(
        tmp_path / "bad-name.md",
        f"""---
name: {bad_name!r}
description: Invalid name variant.
---
body
""",
    )

    with pytest.raises(InvalidSkillNameError):
        parse_skill_file(str(skill_path))


def test_legacy_disable_model_invocation_key_raises(tmp_path: Path) -> None:
    skill_path = _write(
        tmp_path / "legacy.md",
        """---
name: legacy-skill
description: Uses the deprecated camelCase key.
disableModelInvocation: true
---
body
""",
    )

    with pytest.raises(LegacySkillKeyError, match="disable-model-invocation"):
        parse_skill_file(str(skill_path))


def test_legacy_user_invocable_key_raises(tmp_path: Path) -> None:
    skill_path = _write(
        tmp_path / "legacy2.md",
        """---
name: legacy-skill-2
description: Uses the other deprecated camelCase key.
userInvocable: false
---
body
""",
    )

    with pytest.raises(LegacySkillKeyError, match="user-invocable"):
        parse_skill_file(str(skill_path))


def test_missing_name_raises_value_error(tmp_path: Path) -> None:
    skill_path = _write(
        tmp_path / "no-name.md",
        """---
description: No name field at all.
---
body
""",
    )

    with pytest.raises(ValueError):
        parse_skill_file(str(skill_path))


def test_missing_description_raises_value_error(tmp_path: Path) -> None:
    skill_path = _write(
        tmp_path / "no-description.md",
        """---
name: no-description
---
body
""",
    )

    with pytest.raises(ValueError):
        parse_skill_file(str(skill_path))


def test_missing_frontmatter_delimiter_raises(tmp_path: Path) -> None:
    skill_path = _write(tmp_path / "plain.md", "Just markdown, no frontmatter at all.\n")

    with pytest.raises(ValueError):
        parse_skill_file(str(skill_path))


def test_unclosed_frontmatter_raises(tmp_path: Path) -> None:
    skill_path = _write(
        tmp_path / "unclosed.md",
        """---
name: unclosed
description: Never closes the frontmatter block.
body without closing delimiter
""",
    )

    with pytest.raises(ValueError):
        parse_skill_file(str(skill_path))


def test_invalid_bool_ish_value_raises(tmp_path: Path) -> None:
    skill_path = _write(
        tmp_path / "bad-bool.md",
        """---
name: bad-bool
description: Has a nonsense boolean-ish value.
disable-model-invocation: "maybe"
---
body
""",
    )

    with pytest.raises(ValueError):
        parse_skill_file(str(skill_path))
