from __future__ import annotations

from pathlib import Path

import pytest

from agora.cards import Card
from agora.errors import CardFormatError


def test_round_trip_card_preserves_front_matter_body_and_unknown_fields(tmp_path: Path) -> None:
    source = tmp_path / "card.md"
    card = Card.create(
        function="transform",
        request="summarize the report",
        origin="athena",
        inputs={"source": "inputs/report.txt"},
        body="# Work\n\nKeep this body exactly meaningful.\n",
    )
    card.metadata["extension_field"] = {"kept": True}
    card.save(source)

    loaded = Card.load(source)
    loaded.metadata["attempts"] = 1
    loaded.append_record("tester", ["Round-trip mutation allowed."])
    loaded.save()
    reread = Card.load(source)

    assert reread.metadata["extension_field"] == {"kept": True}
    assert reread.attempts == 1
    assert "Keep this body exactly meaningful." in reread.body
    assert "## Record" in reread.body
    assert "tester: Round-trip mutation allowed." in reread.body


def test_corrupt_front_matter_is_a_visible_error() -> None:
    with pytest.raises(CardFormatError, match="invalid YAML"):
        Card.parse("---\npaths: [unterminated\n---\nbody")


def test_card_cannot_close_its_contract_with_invalid_paths_shape() -> None:
    text = """---
function: transform
request: summarize
created: 2026-09-01T00:00:00Z
origin: test
paths: nope
attempts: 0
---
"""
    with pytest.raises(CardFormatError, match="paths"):
        Card.parse(text)
