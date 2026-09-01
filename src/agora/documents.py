"""Safe Markdown front-matter primitives shared by cards, profiles, and skills."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

from agora.errors import DocumentFormatError


class _StringSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that keeps timestamps as strings.

    Timestamps cross process and API boundaries in later phases. Letting PyYAML silently
    turn them into datetime/date objects makes a read-write round trip change their shape.
    """


_StringSafeLoader.yaml_implicit_resolvers = {
    key: [item for item in values if item[0] != "tag:yaml.org,2002:timestamp"]
    for key, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def parse_markdown_document(text: str, *, source: str = "document") -> tuple[dict[str, Any], str]:
    normalized = text.lstrip("\ufeff")
    lines = normalized.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise DocumentFormatError(f"{source}: front matter must start with ---")
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None
    )
    if closing is None:
        raise DocumentFormatError(f"{source}: front matter has no closing ---")
    raw_yaml = "".join(lines[1:closing])
    try:
        metadata = yaml.load(raw_yaml, Loader=_StringSafeLoader) or {}
    except yaml.YAMLError as exc:
        raise DocumentFormatError(f"{source}: invalid YAML front matter: {exc}") from exc
    if not isinstance(metadata, dict) or any(not isinstance(key, str) for key in metadata):
        raise DocumentFormatError(f"{source}: front matter must be a mapping with string keys")
    return metadata, "".join(lines[closing + 1 :])


def render_markdown_document(metadata: dict[str, Any], body: str) -> str:
    yaml_text = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip()
    normalized_body = body.lstrip("\r\n")
    if normalized_body and not normalized_body.endswith("\n"):
        normalized_body += "\n"
    return f"---\n{yaml_text}\n---\n\n{normalized_body}"


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
