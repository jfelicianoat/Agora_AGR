"""`advise-plan`: opina sobre el plan, pero no lo toca ni mira un calendario.

Las dos lineas que esta fase no puede cruzar son finas y conviene tenerlas
escritas: **comentar una estimacion no es hacerla**, y **leer una ambicion no
es calcular un hueco**. Lo que sigue comprueba que el contrato las sostiene sin
depender de que el modelo se porte bien.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from agora.broker.contracts import BrokerPolicy
from agora.broker.executor import apply_skill_output_contract
from agora.broker.request_builder import build_broker_request
from agora.cards import Card
from agora.output_contract import OutputContractError, enforce, validate_payload
from agora.profiles import Profile
from agora.skills import Skill, load_profile_skills

PROFILE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "profiles"
    / "task-planning"
    / "planning-advisor"
)

JUDGEMENTS = ("estimate_comments", "coherence_issues")


@pytest.fixture(scope="module")
def profile() -> Profile:
    return Profile.load(PROFILE_ROOT / "PROFILE.md")


@pytest.fixture(scope="module")
def skill(profile: Profile) -> Skill:
    return load_profile_skills(profile.source.parent, profile.skills)[0]


@pytest.fixture
def payload(skill: Skill) -> dict[str, Any]:
    return copy.deepcopy(skill.output_example)


def check(skill: Skill, payload: Any) -> Any:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return enforce(skill.output_schema, text)


# --- El perfil ---------------------------------------------------------------


def test_profile_declares_the_skill(profile: Profile) -> None:
    assert profile.skills == ("advise-plan",)


def test_profile_promises_not_to_compute_gaps(profile: Profile) -> None:
    guarantees = " ".join(profile.metadata["guarantees"]).lower()
    assert "no calcula huecos" in guarantees


def test_profile_still_refuses_the_calendar(profile: Profile) -> None:
    refuses = profile.metadata["refuses"]
    for forbidden in ("schedule", "assign days", "assign hours"):
        assert forbidden in refuses


def test_profile_separates_commenting_an_estimate_from_making_one(
    profile: Profile,
) -> None:
    """La linea fina de A05, escrita donde se pueda leer."""
    body = profile.body.lower()
    assert "comentar una estimacion no es hacerla" in body
    assert "leer una ambicion no es calcular un hueco" in body


def test_profile_delivers_instead_of_rejecting_when_asked_to_schedule(
    profile: Profile,
) -> None:
    body = profile.body.lower()
    assert "entrega igualmente el consejo" in body
    assert "tampoco lo agendes" in body


# --- El contrato -------------------------------------------------------------


def test_skill_declares_a_versioned_contract(skill: Skill) -> None:
    assert skill.declares_output_contract
    assert skill.output_contract == "plan-advice"
    assert skill.output_contract_version == 1


def test_example_satisfies_its_own_schema(skill: Skill) -> None:
    assert validate_payload(skill.output_schema, skill.output_example) == []


def test_every_section_is_closed_to_extra_fields(skill: Skill) -> None:
    assert skill.output_schema["additionalProperties"] is False
    for section in skill.output_schema["properties"].values():
        if section.get("type") == "array" and section["items"].get("type") == "object":
            assert section["items"]["additionalProperties"] is False
        if section.get("type") == "object":
            assert section["additionalProperties"] is False


# --- Ni calendario ni huecos -------------------------------------------------


def test_the_contract_has_nowhere_to_write_a_calendar(skill: Skill) -> None:
    rendered = repr(skill.output_schema).lower()
    for forbidden in (
        "date",
        "day",
        "hour",
        "slot",
        "schedule",
        "deadline",
        "block",
        "availab",
        "calendar",
    ):
        assert forbidden not in rendered


def test_the_verdict_cannot_say_it_fits(skill: Skill) -> None:
    """«Cabe» o «no cabe» exige un calendario real, y aqui no hay ninguno."""
    verdict = skill.output_schema["properties"]["ambition"]["properties"]["verdict"]
    assert verdict["enum"] == ["looks_ambitious", "looks_reasonable", "cannot_tell"]


def test_an_invented_verdict_is_rejected(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["ambition"]["verdict"] = "does_not_fit"
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_ambition_cannot_carry_arithmetic(
    skill: Skill, payload: dict[str, Any]
) -> None:
    """Sumar minutos contra horas libres es calcular un hueco."""
    payload["ambition"]["total_effort_minutes"] = 480
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_ambition_rests_on_what_the_user_said(skill: Skill) -> None:
    basis = skill.output_schema["properties"]["ambition"]["properties"][
        "capacity_basis"
    ]
    assert basis["type"] == ["string", "null"]
    assert "cannot_tell" in skill.instructions


def test_with_no_declared_capacity_the_answer_is_that_it_cannot_be_told(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["ambition"] = {
        "verdict": "cannot_tell",
        "capacity_basis": None,
        "why": "El encargo no dice de cuanto tiempo dispone el usuario.",
        "requires_confirmation": True,
    }
    assert check(skill, payload) == payload


def test_the_skill_forbids_the_arithmetic_in_words_too(skill: Skill) -> None:
    body = skill.instructions.lower()
    assert "no sumes minutos contra horas disponibles" in body
    assert "no hay cuentas que hacer aqui" in body


# --- No altera el plan -------------------------------------------------------


def test_the_document_declares_itself_advice(skill: Skill) -> None:
    assert skill.output_schema["properties"]["advice_only"] == {
        "const": True,
        **{
            k: v
            for k, v in skill.output_schema["properties"]["advice_only"].items()
            if k != "const"
        },
    }
    assert "advice_only" in skill.output_schema["required"]


def test_advice_that_calls_itself_binding_is_rejected(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["advice_only"] = False
    with pytest.raises(OutputContractError):
        check(skill, payload)


@pytest.mark.parametrize("section", JUDGEMENTS)
def test_a_judgement_always_requires_confirmation(skill: Skill, section: str) -> None:
    items = skill.output_schema["properties"][section]["items"]
    assert items["properties"]["requires_confirmation"]["const"] is True
    assert "requires_confirmation" in items["required"]


@pytest.mark.parametrize("section", JUDGEMENTS)
def test_a_judgement_that_confirms_itself_is_rejected(
    skill: Skill, payload: dict[str, Any], section: str
) -> None:
    payload[section][0]["requires_confirmation"] = False
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_there_is_nowhere_to_return_a_corrected_plan(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["revised_plan"] = [{"item": "Ensayar", "position": 1}]
    with pytest.raises(OutputContractError):
        check(skill, payload)


# --- Comentar una estimacion no es hacerla -----------------------------------


def test_a_comment_says_which_way_it_is_off_never_a_number(skill: Skill) -> None:
    """Estimar es de la capacidad de descomposicion, no de esta."""
    properties = skill.output_schema["properties"]["estimate_comments"]["items"][
        "properties"
    ]
    assert properties["direction"]["enum"] == ["looks_short", "looks_long", "unclear"]
    assert not any("minutes" in name for name in properties)


def test_an_estimate_of_its_own_is_rejected(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["estimate_comments"][0]["suggested_minutes"] = 180
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_not_being_able_to_judge_is_a_valid_comment(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["estimate_comments"][0]["direction"] = "unclear"
    assert check(skill, payload) == payload


# --- Lo demas ----------------------------------------------------------------


def test_every_position_in_the_sequence_carries_its_reason(skill: Skill) -> None:
    items = skill.output_schema["properties"]["sequence"]["items"]
    assert "why" in items["required"]
    for entry in skill.output_example["sequence"]:
        assert entry["why"].strip()


def test_an_order_without_a_reason_is_rejected(
    skill: Skill, payload: dict[str, Any]
) -> None:
    del payload["sequence"][0]["why"]
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_a_risk_says_how_much_it_hurts(skill: Skill) -> None:
    severity = skill.output_schema["properties"]["risks"]["items"]["properties"][
        "severity"
    ]
    assert severity["enum"] == ["low", "medium", "high"]


def test_the_example_says_out_loud_that_it_does_not_schedule(skill: Skill) -> None:
    assert any("no agenda" in w.lower() for w in skill.output_example["warnings"])


def test_a_plan_with_nothing_to_object_is_a_valid_answer(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["estimate_comments"] = []
    payload["coherence_issues"] = []
    payload["risks"] = []
    payload["questions"] = []
    assert check(skill, payload) == payload


def test_the_document_declares_which_contract_it_is(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["contract"] = "review-analysis"
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_json_wrapped_in_prose_is_still_accepted(
    skill: Skill, payload: dict[str, Any]
) -> None:
    noisy = "Aqui va el consejo:\n```json\n" + json.dumps(payload) + "\n```\n"
    assert check(skill, noisy) == payload


# --- Como viaja al broker ----------------------------------------------------


def test_the_profile_demands_a_capable_model(profile: Profile) -> None:
    assert profile.metadata["model_capacity"] == "maximum"


def test_the_policy_carries_the_schema_but_not_the_json_mode(skill: Skill) -> None:
    policy = apply_skill_output_contract(BrokerPolicy(), (skill,))
    assert policy.output_schema == skill.output_schema
    assert policy.output_format != "json"


def test_the_prompt_ends_with_a_filled_example_not_the_schema(
    profile: Profile, skill: Skill, tmp_path: Path
) -> None:
    card = tmp_path / "consejo.md"
    card.write_text(
        "---\n"
        "function: advise\n"
        "request: aconsejame sobre el orden de estos encargos\n"
        "created: 2026-09-08T00:00:00Z\n"
        "origin: test\n"
        "paths: []\n"
        "attempts: 0\n"
        "---\n\nTres encargos para la charla.\n",
        encoding="utf-8",
    )
    policy = apply_skill_output_contract(BrokerPolicy(), (skill,))
    request = build_broker_request(
        profile,
        (skill,),
        Card.load(card),
        policy=policy,
        idempotency_key="k",
    )
    closing = request["content"]["prompt"].rsplit("FORMATO OBLIGATORIO", 1)[1]
    assert "plan-advice" in closing
    assert "Cerrar el guion de la charla" in closing
    assert "additionalProperties" not in closing
    assert "json_schema" not in request["output"]
