"""Rank-ordered skill discovery.

Adapted from DeepSeek Harness's six-tier discovery table (see
`docs/vendor-research/ANALYSIS-deepseek-harness.md` §3) down to the three
tiers Tapestry actually needs for v1:

    rank 100  project  <repo_root>/skills/      (repo_root: nearest ancestor
                                                  of `start_dir` containing a
                                                  `.git` directory)
    rank 400  user     ~/.tapestry/skills/
    rank 600  bundled  the skills/ directory shipped with the Tapestry repo

Lower rank number = higher precedence (nearer/more specific beats bundled
default) — a name collision across tiers is resolved in favor of the lower
rank, matching the DeepSeek Harness precedence rule exactly.

*** v1 SIMPLIFICATION, documented deliberately ***
Rank 100 (project) and rank 600 (bundled) resolve to the SAME physical
directory by default: `<repo_root>/skills/`. That's because this repository
IS simultaneously "the project a persona is working in" *and* "the Tapestry
repo that ships bundled default skills" — v1 has no notion yet of Tapestry
being installed as a dependency into someone else's separate project, which
is the scenario where "project skills" and "bundled skills" would actually
live in two different places. Until that distinction exists, a collision
between the project and bundled tiers is impossible by construction (they're
the same file), and only the user tier (rank 400) can meaningfully shadow a
bundled skill. `bundled_skills_dir` is exposed as an explicit override on the
constructor specifically so that future distinction (and this module's own
tests) don't require changing the discovery algorithm — only the default
wiring.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel

from tapestry.skills.loader import SkillDefinition, parse_skill_file

RANK_PROJECT = 100
RANK_USER = 400
RANK_BUNDLED = 600

DEFAULT_USER_SKILLS_DIR = Path.home() / ".tapestry" / "skills"


class SkillNotFoundError(Exception):
    """Raised by `SkillRegistry.load_body` when no tier has a skill with the
    requested name, or when the winning definition is invocation-gated and
    the caller didn't pass the explicit human-gesture bypass.
    """


class SkillSummary(BaseModel):
    """Catalog entry: everything EXCEPT the body. This is what's cheap enough
    to keep in context all the time (see `catalog_sync.py`) — the full body
    only loads on demand via `SkillRegistry.load_body`.
    """

    name: str
    description: str
    when_to_use: str | None
    user_invocable: bool
    rank: int
    source: str


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from `start` looking for the nearest ancestor with a `.git`
    directory. Returns None if none is found (e.g. running outside any repo).
    """
    current = start.resolve()
    candidates = [current, *current.parents]
    for candidate in candidates:
        if (candidate / ".git").is_dir():
            return candidate
    return None


def _iter_skill_files(root: Path) -> Iterator[Path]:
    """Yield every SKILL.md / <name>.md path directly under `root`.

    Supports both forms per the DeepSeek Harness format: a directory bundle
    `<name>/SKILL.md`, or a flat `<name>.md` file sitting directly in `root`.
    """
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            skill_md = entry / "SKILL.md"
            if skill_md.is_file():
                yield skill_md
        elif entry.is_file() and entry.suffix == ".md":
            yield entry


class SkillRegistry:
    """Discovers skills across the three ranked tiers described above, and
    resolves a single winning definition per name (lower rank wins).

    `start_dir` controls where the walk-up-for-`.git` search begins when
    locating the project tier; it defaults to the current working directory.
    `user_skills_dir` and `bundled_skills_dir` are override hooks for tests
    and for the future point where the bundled tier stops being the same
    physical directory as the project tier (see module docstring) — leave
    them unset in production and you get the documented v1 defaults.
    """

    def __init__(
        self,
        start_dir: str | Path | None = None,
        *,
        user_skills_dir: str | Path | None = None,
        bundled_skills_dir: str | Path | None = None,
    ) -> None:
        self._start_dir = Path(start_dir) if start_dir is not None else Path.cwd()
        self._user_skills_dir = (
            Path(user_skills_dir) if user_skills_dir is not None else DEFAULT_USER_SKILLS_DIR
        )
        self._bundled_skills_dir_override = (
            Path(bundled_skills_dir) if bundled_skills_dir is not None else None
        )

    def _tiers(self) -> list[tuple[int, Path]]:
        """Ordered (rank, directory) pairs to scan. Order doesn't affect
        correctness (collision resolution is rank-based, not scan-order
        based) but is kept project -> bundled -> user for readability.
        """
        tiers: list[tuple[int, Path]] = []
        repo_root = _find_repo_root(self._start_dir)

        if repo_root is not None:
            project_dir = repo_root / "skills"
            tiers.append((RANK_PROJECT, project_dir))
            bundled_dir = (
                self._bundled_skills_dir_override
                if self._bundled_skills_dir_override is not None
                else project_dir  # v1 simplification — see module docstring
            )
            tiers.append((RANK_BUNDLED, bundled_dir))
        elif self._bundled_skills_dir_override is not None:
            # No repo root at all (e.g. running outside any git checkout):
            # there's no project tier, but an explicitly-configured bundled
            # dir still counts.
            tiers.append((RANK_BUNDLED, self._bundled_skills_dir_override))

        tiers.append((RANK_USER, self._user_skills_dir))
        return tiers

    def _resolve_winners(self) -> dict[str, tuple[SkillDefinition, int]]:
        """Parse every skill file in every tier and keep, per name, the
        definition with the lowest rank number (highest precedence).
        """
        winners: dict[str, tuple[SkillDefinition, int]] = {}
        for rank, tier_dir in self._tiers():
            for skill_path in _iter_skill_files(tier_dir):
                definition = parse_skill_file(str(skill_path))
                existing = winners.get(definition.name)
                if existing is None or rank < existing[1]:
                    winners[definition.name] = (definition, rank)
        return winners

    def discover(self) -> list[SkillSummary]:
        """Walk all tiers, parse every skill file found, and return
        catalog-only summaries (never bodies) for the winning definition of
        each name, sorted by name for determinism.
        """
        winners = self._resolve_winners()
        return [
            SkillSummary(
                name=definition.name,
                description=definition.description,
                when_to_use=definition.when_to_use,
                user_invocable=definition.user_invocable,
                rank=rank,
                source=definition.source_path,
            )
            for definition, rank in sorted(winners.values(), key=lambda pair: pair[0].name)
        ]

    def load_body(self, name: str, *, bypass_invocation_check: bool = False) -> str:
        """Re-locate and fully parse the winning definition for `name`, and
        return its body.

        Raises SkillNotFoundError if no tier has a skill with that name, or
        if the winning definition has `disable_model_invocation=True` and
        `bypass_invocation_check` isn't set — that flag exists ONLY for the
        explicit human `/skill-name` gesture path, never for ordinary
        model-initiated lookups.
        """
        winners = self._resolve_winners()
        match = winners.get(name)
        if match is None:
            raise SkillNotFoundError(f"No skill named {name!r} found in any tier")

        definition, _rank = match
        if definition.disable_model_invocation and not bypass_invocation_check:
            raise SkillNotFoundError(
                f"Skill {name!r} has disable_model_invocation=True; it can only be "
                "loaded via the explicit human '/skill-name' gesture path "
                "(pass bypass_invocation_check=True from that path)."
            )
        return definition.body
