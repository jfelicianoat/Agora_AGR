"""Application services shared by desktop, CLI, and future transport adapters.

This module is intentionally free of Qt and networking.  It is the only surface the
desktop UI needs in F1, keeping filesystem ownership and domain transitions in the core.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from agora.board import Board, BoardState
from agora.cards import Card, format_timestamp, utc_now
from agora.dispatcher import Dispatcher, DispatchOutcome
from agora.documents import atomic_write_text
from agora.errors import AgoraError, CardFormatError, ProfileFormatError
from agora.harnesses import DeterministicHarness, WorkerLauncher
from agora.profiles import Profile


@dataclass(frozen=True, slots=True)
class CardSummary:
    state: BoardState
    filename: str
    function: str
    request: str
    priority: str
    attempts: int
    agent: str | None
    corrupt: bool = False


@dataclass(frozen=True, slots=True)
class CardDetail:
    summary: CardSummary
    metadata: dict[str, Any]
    body: str
    record: str
    source: Path


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    name: str
    version: str
    function: str
    description: str
    handles: tuple[str, ...]
    refuses: tuple[str, ...]
    skills: tuple[str, ...]
    harness: str | None
    source: Path


@dataclass(frozen=True, slots=True)
class VisibleError:
    source: str
    message: str


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    timestamp: str
    level: str
    message: str


@dataclass(frozen=True, slots=True)
class AgoraSnapshot:
    captured_at: str
    cards: tuple[CardSummary, ...]
    profiles: tuple[ProfileSummary, ...]
    errors: tuple[VisibleError, ...]
    activity: tuple[ActivityEntry, ...]
    dispatcher_status: str

    def cards_in(self, state: BoardState) -> tuple[CardSummary, ...]:
        return tuple(card for card in self.cards if card.state is state)


class AgoraApplication:
    """Qt-free façade over Agora's domain services."""

    def __init__(
        self,
        workspace: Path,
        *,
        board_name: str = "KANBAN",
        profiles_name: str = "AGENTS",
        launcher: WorkerLauncher | None = None,
        activity_limit: int = 200,
    ) -> None:
        self.workspace = workspace.resolve()
        self.board = Board(self.workspace / board_name)
        self.profiles_root = self.workspace / profiles_name
        self.launcher = launcher or DeterministicHarness(self.workspace)
        self._activity: deque[ActivityEntry] = deque(maxlen=max(1, activity_limit))
        self._dispatcher_status = "idle"

    def initialize(self) -> None:
        self.board.initialize()
        self._log("info", f"BOARD ready at {self.board.root} (id {self.instance_id})")

    @property
    def instance_id(self) -> str:
        """Identidad estable de este tablero.

        Entra en la clave de idempotencia que Agora manda al broker. Sin ella,
        dos tableros con una tarjeta del mismo nombre —el de producción y un
        ensayo, por ejemplo— chocan en el broker con `IDEMPOTENCY_CONFLICT`, y
        el segundo no puede ejecutar nada. Observado ejecutándolo, no deducido.
        """
        marker = self.workspace / ".agora-instance"
        if marker.is_file():
            existing = marker.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        generated = uuid4().hex
        marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(marker, f"{generated}\n")
        return generated

    def snapshot(self) -> AgoraSnapshot:
        cards, card_errors = self._card_census()
        profiles, profile_errors = self._profile_census()
        return AgoraSnapshot(
            captured_at=format_timestamp(utc_now()),
            cards=cards,
            profiles=profiles,
            errors=card_errors + profile_errors,
            activity=tuple(self._activity),
            dispatcher_status=self._dispatcher_status,
        )

    def card_detail(self, state: BoardState, filename: str) -> CardDetail:
        name = _plain_card_name(filename)
        path = self.board.directory(state) / name
        card = Card.load(path)
        return CardDetail(
            summary=_card_summary(state, path, card),
            metadata=dict(card.metadata),
            body=card.body,
            record=_record_section(card.body),
            source=path.resolve(),
        )

    def dispatch_once(self, *, dry_run: bool = False) -> tuple[DispatchOutcome, ...]:
        self._dispatcher_status = "simulating" if dry_run else "running"
        self._log("info", "Dispatcher dry-run started" if dry_run else "Dispatcher started")
        try:
            outcomes = Dispatcher(self.board, self.profiles_root, self.launcher).run_once(
                dry_run=dry_run
            )
        except (AgoraError, OSError) as exc:
            self._dispatcher_status = "error"
            self._log("error", f"Dispatcher failed: {type(exc).__name__}: {exc}")
            raise
        for outcome in outcomes:
            profile = f" via {outcome.profile}" if outcome.profile else ""
            self._log(
                "error" if outcome.status == "error" else "info",
                f"{outcome.card}: {outcome.status.value}{profile} — {outcome.reason}",
            )
        self._dispatcher_status = "idle"
        return outcomes

    def _card_census(self) -> tuple[tuple[CardSummary, ...], tuple[VisibleError, ...]]:
        cards: list[CardSummary] = []
        errors: list[VisibleError] = []
        for state in BoardState:
            for path in self.board.paths(state):
                try:
                    card = Card.load(path)
                except (CardFormatError, OSError) as exc:
                    cards.append(
                        CardSummary(state, path.name, "—", "CARD unreadable", "—", 0, None, True)
                    )
                    errors.append(VisibleError(str(path.resolve()), str(exc)))
                    continue
                cards.append(_card_summary(state, path, card))
        return tuple(cards), tuple(errors)

    def _profile_census(
        self,
    ) -> tuple[tuple[ProfileSummary, ...], tuple[VisibleError, ...]]:
        profiles: list[ProfileSummary] = []
        errors: list[VisibleError] = []
        if not self.profiles_root.exists():
            return (), ()
        for path in sorted(self.profiles_root.rglob("PROFILE.md")):
            try:
                profile = Profile.load(path)
            except (ProfileFormatError, OSError) as exc:
                errors.append(VisibleError(str(path.resolve()), str(exc)))
                continue
            profiles.append(
                ProfileSummary(
                    profile.name,
                    profile.version,
                    profile.function,
                    profile.description,
                    profile.handles,
                    profile.refuses,
                    profile.skills,
                    profile.harness,
                    path.resolve(),
                )
            )
        return tuple(profiles), tuple(errors)

    def _log(self, level: str, message: str, *, when: datetime | None = None) -> None:
        self._activity.append(
            ActivityEntry(format_timestamp(when or utc_now()), level.lower(), message)
        )


def _card_summary(state: BoardState, path: Path, card: Card) -> CardSummary:
    priority = card.metadata.get("priority", "normal")
    agent = card.metadata.get("agent")
    return CardSummary(
        state=state,
        filename=path.name,
        function=card.function,
        request=card.request,
        priority=str(priority),
        attempts=card.attempts,
        agent=agent.strip() if isinstance(agent, str) and agent.strip() else None,
    )


def _record_section(body: str) -> str:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() == "## record":
            return "\n".join(lines[index + 1 :]).strip()
    return ""


def _plain_card_name(filename: str) -> str:
    candidate = Path(filename)
    if candidate.name != filename or candidate.suffix.lower() != ".md":
        raise ValueError("CARD filename must be a plain .md filename")
    return filename
