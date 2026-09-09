"""Small durable stores for API idempotency and event history."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from agora.cards import format_timestamp, utc_now
from agora.documents import atomic_write_text


class IdempotencyConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status_code: int
    payload: dict[str, Any]


class IdempotencyStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    @staticmethod
    def digest(payload: Any) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def lookup(self, scope: str, key: str, digest: str) -> StoredResponse | None:
        with self._lock:
            entries = self._read()
            value = entries.get(f"{scope}:{key}")
            if value is None:
                return None
            if value.get("digest") != digest:
                raise IdempotencyConflict("Idempotency-Key was reused with a different request")
            return StoredResponse(int(value["status_code"]), dict(value["payload"]))

    def save(
        self,
        scope: str,
        key: str,
        digest: str,
        *,
        status_code: int,
        payload: dict[str, Any],
    ) -> None:
        with self._lock:
            entries = self._read()
            compound = f"{scope}:{key}"
            existing = entries.get(compound)
            if existing is not None and existing.get("digest") != digest:
                raise IdempotencyConflict("Idempotency-Key was reused with a different request")
            entries[compound] = {
                "digest": digest,
                "status_code": status_code,
                "payload": payload,
                "saved_at": format_timestamp(utc_now()),
            }
            atomic_write_text(
                self.path,
                json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2) + "\n",
            )

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        entries = data.get("entries", {}) if isinstance(data, dict) else {}
        return entries if isinstance(entries, dict) else {}


@dataclass(frozen=True, slots=True)
class EventPage:
    """Lo que un cliente necesita para sondear sin perderse nada."""

    events: tuple[dict[str, Any], ...]
    #: Lo que hay que mandar como `after` en la siguiente vuelta.
    cursor: int
    #: El evento mas antiguo que el tablero conserva.
    oldest_available: int
    #: El ultimo evento que existe, haya llegado en esta pagina o no.
    newest: int
    #: `True` si el cursor del cliente quedo por detras de la ventana.
    missed: bool
    #: `True` si quedan eventos por leer: hay que volver a pedir sin esperar.
    more: bool


class EventStore:
    def __init__(self, path: Path, *, limit: int = 10_000) -> None:
        self.path = path
        self.limit = max(1, limit)
        self._lock = Lock()

    def emit(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            events = self._read()
            event = {
                "id": (int(events[-1]["id"]) + 1) if events else 1,
                "at": format_timestamp(utc_now()),
                "kind": kind,
                "data": data,
            }
            events.append(event)
            events = events[-self.limit :]
            atomic_write_text(
                self.path,
                json.dumps({"version": 1, "events": events}, ensure_ascii=False, indent=2) + "\n",
            )
            return event

    def since(self, after: int = 0) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(event for event in self._read() if int(event.get("id", 0)) > after)

    def page(self, after: int = 0, limit: int = 200) -> EventPage:
        """Una pagina de eventos, y lo que hace falta para saber si falta alguno.

        El almacen guarda una ventana de los ultimos `limit` eventos. Un cliente
        que estuvo apagado el tiempo suficiente puede volver con un cursor
        anterior a esa ventana, y entonces **le faltan eventos que no sabe que
        existieron**. Devolver solo la lista lo dejaria creyendo que esta al dia.

        Por eso la pagina dice cual es el evento mas antiguo que queda: si es
        posterior al cursor del cliente, hubo un hueco y hay que decirselo.
        """
        with self._lock:
            events = self._read()
        oldest = int(events[0]["id"]) if events else 0
        newest = int(events[-1]["id"]) if events else 0
        pending = [event for event in events if int(event.get("id", 0)) > after]
        page = pending[: max(1, limit)]
        return EventPage(
            events=tuple(page),
            cursor=int(page[-1]["id"]) if page else max(after, newest),
            oldest_available=oldest,
            newest=newest,
            # Un cursor por detras de la ventana significa que se perdieron
            # eventos. `after == 0` es un cliente que empieza de cero y no ha
            # perdido nada: no tenia cursor que quedarse atras.
            missed=bool(events) and after > 0 and after < oldest - 1,
            more=len(pending) > len(page),
        )

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        events = data.get("events", []) if isinstance(data, dict) else []
        return [dict(item) for item in events if isinstance(item, dict)]
