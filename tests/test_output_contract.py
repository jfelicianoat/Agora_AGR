"""Hacer cumplir el contrato de salida cuando el broker no lo impone.

La respuesta que usan varias de estas pruebas es **la que devolvio el broker
real** (contrato 2.10) cuando se le mando un `json_schema`: un bloque Markdown
con un JSON que no seguia el esquema. Es el caso que motiva el modulo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agora.broker.contracts import BrokerPolicy
from agora.broker.executor import BrokerPolicyViolation, _enforce_output_contract
from agora.output_contract import (
    OutputContractError,
    enforce,
    extract_json,
    validate_payload,
)
from agora.errors import SkillFormatError
from agora.skills import Skill

from agora.contracts import ContractRegistry

PROFILES_ROOT = Path(__file__).resolve().parents[1] / "profiles" / "task-planning"
REGISTRY = ContractRegistry.discover(PROFILES_ROOT)
assert REGISTRY is not None, "el registro de contratos tiene que estar"

# Desde A06 el esquema vive en CONTRACTS/, no dentro de la SKILL.
BREAKDOWN_SCHEMA = REGISTRY.get("work-breakdown", 1).schema

#: Literalmente lo que devolvio el broker real en la verificacion de A03.
BROKER_ECHO = """```json
{
  "break_down_work": "preparar una charla de 20 minutos",
  "created": "2026-09-08T10:29:47Z",
  "origin": "a03-check",
  "paths": [],
  "attempts": 0,
  "max_attempts": 3,
  "priority": "normal",
  "body": ""
}
```"""


def _valid_breakdown() -> dict:
    return {
        "contract": "work-breakdown",
        "contract_version": 1,
        "summary": "La charla esta preparada y ensayada.",
        "steps": [
            {
                "order": 1,
                "title": "Definir el mensaje principal",
                "completion_criteria": "Una frase escrita que resume la charla.",
                "estimated_minutes": 30,
                "optional": False,
                "depends_on": [],
            },
            {
                "order": 2,
                "title": "Montar las diapositivas",
                "completion_criteria": "Diapositivas completas y revisadas.",
                "estimated_minutes": 90,
                "optional": False,
                "depends_on": [1],
            },
        ],
        "total_estimated_minutes": 120,
        "confidence": 0.8,
    }


# --- extraccion ------------------------------------------------------------


def test_plain_json_is_read_as_is() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}


def test_a_fenced_block_is_unwrapped() -> None:
    """Los modelos envuelven el JSON constantemente; exigir lo contrario seria irreal."""
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_a_sentence_before_the_block_does_not_break_it() -> None:
    text = 'Aqui tienes el resultado:\n\n```json\n{"a": 1}\n```\n\nEspero que sirva.'

    assert extract_json(text) == {"a": 1}


def test_a_bare_object_inside_prose_is_recovered() -> None:
    assert extract_json('Resultado: {"a": 1} y ya.') == {"a": 1}


def test_text_without_json_is_rejected() -> None:
    with pytest.raises(OutputContractError, match="ningun documento JSON"):
        extract_json("Aqui tienes un CSV,order,title\n1,algo")


def test_broken_json_inside_a_block_is_reported() -> None:
    with pytest.raises(OutputContractError, match="no contiene JSON valido"):
        extract_json('```json\n{"a": }\n```')


# --- validacion ------------------------------------------------------------


def test_a_correct_document_has_no_problems() -> None:
    assert validate_payload(BREAKDOWN_SCHEMA, _valid_breakdown()) == []


def test_the_real_broker_echo_is_caught() -> None:
    """Esta es la respuesta que motivo el modulo entero."""
    payload = extract_json(BROKER_ECHO)

    problems = validate_payload(BREAKDOWN_SCHEMA, payload)

    assert problems
    joined = "; ".join(problems)
    assert "falta el campo requerido 'contract'" in joined
    assert "falta el campo requerido 'steps'" in joined
    assert "campos no declarados" in joined


def test_a_missing_required_field_is_reported() -> None:
    payload = _valid_breakdown()
    del payload["summary"]

    assert "falta el campo requerido 'summary'" in "; ".join(
        validate_payload(BREAKDOWN_SCHEMA, payload)
    )


def test_an_undeclared_field_is_reported() -> None:
    payload = _valid_breakdown()
    payload["scheduled_for"] = "2026-09-08"

    problems = "; ".join(validate_payload(BREAKDOWN_SCHEMA, payload))

    assert "campos no declarados: scheduled_for" in problems


def test_a_wrong_constant_is_reported() -> None:
    payload = _valid_breakdown()
    payload["contract_version"] = 2

    assert "deberia ser 1" in "; ".join(validate_payload(BREAKDOWN_SCHEMA, payload))


def test_a_nested_problem_names_its_path() -> None:
    payload = _valid_breakdown()
    del payload["steps"][1]["completion_criteria"]

    problems = "; ".join(validate_payload(BREAKDOWN_SCHEMA, payload))

    assert "steps[1]: falta el campo requerido 'completion_criteria'" in problems


def test_a_null_estimate_is_accepted_because_the_schema_allows_it() -> None:
    """`null` es una respuesta honesta; el esquema declara el tipo union."""
    payload = _valid_breakdown()
    payload["steps"][0]["estimated_minutes"] = None
    payload["total_estimated_minutes"] = None

    assert validate_payload(BREAKDOWN_SCHEMA, payload) == []


def test_a_wrong_type_is_reported() -> None:
    payload = _valid_breakdown()
    payload["steps"] = "dos pasos"

    assert "se esperaba array" in "; ".join(validate_payload(BREAKDOWN_SCHEMA, payload))


def test_a_boolean_is_not_a_number() -> None:
    payload = _valid_breakdown()
    payload["confidence"] = True

    assert "se esperaba number" in "; ".join(validate_payload(BREAKDOWN_SCHEMA, payload))


def test_a_value_out_of_range_is_reported() -> None:
    payload = _valid_breakdown()
    payload["confidence"] = 1.5

    assert "mayor que 1" in "; ".join(validate_payload(BREAKDOWN_SCHEMA, payload))


def test_an_enum_outside_its_values_is_reported() -> None:
    payload = _valid_breakdown()
    payload["kind"] = "cooking"

    assert "no esta entre" in "; ".join(validate_payload(BREAKDOWN_SCHEMA, payload))


def test_an_unknown_keyword_is_ignored_not_invented() -> None:
    """Lo que este validador no entiende no lo rechaza."""
    schema = {"type": "object", "patternProperties": {"^x": {}}}

    assert validate_payload(schema, {"xa": 1}) == []


# --- el ejecutor lo hace cumplir -------------------------------------------


def test_a_valid_deliverable_is_normalised_to_clean_json() -> None:
    """El artefacto deja de venir envuelto en Markdown."""
    body = f"```json\n{json.dumps(_valid_breakdown())}\n```"
    artifacts = {"broker-result.json": body.encode("utf-8")}
    deliverable = {"name": "broker-result.json", "sha256": "antiguo"}
    policy = BrokerPolicy(output_format="json", output_schema=BREAKDOWN_SCHEMA)

    result = _enforce_output_contract(artifacts, deliverable, policy)

    written = json.loads(result["broker-result.json"].decode("utf-8"))
    assert written == _valid_breakdown()
    assert not result["broker-result.json"].decode("utf-8").startswith("```")
    assert deliverable["sha256"] != "antiguo"


def test_a_deliverable_that_breaks_the_contract_fails_the_card() -> None:
    artifacts = {"broker-result.json": BROKER_ECHO.encode("utf-8")}
    deliverable = {"name": "broker-result.json"}
    policy = BrokerPolicy(output_format="json", output_schema=BREAKDOWN_SCHEMA)

    with pytest.raises(BrokerPolicyViolation, match="no cumple el contrato"):
        _enforce_output_contract(artifacts, deliverable, policy)


def test_a_deliverable_without_json_at_all_fails_the_card() -> None:
    """Lo que devolvio el broker la primera vez fue un CSV."""
    artifacts = {"broker-result.json": b"order,title\n1,algo\n"}
    deliverable = {"name": "broker-result.json"}
    policy = BrokerPolicy(output_format="json", output_schema=BREAKDOWN_SCHEMA)

    with pytest.raises(BrokerPolicyViolation, match="ningun documento JSON"):
        _enforce_output_contract(artifacts, deliverable, policy)


def test_without_a_declared_schema_nothing_is_touched() -> None:
    """Compatibilidad hacia atras: las tarjetas de siempre siguen igual."""
    artifacts = {"broker-result.md": b"# Un informe en Markdown"}
    deliverable = {"name": "broker-result.md"}

    result = _enforce_output_contract(artifacts, deliverable, BrokerPolicy())

    assert result is artifacts


def test_a_missing_deliverable_is_left_alone() -> None:
    policy = BrokerPolicy(output_format="json", output_schema=BREAKDOWN_SCHEMA)
    artifacts = {"otro.json": b"{}"}

    assert _enforce_output_contract(artifacts, {"name": "falta.json"}, policy) is artifacts


def test_enforce_returns_the_parsed_document() -> None:
    body = f"```json\n{json.dumps(_valid_breakdown())}\n```"

    assert enforce(BREAKDOWN_SCHEMA, body) == _valid_breakdown()


# --- el ejemplo que se le ensena al modelo ---------------------------------


def test_every_registered_contract_example_satisfies_its_schema() -> None:
    """Un ejemplo que no cumple el contrato enseñaria a incumplirlo.

    Es la prueba mas barata que existe contra el error mas caro: el modelo
    copia el ejemplo, asi que el ejemplo tiene que ser correcto.
    """
    catalogue = REGISTRY.catalogue()
    assert len(catalogue) >= 4
    for contract in catalogue:
        assert validate_payload(contract.schema, contract.example) == [], contract.reference


def test_every_skill_that_promises_a_document_can_show_one() -> None:
    """Cargadas como las carga el ejecutor: la cita resuelta, el ejemplo puesto."""
    from agora.profiles import Profile
    from agora.skills import load_profile_skills

    checked = 0
    for profile_path in sorted(PROFILES_ROOT.rglob("PROFILE.md")):
        profile = Profile.load(profile_path)
        for skill in load_profile_skills(profile_path.parent, profile.skills):
            if skill.output_contract is None:
                continue
            assert skill.output_schema is not None, skill.name
            assert skill.output_example is not None, skill.name
            assert validate_payload(skill.output_schema, skill.output_example) == [], skill.name
            checked += 1
    assert checked >= 7, f"se esperaban las siete skills con contrato, hay {checked}"


def test_an_example_without_a_contract_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\n"
        "name: suelta\n"
        "version: 1.0.0\n"
        "description: ejemplo sin contrato\n"
        "output_example:\n"
        "  a: 1\n"
        "---\n\n"
        "cuerpo\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillFormatError, match="needs the contract"):
        Skill.load(path)


# --- El tope de salida -------------------------------------------------------


def test_a_policy_without_a_contract_keeps_the_old_ceiling() -> None:
    """Lo que no declara contrato se comporta exactamente igual que antes."""
    from agora.broker.contracts import BrokerPolicy
    from agora.broker.executor import apply_skill_output_contract

    policy = BrokerPolicy()
    assert policy.max_output_tokens == 4000
    assert apply_skill_output_contract(policy, ()) is policy


def test_a_declared_contract_raises_the_ceiling() -> None:
    """Se midio: 4000 cortaba un documento con contrato a media cadena."""
    from pathlib import Path

    from agora.broker.contracts import BrokerPolicy
    from agora.broker.executor import CONTRACT_OUTPUT_TOKENS, apply_skill_output_contract
    from agora.profiles import Profile
    from agora.skills import load_profile_skills

    root = (
        Path(__file__).resolve().parents[1]
        / "profiles"
        / "task-planning"
        / "planning-advisor"
    )
    skills = tuple(load_profile_skills(root, Profile.load(root / "PROFILE.md").skills))
    policy = apply_skill_output_contract(BrokerPolicy(), skills)
    assert policy.max_output_tokens == CONTRACT_OUTPUT_TOKENS > 4000


def test_a_ceiling_someone_raised_on_purpose_is_not_lowered() -> None:
    from pathlib import Path

    from agora.broker.contracts import BrokerPolicy
    from agora.broker.executor import CONTRACT_OUTPUT_TOKENS, apply_skill_output_contract
    from agora.profiles import Profile
    from agora.skills import load_profile_skills

    root = (
        Path(__file__).resolve().parents[1]
        / "profiles"
        / "task-planning"
        / "planning-advisor"
    )
    skills = tuple(load_profile_skills(root, Profile.load(root / "PROFILE.md").skills))
    higher = CONTRACT_OUTPUT_TOKENS + 1000
    policy = apply_skill_output_contract(
        BrokerPolicy(max_output_tokens=higher), skills
    )
    assert policy.max_output_tokens == higher


def test_the_request_asks_for_what_the_policy_says() -> None:
    from pathlib import Path

    from agora.broker.contracts import BrokerPolicy
    from agora.broker.request_builder import build_broker_request
    from agora.cards import Card
    from agora.profiles import Profile

    root = (
        Path(__file__).resolve().parents[1]
        / "profiles"
        / "task-planning"
        / "planning-advisor"
    )
    card = Card.parse(
        "---\nfunction: advise\nrequest: r\ncreated: 2026-09-08T00:00:00Z\n"
        "origin: test\npaths: []\nattempts: 0\n---\ncuerpo\n"
    )
    request = build_broker_request(
        Profile.load(root / "PROFILE.md"),
        (),
        card,
        policy=BrokerPolicy(max_output_tokens=7777),
        idempotency_key="k",
    )
    assert request["generation"]["max_output_tokens"] == 7777


def test_a_ceiling_of_zero_is_not_a_ceiling() -> None:
    import pytest as _pytest

    from agora.broker.contracts import BrokerPolicy

    with _pytest.raises(ValueError, match="max_output_tokens"):
        BrokerPolicy(max_output_tokens=0)
