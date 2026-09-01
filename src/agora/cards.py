"""CARD contract, round-trip serialization, and human-readable Record updates."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agora.documents import atomic_write_text, parse_markdown_document, render_markdown_document
from agora.errors import CardFormatError, DocumentFormatError

_REQUIRED_FIELDS = ("function", "request", "created", "origin", "paths", "attempts")


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(slots=True)
class Card:
    metadata: dict[str, Any]
    body: str
    source: Path | None = None

    @classmethod
    def parse(cls, text: str, *, source: str = "CARD") -> Card:
        try:
            metadata, body = parse_markdown_document(text, source=source)
        except DocumentFormatError as exc:
            raise CardFormatError(str(exc)) from exc
        card = cls(metadata, body)
        card.validate(source=source)
        return card

    @classmethod
    def load(cls, path: Path) -> Card:
        card = cls.parse(path.read_text(encoding="utf-8"), source=str(path))
        card.source = path
        return card

    @classmethod
    def create(
        cls,
        *,
        function: str,
        request: str,
        origin: str,
        created: datetime | None = None,
        inputs: dict[str, Any] | None = None,
        destination: str | None = None,
        priority: str | int = "normal",
        recipient: str | None = None,
        max_attempts: int = 3,
        body: str = "",
    ) -> Card:
        metadata: dict[str, Any] = {
            "function": function,
            "request": request,
            "created": format_timestamp(created or utc_now()),
            "origin": origin,
            "paths": [],
            "attempts": 0,
            "max_attempts": max_attempts,
            "priority": priority,
        }
        if inputs:
            metadata["inputs"] = inputs
        if destination:
            metadata["destination"] = destination
        if recipient:
            metadata["recipient"] = recipient
        card = cls(metadata, body)
        card.ensure_record()
        card.validate()
        return card

    def validate(self, *, source: str = "CARD") -> None:
        missing = [field for field in _REQUIRED_FIELDS if field not in self.metadata]
        if missing:
            raise CardFormatError(f"{source}: missing required fields: {', '.join(missing)}")
        for field in ("function", "request", "created", "origin"):
            value = self.metadata[field]
            if not isinstance(value, str) or not value.strip():
                raise CardFormatError(f"{source}: {field} must be a non-empty string")
        paths = self.metadata["paths"]
        if not isinstance(paths, list) or any(not isinstance(item, str) for item in paths):
            raise CardFormatError(f"{source}: paths must be a list of strings")
        attempts = self.metadata["attempts"]
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
            raise CardFormatError(f"{source}: attempts must be a non-negative integer")
        maximum = self.metadata.get("max_attempts", 3)
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
            raise CardFormatError(f"{source}: max_attempts must be a positive integer")
        inputs = self.metadata.get("inputs", {})
        if inputs is not None and not isinstance(inputs, dict):
            raise CardFormatError(f"{source}: inputs must be a mapping")

    @property
    def function(self) -> str:
        return str(self.metadata["function"])

    @property
    def request(self) -> str:
        return str(self.metadata["request"])

    @property
    def recipient(self) -> str | None:
        value = self.metadata.get("recipient")
        return value.strip() if isinstance(value, str) and value.strip() else None

    @property
    def attempts(self) -> int:
        return int(self.metadata["attempts"])

    @property
    def max_attempts(self) -> int:
        return int(self.metadata.get("max_attempts", 3))

    @property
    def blocked(self) -> bool:
        return bool(self.metadata.get("blocked"))

    @property
    def priority_score(self) -> int:
        value = self.metadata.get("priority", "normal")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return 1000 if str(value).strip().lower() == "urgent" else 100

    def ensure_record(self) -> None:
        if not any(line.strip().lower() == "## record" for line in self.body.splitlines()):
            content = self.body.rstrip()
            self.body = f"{content}\n\n## Record\n" if content else "## Record\n"

    def append_record(
        self,
        actor: str,
        milestones: Iterable[str],
        *,
        when: datetime | None = None,
    ) -> None:
        self.ensure_record()
        clean = [item.strip() for item in milestones if item.strip()]
        if not clean:
            return
        moment = (when or utc_now()).astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        entry = [f"- {moment}", *(f"    - {actor}: {item}" for item in clean)]
        self.body = self.body.rstrip() + "\n" + "\n".join(entry) + "\n"

    def serialize(self) -> str:
        self.validate(source=str(self.source) if self.source else "CARD")
        return render_markdown_document(self.metadata, self.body)

    def save(self, path: Path | None = None) -> Path:
        target = path or self.source
        if target is None:
            raise ValueError("A CARD needs a target path")
        atomic_write_text(target, self.serialize())
        self.source = target
        return target
