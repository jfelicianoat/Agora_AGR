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


def parse_recipient(value: str) -> tuple[str, int | None]:
    """Separa `nombre@mayor` en sus dos mitades.

    Sin `@` no hay version fijada, que es como se ha comportado siempre: el
    cliente nombra el perfil y se queda con el que haya. Con `@N` esta pidiendo
    **esa linea de version**, y si no esta se le dice; nunca se le da otra.
    """
    name, separator, wanted = value.partition("@")
    if not separator:
        return value.strip(), None
    if not wanted.strip().isdigit():
        raise ValueError(f"la version fijada tiene que ser un numero de linea: {value!r}")
    return name.strip(), int(wanted)


def match_card(card: Card, profiles: Iterable[Profile]) -> MatchResult:
    available = tuple(profiles)
    if card.recipient:
        try:
            wanted_name, wanted_major = parse_recipient(card.recipient)
        except ValueError as exc:
            return MatchResult(MatchStatus.INVALID_RECIPIENT, reason=str(exc))
        direct = [
            profile for profile in available if normalize(profile.name) == normalize(wanted_name)
        ]
        if len(direct) != 1 or normalize(direct[0].function) != normalize(card.function):
            return MatchResult(
                MatchStatus.INVALID_RECIPIENT,
                candidates=tuple(profile.name for profile in direct),
                reason="recipient must name exactly one profile with the same function",
            )
        if wanted_major is not None and direct[0].major != wanted_major:
            # Ejecutar otra linea de version seria darle al cliente un resultado
            # con una forma que no pidio, y ademas en silencio.
            return MatchResult(
                MatchStatus.INVALID_RECIPIENT,
                candidates=(f"{direct[0].name}@{direct[0].major}",),
                reason=(
                    f"se pidio {wanted_name}@{wanted_major} y aqui hay "
                    f"{direct[0].name}@{direct[0].major} ({direct[0].version})"
                ),
            )
        return MatchResult(
            MatchStatus.MATCHED,
            profile=direct[0],
            reason=(
                f"valid recipient selected {direct[0].name}@{direct[0].major} directly"
                if wanted_major is not None
                else "valid recipient selected the profile directly"
            ),
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
