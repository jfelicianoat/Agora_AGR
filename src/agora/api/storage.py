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

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        events = data.get("events", []) if isinstance(data, dict) else []
        return [dict(item) for item in events if isinstance(item, dict)]
