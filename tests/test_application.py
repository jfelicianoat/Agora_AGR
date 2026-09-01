from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agora.application import AgoraApplication
from agora.board import BoardState
from conftest import create_card, write_profile


def test_application_core_imports_without_qt() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import agora.application; "
            "assert not any(name.startswith('PySide6') for name in sys.modules)",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr


def test_snapshot_reflects_real_board_movements(tmp_path: Path) -> None:
    service = AgoraApplication(tmp_path)
    service.initialize()
    create_card(service.board)

    pending = service.snapshot()
    service.board.claim("task.md", "worker")
    claimed = service.snapshot()

    assert [card.filename for card in pending.cards_in(BoardState.PENDING)] == ["task.md"]
    assert not claimed.cards_in(BoardState.PENDING)
    assert claimed.cards_in(BoardState.IN_PROGRESS)[0].agent == "worker"


def test_corrupt_card_and_profile_are_visible_without_hiding_valid_items(tmp_path: Path) -> None:
    service = AgoraApplication(tmp_path)
    service.initialize()
    create_card(service.board, "valid.md")
    corrupt_card = service.board.directory(BoardState.PENDING) / "corrupt.md"
    corrupt_card.write_text("---\nfunction: [broken\n---\n", encoding="utf-8")
    write_profile(tmp_path / "AGENTS", "valid-profile", handles=["summarize"])
    corrupt_profile = tmp_path / "AGENTS" / "broken" / "PROFILE.md"
    corrupt_profile.parent.mkdir(parents=True)
    corrupt_profile.write_text("---\nname: [broken\n---\n", encoding="utf-8")

    snapshot = service.snapshot()

    assert {card.filename for card in snapshot.cards} == {"valid.md", "corrupt.md"}
    assert next(card for card in snapshot.cards if card.filename == "corrupt.md").corrupt
    assert [profile.name for profile in snapshot.profiles] == ["valid-profile"]
    assert {Path(error.source).name for error in snapshot.errors} == {
        "corrupt.md",
        "PROFILE.md",
    }


def test_dispatch_action_uses_domain_service_and_records_activity(tmp_path: Path) -> None:
    service = AgoraApplication(tmp_path)
    service.initialize()
    write_profile(
        tmp_path / "AGENTS",
        "summarizer",
        handles=["summarize"],
        skills=["summarize-text"],
    )
    create_card(service.board, destination="artifacts/ui-summary.txt")

    outcomes = service.dispatch_once()
    snapshot = service.snapshot()

    assert outcomes[0].status == "dispatched"
    assert not snapshot.cards_in(BoardState.PENDING)
    assert snapshot.cards_in(BoardState.DONE)[0].filename == "task.md"
    assert any("task.md: dispatched via summarizer" in item.message for item in snapshot.activity)
    detail = service.card_detail(BoardState.DONE, "task.md")
    assert "CARD closed." in detail.record
    assert Path(detail.metadata["paths"][0]).is_file()


def test_card_detail_rejects_path_traversal(tmp_path: Path) -> None:
    service = AgoraApplication(tmp_path)
    service.initialize()

    try:
        service.card_detail(BoardState.PENDING, "../outside.md")
    except ValueError as exc:
        assert "plain .md filename" in str(exc)
    else:
        raise AssertionError("path traversal must be rejected")
