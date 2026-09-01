"""Atomic SKILL loader and the seam for Athena SkillManifest adaptation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agora.documents import parse_markdown_document
from agora.errors import DocumentFormatError, SkillFormatError


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    version: str
    description: str
    modes: tuple[str, ...]
    metadata: dict[str, Any]
    instructions: str
    source: Path

    @classmethod
    def load(cls, path: Path) -> Skill:
        try:
            metadata, body = parse_markdown_document(
                path.read_text(encoding="utf-8"), source=str(path)
            )
        except DocumentFormatError as exc:
            raise SkillFormatError(str(exc)) from exc
        for field in ("name", "version", "description"):
            if not isinstance(metadata.get(field), str) or not metadata[field].strip():
                raise SkillFormatError(f"{path}: {field} must be a non-empty string")
        modes_value = metadata.get("modes", ["conversation", "card"])
        if not isinstance(modes_value, list) or any(
            not isinstance(item, str) for item in modes_value
        ):
            raise SkillFormatError(f"{path}: modes must be a list of strings")
        return cls(
            name=metadata["name"].strip(),
            version=metadata["version"].strip(),
            description=metadata["description"].strip(),
            modes=tuple(item.strip() for item in modes_value if item.strip()),
            metadata=metadata,
            instructions=body.strip(),
            source=path,
        )


def load_profile_skills(profile_root: Path, names: tuple[str, ...]) -> tuple[Skill, ...]:
    loaded: list[Skill] = []
    for name in names:
        path = profile_root / "skills" / name / "SKILL.md"
        if not path.is_file():
            raise SkillFormatError(f"Declared skill not found: {path}")
        skill = Skill.load(path)
        if "card" not in skill.modes and "scheduled" not in skill.modes:
            raise SkillFormatError(f"{path}: skill does not support card mode")
        loaded.append(skill)
    return tuple(loaded)
