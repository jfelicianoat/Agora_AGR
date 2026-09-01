from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agora.board import Board, BoardState
from agora.cards import Card
from agora.scheduler import Scheduler


def test_daily_schedule_materializes_once_per_period_without_assigning_profile(
    tmp_path: Path,
) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    template = Card.create(function="transform", request="summarize document", origin="agora")
    template.metadata["trigger"] = {"every": "day", "time": "09:00", "active": True}
    board.create("daily-summary.md", template, state=BoardState.SCHEDULED)
    scheduler = Scheduler(board)
    now = datetime(2026, 9, 1, 10, tzinfo=UTC)

    first = scheduler.materialize(now=now)
    second = scheduler.materialize(now=now)

    assert first[0].created is True
    assert second[0].created is False
    instance = Card.load(board.directory(BoardState.PENDING) / "2026-09-01-daily-summary.md")
    assert "recipient" not in instance.metadata
    assert instance.metadata["scheduled_from"] == "daily-summary.md"
    assert instance.attempts == 0


def test_schedule_does_not_catch_up_a_missed_weekly_day(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    template = Card.create(function="transform", request="summarize document", origin="agora")
    template.metadata["trigger"] = {"every": "week", "day": "monday", "active": True}
    board.create("weekly.md", template, state=BoardState.SCHEDULED)

    result = Scheduler(board).materialize(
        now=datetime(2026, 9, 1, 12, tzinfo=UTC)  # Tuesday
    )

    assert result[0].created is False
    assert not board.paths(BoardState.PENDING)
