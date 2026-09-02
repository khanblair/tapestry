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
