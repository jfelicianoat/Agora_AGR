from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

from agora.board import Board, BoardState
from agora.cards import Card
from agora.errors import ClaimConflict
from conftest import create_card


def test_two_workers_competing_for_one_card_have_exactly_one_winner(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    create_card(board)
    barrier = Barrier(2)

    def compete(agent: str) -> str:
        barrier.wait()
        try:
            board.claim("task.md", agent)
        except (ClaimConflict, FileNotFoundError):
            return "lost"
        return "won"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, ("worker-a", "worker-b")))

    assert results.count("won") == 1
    assert results.count("lost") == 1
    assert not board.paths(BoardState.PENDING)
    claimed = board.paths(BoardState.IN_PROGRESS)
    assert len(claimed) == 1
    assert Card.load(claimed[0]).metadata["agent"] in {"worker-a", "worker-b"}


def test_zombie_returns_to_pending_or_blocks_at_attempt_limit(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    old = datetime(2026, 9, 1, 8, tzinfo=UTC)
    now = old + timedelta(hours=2)
    create_card(board, "retry.md")
    board.claim("retry.md", "worker", when=old)
    create_card(board, "exhausted.md")
    exhausted = board.claim("exhausted.md", "worker", when=old)
    exhausted_card = Card.load(exhausted)
    exhausted_card.metadata["attempts"] = 2
    exhausted_card.save()

    actions = board.recover_zombies(now=now, threshold=timedelta(minutes=30))

    assert {action.card: action.destination for action in actions} == {
        "retry.md": BoardState.PENDING,
        "exhausted.md": BoardState.BLOCKED,
    }
    assert Card.load(board.directory(BoardState.PENDING) / "retry.md").attempts == 1
    blocked = Card.load(board.directory(BoardState.BLOCKED) / "exhausted.md")
    assert blocked.attempts == 3
    assert blocked.blocked


def test_board_initializes_all_six_states(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()

    assert {path.name for path in board.root.iterdir() if path.is_dir()} == {
        "pending",
        "in-progress",
        "done",
        "blocked",
        "archive",
        "scheduled",
    }
