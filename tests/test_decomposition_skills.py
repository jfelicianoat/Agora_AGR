"""Skills de descomposicion: contrato compartido, esfuerzo y ninguna fecha.

Las cuatro skills producen el mismo documento (`work-breakdown` v1). Lo que
cambia entre ellas es el procedimiento, no la forma: quien consuma el resultado
no tiene que saber si el encargo era un curso o un programa.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agora.broker.contracts import BrokerPolicy
from agora.broker.executor import apply_skill_output_contract
from agora.broker.request_builder import build_broker_request
from agora.cards import Card
from agora.profiles import Profile
from agora.skills import Skill, load_profile_skills

PROFILE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "profiles"
    / "task-planning"
    / "task-decomposer"
)

SPECIALISED = ("decompose-course", "decompose-study", "decompose-software")


@pytest.fixture(scope="module")
def profile() -> Profile:
    return Profile.load(PROFILE_ROOT / "PROFILE.md")


@pytest.fixture(scope="module")
def skills(profile: Profile) -> tuple[Skill, ...]:
    return load_profile_skills(profile.source.parent, profile.skills)


@pytest.fixture(scope="module")
def generic(skills: tuple[Skill, ...]) -> Skill:
    return next(item for item in skills if item.name == "decompose-work")


# --- carga -----------------------------------------------------------------


def test_the_profile_declares_generic_and_specialised(profile: Profile) -> None:
    # La version concreta no se fija aqui: sube cada vez que el perfil cambia.
    assert profile.version.split(".")[0] == "1"
    assert profile.skills == ("decompose-work", *SPECIALISED)


def test_every_skill_loads_and_supports_card_mode(skills: tuple[Skill, ...]) -> None:
    assert len(skills) == 4
    for skill in skills:
        assert "card" in skill.modes, skill.name
        assert skill.instructions.strip(), skill.name


# --- contrato compartido ---------------------------------------------------


def test_every_decomposition_skill_declares_the_same_contract(
    skills: tuple[Skill, ...], generic: Skill
) -> None:
    """Las cuatro prometen `work-breakdown` v1, y ahora lo declaran.

    Antes solo lo declaraba la generica, y las tres especializadas lo cumplian
    de rebote: el ejecutor manda todas las skills del perfil, asi que siempre
    viajaba una que lo declaraba. Separada del grupo, cualquiera de ellas habria
    escrito su artefacto **sin que nadie comprobara nada**.

    Se declaraba una sola porque el esquema iba dentro del front matter y cuatro
    copias en el prompt salieron caras contra el broker real. Desde A06 el
    esquema no va en el front matter: se cita, vive una vez en CONTRACTS/ y
    llega al modelo una sola vez, como ejemplo relleno al final del prompt.
    """
    assert (generic.output_contract, generic.output_contract_version) == (
        "work-breakdown",
        1,
    )
    for skill in skills:
        assert skill.declares_output_contract is True, skill.name
        assert skill.output_contract == "work-breakdown", skill.name
        assert skill.output_contract_version == 1, skill.name


def test_the_four_share_one_schema_not_four_copies(
    skills: tuple[Skill, ...], generic: Skill
) -> None:
    """Mismo objeto, no copias iguales: no puede haber dos formas del documento."""
    for skill in skills:
        assert skill.output_schema is generic.output_schema, skill.name


def test_the_shared_contract_still_applies_to_the_whole_profile(
    skills: tuple[Skill, ...], generic: Skill
) -> None:
    """Aunque solo una lo declare, manda para todo el perfil."""
    policy = apply_skill_output_contract(BrokerPolicy(), skills)

    assert policy.output_schema == generic.output_schema


def test_a_shared_contract_reaches_the_prompt(
    profile: Profile, skills: tuple[Skill, ...], generic: Skill
) -> None:
    policy = apply_skill_output_contract(BrokerPolicy(), skills)

    assert policy.output_schema == generic.output_schema

    request = build_broker_request(
        profile,
        skills,
        Card.create(function="decompose", request="break down work", origin="test"),
        policy=policy,
        idempotency_key="test-key",
    )
    prompt = request["content"]["prompt"]
    assert "«work-breakdown» version 1" in prompt
    # Los cuatro procedimientos viajan; cada uno dice cuando se aplica.
    for skill in skills:
        assert skill.name in prompt
    # Lo que se le ensena al modelo es un **ejemplo relleno**, no el esquema:
    # con el JSON Schema delante devolvia el esquema en vez de una instancia.
    # Se comprobo contra el broker real.
    assert prompt.count('"total_estimated_minutes"') == 1
    assert '"contract": "work-breakdown"' in prompt
    assert "no el esquema que lo describe" in prompt
    assert prompt.index("FORMATO OBLIGATORIO") > prompt.index("decompose-software")


# --- lo que exige la fase --------------------------------------------------


def test_a_step_carries_dependencies_effort_criteria_and_optionality(
    generic: Skill,
) -> None:
    step = generic.output_schema["properties"]["steps"]["items"]

    assert step["required"] == [
        "order",
        "title",
        "completion_criteria",
        "estimated_minutes",
        "optional",
        "depends_on",
    ]
    assert step["properties"]["optional"]["type"] == "boolean"
    assert step["properties"]["depends_on"]["items"]["type"] == "integer"


def test_effort_may_be_unknown_but_never_invented(generic: Skill) -> None:
    """`null` es una respuesta valida; un numero inventado no lo seria."""
    step = generic.output_schema["properties"]["steps"]["items"]["properties"]

    assert step["estimated_minutes"]["type"] == ["integer", "null"]
    assert "no hay con que estimar" in step["estimated_minutes"]["description"]
    assert "peor que un hueco" in generic.instructions


def test_the_schema_has_no_room_for_a_date(generic: Skill) -> None:
    step_keys = set(
        generic.output_schema["properties"]["steps"]["items"]["properties"]
    )
    top_keys = set(generic.output_schema["properties"])

    assert generic.output_schema["additionalProperties"] is False
    assert generic.output_schema["properties"]["steps"]["items"][
        "additionalProperties"
    ] is False
    for forbidden in ("date", "day", "start_at", "scheduled_at", "deadline", "due"):
        assert not any(forbidden in key for key in step_keys), forbidden
        assert not any(forbidden in key for key in top_keys), forbidden


def test_every_procedure_says_it_never_schedules(skills: tuple[Skill, ...]) -> None:
    for skill in skills:
        body = skill.instructions.lower()
        assert "fecha" in body, skill.name
        assert "no agenda" in body or "no pone fechas" in body, skill.name


def test_a_scheduling_demand_does_not_kill_the_whole_card(
    profile: Profile, generic: Skill
) -> None:
    """Negarse a todo deja al usuario sin lo que si se podia hacer.

    Contra el broker real el modelo respondio «I'm sorry, but I can't provide a
    schedule» y no entrego nada. El limite estaba bien; la reaccion, no.
    """
    for body in (profile.body.lower(), generic.instructions.lower()):
        assert "rechaces el encargo entero" in body
        assert "planificador del cliente" in body
    assert "warnings" in generic.instructions.lower()


def test_the_generic_procedure_demands_verifiable_steps(generic: Skill) -> None:
    body = generic.instructions.lower()

    assert "criterio de terminado" in body
    assert "observable" in body
    assert "no repite el encargo como paso" in body


# --- especializacion sin acoplarse -----------------------------------------


@pytest.mark.parametrize("name", SPECIALISED)
def test_each_specialisation_says_when_it_applies(
    skills: tuple[Skill, ...], name: str
) -> None:
    skill = next(item for item in skills if item.name == name)
    body = skill.instructions.lower()

    assert "cuando se aplica" in body
    # No sustituye al procedimiento generico: lo complementa.
    assert "ademas del procedimiento generico" in body


@pytest.mark.parametrize(
    ("name", "marker"),
    [
        ("decompose-course", "evaluacion"),
        ("decompose-study", "como se sabe que ya se sabe"),
        ("decompose-software", "comprobacion ejecutable"),
    ],
)
def test_each_specialisation_brings_its_own_pitfall(
    skills: tuple[Skill, ...], name: str, marker: str
) -> None:
    skill = next(item for item in skills if item.name == name)

    assert marker in skill.instructions.lower()


def test_the_kind_field_is_optional_and_closed(generic: Skill) -> None:
    """Se puede declarar el tipo reconocido, pero no inventarse uno."""
    kind = generic.output_schema["properties"]["kind"]

    assert "kind" not in generic.output_schema["required"]
    assert kind["enum"] == ["generic", "course", "study", "software"]


# --- reutilizable ----------------------------------------------------------


def test_no_skill_names_a_client_application(skills: tuple[Skill, ...]) -> None:
    for skill in skills:
        text = (skill.description + skill.instructions).lower()
        for forbidden in ("gestion de tareas", "gestion-tareas", "pomodoro", "sqlite"):
            assert forbidden not in text, skill.name
