"""Small F0 command-line surface for board operations and diagnostics."""

from __future__ import annotations

import argparse
from pathlib import Path

from agora.board import Board
from agora.dispatcher import Dispatcher
from agora.harnesses import DeterministicHarness
from agora.matching import check_overlaps
from agora.profiles import load_profiles


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agora")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create the six BOARD state directories")
    init.add_argument("board", type=Path)
    dispatch = commands.add_parser("dispatch", help="Run one deterministic heartbeat")
    dispatch.add_argument("board", type=Path)
    dispatch.add_argument("profiles", type=Path)
    dispatch.add_argument("--dry-run", action="store_true")
    overlap = commands.add_parser("check-overlap", help="Verify profile handle ownership")
    overlap.add_argument("profiles", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init":
        Board(args.board).initialize()
        print(f"BOARD ready: {args.board.resolve()}")
        return 0
    if args.command == "check-overlap":
        issues = check_overlaps(load_profiles(args.profiles))
        if not issues:
            print("No profile handle overlaps detected.")
            return 0
        for issue in issues:
            names = ", ".join(issue.candidates) or issue.resolved_to or "none"
            print(f"{issue.owner}: {issue.descriptor!r} -> {issue.status.value} ({names})")
        return 1
    board = Board(args.board)
    board.initialize()
    dispatcher = Dispatcher(board, args.profiles, DeterministicHarness(board.root.parent))
    outcomes = dispatcher.run_once(dry_run=args.dry_run)
    for outcome in outcomes:
        profile = f" -> {outcome.profile}" if outcome.profile else ""
        print(f"{outcome.card}: {outcome.status.value}{profile} ({outcome.reason})")
    return 1 if any(outcome.status == "error" for outcome in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
