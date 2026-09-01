"""Direct PROFILE census. Profiles are contracts and are never cached."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agora.documents import parse_markdown_document
from agora.errors import DocumentFormatError, ProfileFormatError


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    version: str
    description: str
    function: str
    handles: tuple[str, ...]
    refuses: tuple[str, ...]
    skills: tuple[str, ...]
    harness: str | None
    metadata: dict[str, Any]
    body: str
    source: Path

    @classmethod
    def load(cls, path: Path) -> Profile:
        try:
            metadata, body = parse_markdown_document(
                path.read_text(encoding="utf-8"), source=str(path)
            )
        except DocumentFormatError as exc:
            raise ProfileFormatError(str(exc)) from exc
        required = ("name", "version", "description", "function", "handles", "refuses")
        missing = [field for field in required if field not in metadata]
        if missing:
            raise ProfileFormatError(f"{path}: missing required fields: {', '.join(missing)}")
        for field in ("name", "version", "description", "function"):
            if not isinstance(metadata[field], str) or not metadata[field].strip():
                raise ProfileFormatError(f"{path}: {field} must be a non-empty string")
        handles = _string_tuple(metadata["handles"], path, "handles", allow_empty=False)
        refuses = _string_tuple(metadata["refuses"], path, "refuses", allow_empty=True)
        skills = _string_tuple(metadata.get("skills", []), path, "skills", allow_empty=True)
        harness_value = metadata.get("harness")
        if harness_value is not None and not isinstance(harness_value, str):
            raise ProfileFormatError(f"{path}: harness must be a string")
        return cls(
            name=metadata["name"].strip(),
            version=metadata["version"].strip(),
            description=metadata["description"].strip(),
            function=metadata["function"].strip(),
            handles=handles,
            refuses=refuses,
            skills=skills,
            harness=harness_value.strip()
            if isinstance(harness_value, str) and harness_value.strip()
            else None,
            metadata=metadata,
            body=body,
            source=path,
        )


def _string_tuple(value: Any, path: Path, field: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ProfileFormatError(f"{path}: {field} must be a list of non-empty strings")
    result = tuple(item.strip() for item in value)
    if not result and not allow_empty:
        raise ProfileFormatError(f"{path}: {field} must not be empty")
    return result


def load_profiles(root: Path) -> tuple[Profile, ...]:
    if not root.exists():
        return ()
    return tuple(Profile.load(path) for path in sorted(root.rglob("PROFILE.md")))
