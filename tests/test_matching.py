from __future__ import annotations

from pathlib import Path

from agora.cards import Card
from agora.matching import MatchStatus, check_overlaps, match_card
from agora.profiles import load_profiles
from conftest import write_profile


def test_card_without_compatible_profile_remains_unmatched(tmp_path: Path) -> None:
    profiles_root = tmp_path / "AGENTS"
    write_profile(profiles_root, "translator", handles=["translate"])
    card = Card.create(function="transform", request="summarize document", origin="test")

    result = match_card(card, load_profiles(profiles_root))

    assert result.status is MatchStatus.UNMATCHED


def test_refuses_eliminates_a_candidate(tmp_path: Path) -> None:
    profiles_root = tmp_path / "AGENTS"
    write_profile(
        profiles_root,
        "summarizer",
        handles=["summarize"],
        refuses=["medical record"],
    )
    card = Card.create(
        function="transform",
        request="summarize medical record",
        origin="test",
    )

    assert match_card(card, load_profiles(profiles_root)).status is MatchStatus.UNMATCHED


def test_most_specific_descriptor_wins(tmp_path: Path) -> None:
    profiles_root = tmp_path / "AGENTS"
    write_profile(profiles_root, "general", handles=["summarize"])
    write_profile(profiles_root, "legal", handles=["summarize legal contract"])
    card = Card.create(
        function="transform",
        request="summarize legal contract",
        origin="test",
    )

    result = match_card(card, load_profiles(profiles_root))

    assert result.status is MatchStatus.MATCHED
    assert result.profile is not None and result.profile.name == "legal"
    assert result.descriptor == "summarize legal contract"


def test_valid_recipient_skips_handles_but_invalid_recipient_does_not(tmp_path: Path) -> None:
    profiles_root = tmp_path / "AGENTS"
    write_profile(profiles_root, "translator", handles=["translate"])
    profiles = load_profiles(profiles_root)
    direct = Card.create(
        function="transform",
        request="summarize document",
        origin="test",
        recipient="translator",
    )
    unknown = Card.create(
        function="transform",
        request="summarize document",
        origin="test",
        recipient="missing",
    )
    wrong_function = Card.create(
        function="research",
        request="translate document",
        origin="test",
        recipient="translator",
    )

    assert match_card(direct, profiles).status is MatchStatus.MATCHED
    assert match_card(unknown, profiles).status is MatchStatus.INVALID_RECIPIENT
    assert match_card(wrong_function, profiles).status is MatchStatus.INVALID_RECIPIENT


def test_overlap_checker_detects_ambiguous_profile_handles(tmp_path: Path) -> None:
    profiles_root = tmp_path / "AGENTS"
    write_profile(profiles_root, "one", handles=["summarize"])
    write_profile(profiles_root, "two", handles=["summarize"])

    issues = check_overlaps(load_profiles(profiles_root))

    assert {(issue.owner, issue.descriptor) for issue in issues} == {
        ("one", "summarize"),
        ("two", "summarize"),
    }
    assert all(issue.status is MatchStatus.AMBIGUOUS for issue in issues)
