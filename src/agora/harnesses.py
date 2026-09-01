"""Harness seam and the deterministic F0 worker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agora.board import Board
from agora.documents import atomic_write_text
from agora.errors import InvalidTransition
from agora.profiles import Profile
from agora.skills import load_profile_skills


class WorkerLauncher(Protocol):
    def launch(self, board: Board, claimed_path: Path, profile: Profile) -> Path: ...


@dataclass(slots=True)
class DeterministicHarness:
    """A network-free worker used to prove the entire F0 lifecycle."""

    workspace_root: Path | None = None

    def launch(self, board: Board, claimed_path: Path, profile: Profile) -> Path:
        from agora.cards import Card

        card = Card.load(claimed_path)
        skills = load_profile_skills(profile.source.parent, profile.skills)
        skill_names = ", ".join(f"{skill.name}@{skill.version}" for skill in skills) or "none"
        board.progress(
            claimed_path,
            profile.name,
            [
                f"Admission passed for handle-compatible request '{card.request}'.",
                f"Mounted declared skills: {skill_names}.",
            ],
        )
        root = (self.workspace_root or board.root.parent).resolve()
        configured = card.metadata.get("destination")
        destination = (
            Path(configured)
            if isinstance(configured, str) and configured.strip()
            else Path("artifacts") / f"{claimed_path.stem}.txt"
        )
        if not destination.is_absolute():
            destination = root / destination
        destination = destination.resolve()
        try:
            destination.relative_to(root)
        except ValueError as exc:
            raise InvalidTransition(
                f"Harness destination escapes workspace: {destination}"
            ) from exc
        artifact = (
            f"Agora deterministic F0 artifact\n"
            f"profile: {profile.name}@{profile.version}\n"
            f"function: {card.function}\n"
            f"request: {card.request}\n"
            f"skills: {skill_names}\n"
        )
        atomic_write_text(destination, artifact)
        return board.close(
            claimed_path,
            actor=profile.name,
            paths=[destination],
            model="deterministic-f0",
        )
