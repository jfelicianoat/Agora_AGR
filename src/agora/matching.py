"""Blind, deterministic CARD-to-PROFILE matching."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from agora.cards import Card
from agora.profiles import Profile


def normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_like = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[a-z0-9]+", ascii_like.lower()))


def descriptor_matches(descriptor: str, request: str) -> bool:
    wanted = normalize(descriptor)
    haystack = normalize(request)
    return bool(wanted) and f" {wanted} " in f" {haystack} "


class MatchStatus(StrEnum):
    MATCHED = "matched"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"
    INVALID_RECIPIENT = "invalid_recipient"


@dataclass(frozen=True, slots=True)
class MatchResult:
    status: MatchStatus
    profile: Profile | None = None
    descriptor: str | None = None
    candidates: tuple[str, ...] = ()
    reason: str = ""


def match_card(card: Card, profiles: Iterable[Profile]) -> MatchResult:
    available = tuple(profiles)
    if card.recipient:
        direct = [
            profile for profile in available if normalize(profile.name) == normalize(card.recipient)
        ]
        if len(direct) != 1 or normalize(direct[0].function) != normalize(card.function):
            return MatchResult(
                MatchStatus.INVALID_RECIPIENT,
                candidates=tuple(profile.name for profile in direct),
                reason="recipient must name exactly one profile with the same function",
            )
        return MatchResult(
            MatchStatus.MATCHED,
            profile=direct[0],
            reason="valid recipient selected the profile directly",
        )

    ranked: list[tuple[int, str, Profile]] = []
    for profile in available:
        if normalize(profile.function) != normalize(card.function):
            continue
        if any(descriptor_matches(item, card.request) for item in profile.refuses):
            continue
        matching = [item for item in profile.handles if descriptor_matches(item, card.request)]
        if not matching:
            continue
        most_specific = max(matching, key=lambda item: (len(normalize(item)), normalize(item)))
        ranked.append((len(normalize(most_specific)), most_specific, profile))

    if not ranked:
        return MatchResult(MatchStatus.UNMATCHED, reason="no compatible handle matched")
    top_score = max(score for score, _, _ in ranked)
    winners = sorted(
        ((descriptor, profile) for score, descriptor, profile in ranked if score == top_score),
        key=lambda item: normalize(item[1].name),
    )
    if len(winners) != 1:
        return MatchResult(
            MatchStatus.AMBIGUOUS,
            descriptor=winners[0][0],
            candidates=tuple(profile.name for _, profile in winners),
            reason="equally specific handles matched more than one profile",
        )
    descriptor, profile = winners[0]
    return MatchResult(
        MatchStatus.MATCHED,
        profile=profile,
        descriptor=descriptor,
        reason="longest matching handle won",
    )


@dataclass(frozen=True, slots=True)
class OverlapIssue:
    owner: str
    descriptor: str
    status: MatchStatus
    resolved_to: str | None
    candidates: tuple[str, ...]


def check_overlaps(profiles: Iterable[Profile]) -> tuple[OverlapIssue, ...]:
    available = tuple(profiles)
    issues: list[OverlapIssue] = []
    for owner in available:
        for descriptor in owner.handles:
            probe = Card.create(function=owner.function, request=descriptor, origin="overlap-check")
            result = match_card(probe, available)
            resolved = result.profile.name if result.profile else None
            if result.status is not MatchStatus.MATCHED or resolved != owner.name:
                issues.append(
                    OverlapIssue(
                        owner=owner.name,
                        descriptor=descriptor,
                        status=result.status,
                        resolved_to=resolved,
                        candidates=result.candidates,
                    )
                )
    return tuple(issues)
