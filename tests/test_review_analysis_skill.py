"""`analyze-review`: lo dicho por el usuario nunca se mezcla con lo deducido.

La regla cara de esta skill es una: **nada se da por completado por deduccion**.
Aqui se comprueba que eso no depende de la buena voluntad del modelo, sino de
que el contrato no ofrece ninguna forma de expresarlo.
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
    / "review-analyzer"
)

PROPOSALS = ("inferences", "discovered_work", "proposed_dependencies")


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
    """Lo mismo que hace el ejecutor antes de escribir el artefacto."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return enforce(skill.output_schema, text)


# --- El perfil declara la capacidad -----------------------------------------


def test_profile_declares_the_skill(profile: Profile) -> None:
    assert profile.skills == ("analyze-review",)


def test_profile_promises_not_to_close_work_by_inference(profile: Profile) -> None:
    guarantees = " ".join(profile.metadata["guarantees"]).lower()
    assert "completada" in guarantees and "deduccion" in guarantees


def test_profile_still_refuses_to_schedule(profile: Profile) -> None:
    assert "schedule" in profile.metadata["refuses"]


def test_profile_delivers_instead_of_rejecting_when_asked_to_replan(
    profile: Profile,
) -> None:
    """Negarse a la tarjeta entera deja al usuario sin lo que si se podia hacer."""
    body = profile.body.lower()
    assert "entrega igualmente el analisis" in body
    assert "tampoco lo agendes" in body


# --- El contrato ------------------------------------------------------------


def test_skill_declares_a_versioned_contract(skill: Skill) -> None:
    assert skill.declares_output_contract
    assert skill.output_contract == "review-analysis"
    assert skill.output_contract_version == 1


def test_example_satisfies_its_own_schema(skill: Skill) -> None:
    assert validate_payload(skill.output_schema, skill.output_example) == []


def test_example_cites_facts_that_exist(skill: Skill) -> None:
    """`based_on` son posiciones de `facts`; el esquema no puede acotarlas."""
    facts = skill.output_example["facts"]
    citing = skill.output_example["inferences"] + skill.output_example["causes"]
    for entry in citing:
        assert entry["based_on"]
        for index in entry["based_on"]:
            assert 0 <= index < len(facts)


def test_every_fact_quotes_the_user(skill: Skill) -> None:
    for fact in skill.output_example["facts"]:
        assert fact["evidence"].strip()


def test_the_contract_knows_nothing_about_the_calendar(skill: Skill) -> None:
    """Agendar es del cliente: no hay donde escribir una fecha."""
    rendered = repr(skill.output_schema).lower()
    for forbidden in ("date", "start", "day", "hour", "slot", "schedule", "deadline"):
        assert forbidden not in rendered


# --- Nada se cierra por deduccion -------------------------------------------


def test_only_facts_can_carry_a_completion(skill: Skill) -> None:
    properties = skill.output_schema["properties"]
    assert "completed" in properties["facts"]["items"]["properties"]["kind"]["enum"]
    for section in ("inferences", "causes", *PROPOSALS):
        assert "kind" not in properties[section]["items"]["properties"]


def test_every_section_is_closed_to_extra_fields(skill: Skill) -> None:
    """Sin esto, el modelo podria inventarse un `completed: true` propio."""
    assert skill.output_schema["additionalProperties"] is False
    for section in skill.output_schema["properties"].values():
        if section.get("type") == "array" and section["items"].get("type") == "object":
            assert section["items"]["additionalProperties"] is False


@pytest.mark.parametrize("section", PROPOSALS)
def test_a_proposal_always_requires_confirmation(skill: Skill, section: str) -> None:
    items = skill.output_schema["properties"][section]["items"]
    assert items["properties"]["requires_confirmation"]["const"] is True
    assert "requires_confirmation" in items["required"]


@pytest.mark.parametrize("section", PROPOSALS)
def test_a_proposal_that_confirms_itself_is_rejected(
    skill: Skill, payload: dict[str, Any], section: str
) -> None:
    payload[section][0]["requires_confirmation"] = False
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_a_fact_without_a_quote_is_rejected(
    skill: Skill, payload: dict[str, Any]
) -> None:
    del payload["facts"][0]["evidence"]
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_an_empty_quote_is_not_a_quote(skill: Skill, payload: dict[str, Any]) -> None:
    payload["facts"][0]["evidence"] = ""
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_an_invented_completion_state_is_rejected(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["facts"][0]["kind"] = "probably_done"
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_an_inference_cannot_smuggle_a_completion(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["inferences"][0]["kind"] = "completed"
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_an_inference_must_cite_a_fact(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["inferences"][0]["based_on"] = []
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_discovered_work_estimates_effort_never_a_date(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["discovered_work"][0]["due_date"] = "2026-09-10"
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_effort_may_be_unknown(skill: Skill, payload: dict[str, Any]) -> None:
    payload["discovered_work"][0]["estimated_minutes"] = None
    assert check(skill, payload) == payload


def test_a_review_with_nothing_new_is_a_valid_answer(
    skill: Skill, payload: dict[str, Any]
) -> None:
    """Listas vacias antes que invenciones."""
    payload["inferences"] = []
    payload["causes"] = []
    payload["discovered_work"] = []
    payload["proposed_dependencies"] = []
    assert check(skill, payload) == payload


def test_the_document_declares_which_contract_it_is(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["contract"] = "work-breakdown"
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_json_wrapped_in_prose_is_still_accepted(
    skill: Skill, payload: dict[str, Any]
) -> None:
    noisy = "Aqui tienes el analisis:\n```json\n" + json.dumps(payload) + "\n```\n"
    assert check(skill, noisy) == payload


# --- Como viaja al broker ---------------------------------------------------


def test_the_policy_carries_the_schema_but_not_the_json_mode(skill: Skill) -> None:
    """El modo json del broker rompe con LM Studio y no lo impone nadie."""
    policy = apply_skill_output_contract(BrokerPolicy(), (skill,))
    assert policy.output_schema == skill.output_schema
    assert policy.output_format != "json"


def test_the_prompt_ends_with_a_filled_example_not_the_schema(
    profile: Profile, skill: Skill, tmp_path: Path
) -> None:
    card = tmp_path / "revision.md"
    card.write_text(
        "---\n"
        "function: analyze\n"
        "request: analiza mi revision de la semana\n"
        "created: 2026-09-08T00:00:00Z\n"
        "origin: test\n"
        "paths: []\n"
        "attempts: 0\n"
        "---\n\n"
        "El indice ya esta hecho.\n",
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
    prompt = request["content"]["prompt"]
    closing = prompt.rsplit("FORMATO OBLIGATORIO", 1)[1]
    assert "review-analysis" in closing
    assert "sigo esperando al director" in closing
    assert "additionalProperties" not in closing
    assert "json_schema" not in request["output"]


# --- Lo que no se puede hacer, se dice ---------------------------------------


def test_the_example_says_out_loud_that_it_does_not_schedule(skill: Skill) -> None:
    """Sin `warnings`, la parte de la peticion que no se atiende se pierde."""
    assert any("agenda" in w.lower() for w in skill.output_example["warnings"])


def test_warnings_are_optional(skill: Skill, payload: dict[str, Any]) -> None:
    del payload["warnings"]
    assert check(skill, payload) == payload


def test_an_empty_warning_is_not_a_warning(
    skill: Skill, payload: dict[str, Any]
) -> None:
    payload["warnings"] = [""]
    with pytest.raises(OutputContractError):
        check(skill, payload)


def test_silence_is_reported_but_never_turned_into_a_fact(skill: Skill) -> None:
    """Lo previsto que el usuario no menciona se avisa; no se clasifica."""
    body = skill.instructions.lower()
    assert "no la conviertas en un hecho" in body
    assert "el silencio no es" in body


# --- El modelo tiene que ser capaz de seguir un contrato ---------------------


def test_the_profile_demands_a_capable_model(profile: Profile) -> None:
    """Con enrutado libre la tarjeta puede morir sin reintento: PROMPT_ECHOED."""
    assert profile.metadata["model_capacity"] == "maximum"


def test_that_capacity_pins_a_model_and_keeps_the_review_private() -> None:
    from agora.models import ModelCatalog, profile_capacity

    catalog = ModelCatalog.load(
        Path(__file__).resolve().parents[1]
        / "examples"
        / "f0-demo"
        / "AGENTS"
        / "models.yml"
    )
    profile = Profile.load(PROFILE_ROOT / "PROFILE.md")
    policy = catalog.policy_for(profile_capacity(profile.metadata), BrokerPolicy())
    assert policy.target_model is not None
    assert policy.determinism == "strict"
    assert policy.data_classification == "confidential"
