"""Idempotent materialization of scheduled CARD templates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from agora.board import Board, BoardState
from agora.cards import Card, format_timestamp
from agora.errors import AgoraError, CardFormatError, InvalidTransition

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}


@dataclass(frozen=True, slots=True)
class ScheduledMaterialization:
    template: str
    instance: str
    created: bool
    reason: str
    #: La plantilla no se pudo leer o la instancia no se pudo crear.
    error: bool = False


class Scheduler:
    def __init__(self, board: Board) -> None:
        self.board = board

    def materialize(self, *, now: datetime | None = None) -> tuple[ScheduledMaterialization, ...]:
        # El dia y la hora de `trigger` son los del reloj de quien la escribio:
        # hora local del PC del tablero. Una `now` explicita manda su propia zona.
        current = now if now is not None else datetime.now().astimezone()
        if current.tzinfo is None:
            current = current.astimezone()
        outcomes: list[ScheduledMaterialization] = []
        for template_path in self.board.paths(BoardState.SCHEDULED):
            try:
                card = Card.load(template_path)
            except (CardFormatError, OSError) as exc:
                # Una plantilla rota no puede parar a las demas ni el latido.
                outcomes.append(
                    ScheduledMaterialization(template_path.name, "", False, str(exc), True)
                )
                continue
            trigger = card.metadata.get("trigger")
            due, reason = _is_due(trigger, current)
            if not due:
                outcomes.append(ScheduledMaterialization(template_path.name, "", False, reason))
                continue
            filename = f"{current.date().isoformat()}-{_slug(template_path.stem)}.md"
            # La instancia del periodo puede estar ya en cualquier carpeta: si
            # sólo se miraba `pending`, en cuanto el runner la reclamaba se volvía
            # a crear, se pagaba dos veces y el duplicado se atascaba (A-1).
            if any((self.board.directory(state) / filename).exists() for state in BoardState):
                outcomes.append(
                    ScheduledMaterialization(
                        template_path.name, filename, False, "already materialized"
                    )
                )
                continue
            metadata = dict(card.metadata)
            metadata.pop("trigger", None)
            metadata["scheduled_from"] = template_path.name
            metadata["created"] = format_timestamp(current)
            metadata["paths"] = []
            metadata["attempts"] = 0
            # Lo que es de una ejecución concreta no se hereda: un `remote.task_id`
            # haría que el runner reanudara una tarea vieja del broker (A-8).
            for field in (
                "agent", "claimed", "closed", "blocked", "model", "cancelled", "remote",
                "releases", "free_releases", "attempt_budget", "external_reference",
                "review_context", "origin_identity", "flow_id", "execution", "review_gate",
                "profile", "profile_version",
            ):
                metadata.pop(field, None)
            instance = Card(metadata, card.body)
            instance.append_record(
                "scheduler", [f"Materialized from {template_path.name}."], when=current
            )
            try:
                self.board.create(filename, instance)
            except InvalidTransition:
                # Otro proceso (la ventana o el API) la creó en el mismo instante.
                outcomes.append(
                    ScheduledMaterialization(
                        template_path.name, filename, False, "already materialized"
                    )
                )
                continue
            except (AgoraError, OSError) as exc:
                outcomes.append(
                    ScheduledMaterialization(template_path.name, filename, False, str(exc), True)
                )
                continue
            outcomes.append(ScheduledMaterialization(template_path.name, filename, True, "due now"))
        return tuple(outcomes)


def _is_due(trigger: Any, current: datetime) -> tuple[bool, str]:
    if not isinstance(trigger, dict):
        return False, "missing trigger"
    if trigger.get("active") is False:
        return False, "inactive"
    every = str(trigger.get("every", "")).strip().lower()
    not_before = _parse_time(trigger.get("time"))
    if not_before is not None and current.time().replace(tzinfo=None) < not_before:
        return False, "before configured time"
    if every == "day":
        return True, "daily period is due"
    if every == "week":
        weekday = _weekday(trigger.get("day"))
        return (
            current.weekday() == weekday,
            "weekly day is due" if current.weekday() == weekday else "not weekly day",
        )
    if every == "month":
        day = trigger.get("day", 1)
        if not isinstance(day, int) or isinstance(day, bool) or day < 1 or day > 31:
            return False, "invalid monthly day"
        return (
            current.day == day,
            "monthly day is due" if current.day == day else "not monthly day",
        )
    return False, "unsupported period"


def _parse_time(value: Any) -> time | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return time.max
    try:
        return time.fromisoformat(value)
    except ValueError:
        return time.max


def _weekday(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 6:
        return value
    normalized = str(value or "monday").strip().lower()
    return _WEEKDAYS.get(normalized, -1)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "scheduled"
