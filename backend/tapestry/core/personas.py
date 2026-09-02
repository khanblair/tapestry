"""Persona identity and permissions.

A persona is a scoped identity: its own model choice, its own tool/MCP
permissions, its own standing system prompt. Persona *content* lives as data
in `personas/*.yaml` at the repo root (not in this package) — this module is
just the loader that turns those files into `Persona` objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel


class Persona(BaseModel):
    id: str
    name: str
    role: str
    model: str
    system_prompt: str
    tools: list[str]
    mcp_servers: list[str]
    status: Literal["online", "busy", "paused", "offline"]
    color: str


def load_personas(directory: str = "personas") -> dict[str, Persona]:
    """Load every `*.yaml` file in `directory` as one persona.

    Each file is one persona; fields match `Persona`. `id` defaults to the
    filename stem (e.g. `ada.yaml` -> id `"ada"`) when the YAML itself
    doesn't set one, so a persona's id and its filename agree by
    convention without forcing the file to repeat it.
    """
    personas: dict[str, Persona] = {}
    directory_path = Path(directory)
    for yaml_path in sorted(directory_path.glob("*.yaml")):
        with yaml_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        data.setdefault("id", yaml_path.stem)
        persona = Persona(**data)
        personas[persona.id] = persona
    return personas


def save_persona(persona: Persona, directory: str = "personas") -> None:
    """Write `persona` to `<directory>/<persona.id>.yaml`, creating or
    overwriting the file. The write-side inverse of `load_personas` for one
    persona at a time.

    Added for `adapters/web_adapter/api.py`'s persona create/update/pause-all
    endpoints — `load_personas` was read-only, and persona *content* is
    explicitly documented (this module's own header) as living in
    `personas/*.yaml`, not in any database, so a real create/update endpoint
    has nowhere else to durably persist a change. Kept intentionally
    symmetrical with `load_personas`: same YAML shape (`Persona.model_dump()`
    written back with `yaml.safe_dump`), same filename convention
    (`<id>.yaml`), so a file this writes loads back byte-for-byte equivalent
    via `load_personas`. `directory` is created if it doesn't exist yet
    (harmless for the real `personas/` dir, which always does; useful for
    callers pointed at an isolated directory, e.g. tests).
    """
    directory_path = Path(directory)
    directory_path.mkdir(parents=True, exist_ok=True)
    yaml_path = directory_path / f"{persona.id}.yaml"
    data = persona.model_dump(mode="python")
    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
