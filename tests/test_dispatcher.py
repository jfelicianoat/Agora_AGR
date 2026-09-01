from __future__ import annotations

from pathlib import Path

from agora.board import Board, BoardState
from agora.cards import Card
from agora.dispatcher import Dispatcher, DispatchStatus
from agora.harnesses import DeterministicHarness
from conftest import create_card, write_profile


def _dispatcher(tmp_path: Path, board: Board, *, max_dispatches: int = 3) -> Dispatcher:
    profiles_root = tmp_path / "AGENTS"
    write_profile(
        profiles_root,
        "summarizer",
        handles=["summarize"],
        skills=["summarize-text"],
    )
    return Dispatcher(
        board,
        profiles_root,
        DeterministicHarness(tmp_path),
        max_dispatches_per_round=max_dispatches,
    )


def test_missing_input_waits_without_incrementing_attempts(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    path = create_card(board, inputs={"source": "inputs/missing.txt"})
    dispatcher = _dispatcher(tmp_path, board)

    outcome = dispatcher.run_once()

    assert outcome[0].status is DispatchStatus.WAITING_INPUT
    assert path.exists()
    assert Card.load(path).attempts == 0
    assert not board.paths(BoardState.IN_PROGRESS)


def test_blocked_card_is_never_dispatched(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    path = create_card(board)
    card = Card.load(path)
    card.metadata["blocked"] = {"at": "2026-09-01T00:00:00Z", "reason": "human review"}
    card.save()
    dispatcher = _dispatcher(tmp_path, board)

    outcome = dispatcher.run_once()

    assert outcome[0].status is DispatchStatus.BLOCKED
    assert path.exists()
    assert not board.paths(BoardState.DONE)


def test_dry_run_changes_no_file(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    path = create_card(board)
    before = {
        item: (item.read_bytes(), item.stat().st_mtime_ns)
        for item in board.root.rglob("*")
        if item.is_file()
    }
    dispatcher = _dispatcher(tmp_path, board)

    outcome = dispatcher.run_once(dry_run=True)
    after = {
        item: (item.read_bytes(), item.stat().st_mtime_ns)
        for item in board.root.rglob("*")
        if item.is_file()
    }

    assert outcome[0].status is DispatchStatus.WOULD_DISPATCH
    assert before == after
    assert path.exists()


def test_dry_run_does_not_block_an_untrusted_origin(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    path = create_card(board, origin="external")
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    dispatcher = _dispatcher(tmp_path, board)

    outcome = dispatcher.run_once(dry_run=True)

    assert outcome[0].status is DispatchStatus.BLOCKED
    assert "dry-run" in outcome[0].reason
    assert path.exists()
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert not board.paths(BoardState.BLOCKED)


def test_end_to_end_fake_harness_closes_with_full_paths_and_readable_record(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    create_card(board, destination="deliverables/summary.txt")
    dispatcher = _dispatcher(tmp_path, board)

    outcome = dispatcher.run_once()

    assert outcome[0].status is DispatchStatus.DISPATCHED
    done_path = board.directory(BoardState.DONE) / "task.md"
    closed = Card.load(done_path)
    assert len(closed.metadata["paths"]) == 1
    artifact = Path(closed.metadata["paths"][0])
    assert artifact.is_absolute() and artifact.is_file()
    assert "profile: summarizer@1.0.0" in artifact.read_text(encoding="utf-8")
    assert "Admission passed" in closed.body
    assert "Mounted declared skills: summarize-text@1.0.0" in closed.body
    assert str(artifact) in closed.body
    assert "CARD closed." in closed.body


def test_urgent_card_dispatches_before_older_normal_card(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    create_card(board, "normal.md", priority="normal")
    create_card(board, "urgent.md", priority="urgent")
    dispatcher = _dispatcher(tmp_path, board, max_dispatches=1)

    outcomes = dispatcher.run_once()

    assert outcomes[0].card == "urgent.md"
    assert outcomes[0].status is DispatchStatus.DISPATCHED
    assert (board.directory(BoardState.PENDING) / "normal.md").exists()


def test_unmatched_card_stays_pending(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    path = create_card(board, request="translate document")
    dispatcher = _dispatcher(tmp_path, board)

    outcome = dispatcher.run_once()

    assert outcome[0].status is DispatchStatus.UNMATCHED
    assert path.exists()
