"""PROFILEs de planificacion personal: carga, limites y no contaminacion.

Lo que estas pruebas protegen no es el formato —de eso ya hay pruebas— sino el
limite arquitectonico: Agora ejecuta trabajo de IA y **no** es un gestor de
calendario. Si algun dia alguien anade un `handles: schedule week` a uno de
estos perfiles, la suite tiene que ponerse roja.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agora.cards import Card
from agora.matching import MatchStatus, check_overlaps, match_card
from agora.profiles import Profile, load_profiles
from agora.skills import load_profile_skills

PROFILES_ROOT = Path(__file__).resolve().parents[1] / "profiles" / "task-planning"

EXPECTED = {
    "task-intake": "understand",
    "task-decomposer": "decompose",
    "review-analyzer": "analyze",
    "planning-advisor": "advise",
}

#: Vocabulario que delata que un perfil ha empezado a planificar por su cuenta.
CALENDAR_WORDS = (
    "calendar",
    "calendario",
    "workblock",
    "vacation",
    "vacaciones",
    "availability",
    "disponibilidad",
    "pause",
    "deadline",
    "timezone",
)


@pytest.fixture(scope="module")
def profiles() -> tuple[Profile, ...]:
    return load_profiles(PROFILES_ROOT)


# --- carga -----------------------------------------------------------------


def test_the_four_profiles_load(profiles: tuple[Profile, ...]) -> None:
    assert {profile.name for profile in profiles} == set(EXPECTED)


def test_each_profile_declares_its_own_function(profiles: tuple[Profile, ...]) -> None:
    assert {profile.name: profile.function for profile in profiles} == EXPECTED


def test_every_profile_is_versioned(profiles: tuple[Profile, ...]) -> None:
    """La version sube cuando el contrato del perfil cambia, no todas a la vez.

    No se fija aqui ningun numero concreto: hacerlo obliga a tocar esta prueba
    cada vez que un perfil evoluciona, que es justo lo contrario de lo que se
    quiere proteger. Lo que se comprueba es que son semver validos, que cada
    perfil se explica, y que **no van todos al mismo paso**.
    """
    for profile in profiles:
        major, minor, patch = profile.version.split(".")
        assert major.isdigit() and minor.isdigit() and patch.isdigit(), profile.name
        assert profile.description.strip()
        assert profile.body.strip()

    versions = {profile.name: profile.version for profile in profiles}
    assert len(set(versions.values())) > 1, versions


def test_a_profile_that_gained_a_skill_moved_past_its_first_version(
    profiles: tuple[Profile, ...],
) -> None:
    """Declarar una skill cambia lo que el perfil promete: eso se versiona."""
    for profile in profiles:
        if not profile.skills:
            continue
        assert profile.version != "1.0.0", profile.name


def test_no_profile_declares_a_skill_it_does_not_have(
    profiles: tuple[Profile, ...],
) -> None:
    """Un PROFILE que declara una skill inexistente no se puede ejecutar.

    `load_profile_skills` falla al no encontrar el `SKILL.md`, asi que las
    skills se declaran en la fase que las crea, no antes. Cargarlas todas es la
    comprobacion: si alguna falta, esto revienta.
    """
    for profile in profiles:
        loaded = load_profile_skills(profile.source.parent, profile.skills)
        assert len(loaded) == len(profile.skills), profile.name


# --- limites ---------------------------------------------------------------


def test_every_profile_refuses_to_schedule(profiles: tuple[Profile, ...]) -> None:
    for profile in profiles:
        assert "schedule" in profile.refuses, profile.name


def test_the_advisor_refuses_everything_temporal(
    profiles: tuple[Profile, ...],
) -> None:
    advisor = next(item for item in profiles if item.name == "planning-advisor")

    for refusal in ("schedule", "plan calendar", "assign days", "assign hours"):
        assert refusal in advisor.refuses


def test_no_profile_handles_calendar_work(profiles: tuple[Profile, ...]) -> None:
    """Ningun descriptor aceptado puede ser trabajo de calendario."""
    for profile in profiles:
        for descriptor in profile.handles:
            lowered = descriptor.lower()
            for word in CALENDAR_WORDS:
                assert word not in lowered, f"{profile.name}: {descriptor}"


def test_the_contracts_state_the_boundary(profiles: tuple[Profile, ...]) -> None:
    """El cuerpo del PROFILE es la instruccion que ve el modelo.

    Si el limite no esta escrito ahi, el modelo no lo conoce.
    """
    for profile in profiles:
        body = profile.body.lower()
        assert "no agenda" in body or "no planifica" in body, profile.name
        assert "cliente" in body, profile.name


def test_a_scheduling_request_is_never_matched(
    profiles: tuple[Profile, ...],
) -> None:
    for function in EXPECTED.values():
        card = Card.create(
            function=function,
            request="schedule the week and assign days",
            origin="test",
        )

        result = match_card(card, profiles)

        assert result.status is not MatchStatus.MATCHED, function


# --- matching --------------------------------------------------------------


def test_each_profile_owns_its_descriptors(profiles: tuple[Profile, ...]) -> None:
    """Ningun descriptor de un perfil se lo lleva otro."""
    assert check_overlaps(profiles) == ()


@pytest.mark.parametrize(
    ("function", "wording", "expected"),
    [
        ("understand", "understand request about the annexe", "task-intake"),
        ("decompose", "break down work for the report", "task-decomposer"),
        ("analyze", "analyze outcomes of last week", "review-analyzer"),
        ("advise", "advise on priorities for the month", "planning-advisor"),
    ],
)
def test_a_real_request_reaches_its_profile(
    profiles: tuple[Profile, ...],
    function: str,
    wording: str,
    expected: str,
) -> None:
    result = match_card(
        Card.create(function=function, request=wording, origin="test"), profiles
    )

    assert result.status is MatchStatus.MATCHED
    assert result.profile is not None
    assert result.profile.name == expected


def test_a_request_for_another_function_does_not_match(
    profiles: tuple[Profile, ...],
) -> None:
    """La funcion manda: pedir descomponer a un perfil de analisis no cuela."""
    result = match_card(
        Card.create(function="analyze", request="break down work", origin="test"),
        profiles,
    )

    assert result.status is MatchStatus.UNMATCHED


# --- reutilizables por cualquier cliente -----------------------------------


def test_nothing_names_the_client_application(profiles: tuple[Profile, ...]) -> None:
    """La capacidad es generica: no menciona a quien la encargo."""
    for profile in profiles:
        text = (profile.description + profile.body).lower()
        for forbidden in ("gestion de tareas ia", "gestion-tareas", "pomodoro"):
            assert forbidden not in text, profile.name


def test_the_readme_explains_where_the_boundary_is() -> None:
    readme = (PROFILES_ROOT / "README.md").read_text(encoding="utf-8").lower()

    assert "agora ejecuta el trabajo de ia" in readme
    assert "calendario" in readme
