from __future__ import annotations

from pathlib import Path

import yaml

from agora.board import Board
from agora.cards import Card


def write_document(path: Path, metadata: dict[str, object], body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    front = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip()
    path.write_text(f"---\n{front}\n---\n\n{body}", encoding="utf-8")
    return path


def write_profile(
    root: Path,
    name: str,
    *,
    function: str = "transform",
    handles: list[str] | None = None,
    refuses: list[str] | None = None,
    skills: list[str] | None = None,
    harness: str = "fake",
) -> Path:
    profile_root = root / name
    path = write_document(
        profile_root / "PROFILE.md",
        {
            "name": name,
            "version": "1.0.0",
            "description": f"Contract for {name}.",
            "function": function,
            "handles": handles or [name],
            "refuses": refuses or [],
            "inputs": [],
            "outputs": [],
            "guarantees": ["read-only inputs", "writes only to destination"],
            "model_capacity": "minimum",
            "model_modality": "text",
            "skills": skills or [],
            "harness": harness,
        },
        "# Mission\n\nProduce one verified artifact.\n",
    )
    for skill in skills or []:
        write_document(
            profile_root / "skills" / skill / "SKILL.md",
            {
                "name": skill,
                "version": "1.0.0",
                "description": f"How to {skill}.",
                "modes": ["conversation", "card"],
            },
            f"# Procedure\n\nFollow {skill} deterministically.\n",
        )
    return path


def create_card(board: Board, filename: str = "task.md", **overrides: object) -> Path:
    values: dict[str, object] = {
        "function": "transform",
        "request": "summarize document",
        "origin": "test",
    }
    values.update(overrides)
    card = Card.create(**values)  # type: ignore[arg-type]
    return board.create(filename, card)
