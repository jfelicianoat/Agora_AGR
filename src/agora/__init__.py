"""Agora atomic-work core."""

from importlib.metadata import PackageNotFoundError, version

from agora.board import Board, BoardState
from agora.cards import Card
from agora.dispatcher import Dispatcher
from agora.matching import MatchResult, match_card
from agora.profiles import Profile

try:
    __version__ = version("agora-atomic-work")
except PackageNotFoundError:  # ejecutado desde el repo, sin instalar
    __version__ = "0.2.9+dev"

__all__ = [
    "Board",
    "BoardState",
    "Card",
    "Dispatcher",
    "MatchResult",
    "Profile",
    "__version__",
    "match_card",
]
