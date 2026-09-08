"""Skill `adaptive-task-interview`: contrato de salida y limites.

Lo que se protege aqui es que la skill **promete una forma** y que esa promesa
llega hasta el broker, no solo hasta el texto del prompt.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agora.broker.contracts import BrokerPolicy
from agora.broker.executor import apply_skill_output_contract
from agora.broker.request_builder import build_broker_request
from agora.cards import Card
from agora.errors import SkillFormatError
from agora.profiles import Profile
from agora.skills import Skill, load_profile_skills

PROFILE_ROOT = (
    Path(__file__).resolve().parents[1] / "profiles" / "task-planning" / "task-intake"
)
SKILL_PATH = PROFILE_ROOT / "skills" / "adaptive-task-interview" / "SKILL.md"


@pytest.fixture(scope="module")
def skill() -> Skill:
    """Cargada como la carga el ejecutor: con su cita al registro resuelta."""
    return load_profile_skills(PROFILE_ROOT, ("adaptive-task-interview",))[0]


@pytest.fixture(scope="module")
def profile() -> Profile:
    return Profile.load(PROFILE_ROOT / "PROFILE.md")


# --- carga -----------------------------------------------------------------


def test_the_skill_loads_and_supports_card_mode(skill: Skill) -> None:
    assert skill.name == "adaptive-task-interview"
    assert skill.version == "1.0.0"
    assert "card" in skill.modes
    assert "conversation" in skill.modes
    assert skill.instructions.strip()


def test_the_profile_declares_it_and_it_resolves(profile: Profile) -> None:
    assert profile.skills == ("adaptive-task-interview",)

    loaded = load_profile_skills(profile.source.parent, profile.skills)

    assert [item.name for item in loaded] == ["adaptive-task-interview"]


# --- contrato de salida ----------------------------------------------------


def test_the_output_contract_is_named_and_versioned(skill: Skill) -> None:
    assert skill.declares_output_contract is True
    assert skill.output_contract == "task-brief"
    assert skill.output_contract_version == 1


def test_the_schema_pins_the_contract_inside_the_payload(skill: Skill) -> None:
    """El propio JSON dice que contrato es: quien lo lea no tiene que adivinar."""
    properties = skill.output_schema["properties"]

    assert properties["contract"]["const"] == "task-brief"
    assert properties["contract_version"]["const"] == 1
    assert skill.output_schema["additionalProperties"] is False


def test_the_schema_demands_evidence_and_impact(skill: Skill) -> None:
    """No basta con afirmar: cada afirmacion trae de donde sale."""
    properties = skill.output_schema["properties"]

    assert properties["understood"]["items"]["required"] == ["statement", "evidence"]
    assert properties["assumptions"]["items"]["required"] == [
        "statement",
        "impact_if_wrong",
    ]
    assert properties["questions"]["items"]["required"] == [
        "question",
        "why_it_matters",
    ]


def test_the_schema_has_no_room_for_a_calendar(skill: Skill) -> None:
    """`additionalProperties: false` mas estas claves: no cabe una fecha."""
    keys = set(skill.output_schema["properties"])

    assert keys == {
        "contract",
        "contract_version",
        "summary",
        "understood",
        "assumptions",
        "questions",
        "confidence",
    }


def test_the_procedure_states_what_it_never_does(skill: Skill) -> None:
    body = skill.instructions.lower()

    assert "no descompone" in body
    assert "no estima tiempo" in body
    assert "no agenda" in body
    assert "calendario" in body and "cliente" in body


# --- la promesa llega al broker --------------------------------------------


def test_a_declared_contract_travels_in_the_policy(skill: Skill) -> None:
    """El esquema se conserva; el formato del broker NO se cambia.

    Se comprobo contra el broker real: pedir `output.format: json` no impone el
    esquema y ademas revienta cuando enruta a lmstudio, con un error no
    reintentable. El contrato lo hace cumplir Agora.
    """
    policy = BrokerPolicy()
    assert policy.output_format == "markdown"

    applied = apply_skill_output_contract(policy, (skill,))

    assert applied.output_schema == skill.output_schema
    assert applied.output_format == "markdown"


def test_a_skill_without_a_contract_changes_nothing() -> None:
    """Compatibilidad hacia atras: las skills de siempre siguen igual."""
    plain = Skill(
        name="plain",
        version="1.0.0",
        description="sin contrato",
        modes=("card",),
        metadata={},
        instructions="haz algo",
        source=Path("plain/SKILL.md"),
    )
    policy = BrokerPolicy(output_format="markdown")

    assert apply_skill_output_contract(policy, ()) is policy
    assert apply_skill_output_contract(policy, (plain,)) is policy


def test_two_skills_may_share_the_same_contract(skill: Skill) -> None:
    """Una generica y su especializacion producen el mismo documento."""
    twin = replace(skill, name="specialised-interview")

    applied = apply_skill_output_contract(BrokerPolicy(), (skill, twin))

    assert applied.output_schema == skill.output_schema
    assert applied.output_format == "markdown"


def test_two_different_contracts_are_an_error_not_a_silent_choice(
    skill: Skill,
) -> None:
    other = replace(skill, name="other", output_contract="something-else")

    with pytest.raises(ValueError, match="conflicting output contracts"):
        apply_skill_output_contract(BrokerPolicy(), (skill, other))


def test_the_request_demands_the_contract_in_the_prompt(
    profile: Profile, skill: Skill
) -> None:
    """La exigencia va al final del prompt, que es lo ultimo que lee el modelo.

    No se manda `json_schema` en `output`: el broker no lo impone y rompe con
    algunos proveedores. Lo que si funciona es pedirlo con claridad y validar
    la respuesta despues.
    """
    policy = apply_skill_output_contract(BrokerPolicy(), (skill,))
    card = Card.create(
        function="understand",
        request="understand request about the annexe",
        origin="test",
    )

    request = build_broker_request(
        profile, (skill,), card, policy=policy, idempotency_key="test-key"
    )

    assert "json_schema" not in request["output"]
    prompt = request["content"]["prompt"]
    assert prompt.rstrip().endswith("vez de inventarlo.")
    assert "FORMATO OBLIGATORIO" in prompt
    assert "«task-brief» version 1" in prompt
    # El esquema y el procedimiento viajan dentro del contrato de la SKILL.
    assert "adaptive-task-interview" in prompt
    assert "Preguntar solo lo material" in prompt
    assert "output_schema" in prompt


# --- validacion del formato de la skill ------------------------------------


def test_a_contract_without_a_version_cannot_be_cited(tmp_path: Path) -> None:
    """Desde A06 el esquema puede faltar —se cita el registro— pero el nombre y
    la version no: sin los dos no hay forma de saber que se esta prometiendo."""
    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\n"
        "name: media\n"
        "version: 1.0.0\n"
        "description: contrato a medias\n"
        "output_contract: algo\n"
        "---\n\n"
        "cuerpo\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillFormatError, match="output_contract_version"):
        Skill.load(path)


def test_a_schema_without_a_name_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\n"
        "name: suelta\n"
        "version: 1.0.0\n"
        "description: esquema suelto\n"
        "output_schema:\n"
        "  type: object\n"
        "---\n\n"
        "cuerpo\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillFormatError, match="output_contract"):
        Skill.load(path)


@pytest.mark.parametrize(
    ("version_line", "message"),
    [
        ("output_contract_version: 0", "positive integer"),
        ("output_contract_version: uno", "positive integer"),
    ],
)
def test_an_invalid_contract_version_is_rejected(
    tmp_path: Path, version_line: str, message: str
) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\n"
        "name: mala\n"
        "version: 1.0.0\n"
        "description: version mala\n"
        "output_contract: algo\n"
        f"{version_line}\n"
        "output_schema:\n"
        "  type: object\n"
        "---\n\n"
        "cuerpo\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillFormatError, match=message):
        Skill.load(path)


def test_an_empty_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\n"
        "name: vacia\n"
        "version: 1.0.0\n"
        "description: esquema vacio\n"
        "output_contract: algo\n"
        "output_contract_version: 1\n"
        "output_schema: {}\n"
        "---\n\n"
        "cuerpo\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillFormatError, match="non-empty mapping"):
        Skill.load(path)


# --- generica, no de un cliente concreto -----------------------------------


def test_the_skill_names_no_client_application(skill: Skill) -> None:
    text = (skill.description + skill.instructions).lower()

    for forbidden in ("gestion de tareas", "gestion-tareas", "pomodoro", "sqlite"):
        assert forbidden not in text
