"""A13: la frontera, comprobada en vez de acordada.

**Agora ejecuta trabajo de IA; el cliente es dueno de sus tareas, su
calendario, su disponibilidad y su planificador.**

Lo malo de una frontera asi es que no se cruza de golpe: se cruza poco a poco.
Un campo util aqui, otro alli, y un dia Agora es medio calendario y ya nadie
sabe quien decide una fecha. Estas pruebas existen para que ese primer campo no
llegue a entrar.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from agora.api import ApiSettings, create_api
from agora.application import AgoraApplication
from agora.contracts import (
    CALENDAR_EXCEPTIONS,
    CALENDAR_TOKENS,
    CALENDAR_WORDS,
    ContractError,
    ContractRegistry,
)
from agora.profiles import load_profiles
from agora.skills import load_profile_skills
from conftest import write_profile

PROFILES_ROOT = Path(__file__).resolve().parents[1] / "profiles" / "task-planning"

BASE: dict[str, Any] = {
    "name": "ejemplo",
    "version": 1,
    "description": "Un contrato de prueba.",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["contract", "contract_version"],
        "properties": {
            "contract": {"const": "ejemplo"},
            "contract_version": {"const": 1},
            "pasos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["titulo"],
                    "properties": {"titulo": {"type": "string", "minLength": 1}},
                },
            },
        },
    },
    "example": {"contract": "ejemplo", "contract_version": 1, "pasos": [{"titulo": "x"}]},
}


def _escribir(root: Path, doc: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ejemplo.v1.yml").write_text(
        yaml.dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


@pytest.fixture
def doc() -> dict[str, Any]:
    return copy.deepcopy(BASE)


# --- La frontera se comprueba al cargar --------------------------------------


@pytest.mark.parametrize(
    "campo",
    [
        "start_date",
        "due_date",
        "scheduled_day",
        "hour_of_day",
        "calendar_slot",
        "availability",
        "work_block",
        "workblock_id",
        "deadline",
        "week_number",
        "pomodoro_count",
        "vacation_days",
    ],
)
def test_a_planner_field_is_refused_at_the_root(
    tmp_path: Path, doc: dict[str, Any], campo: str
) -> None:
    doc["schema"]["properties"][campo] = {"type": "string"}
    _escribir(tmp_path, doc)

    with pytest.raises(ContractError, match="planificador del cliente"):
        ContractRegistry.load(tmp_path)


def test_a_planner_field_hidden_inside_a_list_is_refused_too(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    """Es donde se colaria de verdad: un paso con su fecha."""
    doc["schema"]["properties"]["pasos"]["items"]["properties"]["start_date"] = {
        "type": "string"
    }
    _escribir(tmp_path, doc)

    with pytest.raises(ContractError, match=r"pasos\[\].start_date"):
        ContractRegistry.load(tmp_path)


def test_the_refusal_says_what_to_do_instead(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    """Un «no» que no ofrece salida se termina saltando."""
    doc["schema"]["properties"]["due_date"] = {"type": "string"}
    _escribir(tmp_path, doc)

    with pytest.raises(ContractError) as error:
        ContractRegistry.load(tmp_path)

    assert "minutos estimados" in str(error.value)


# --- Lo que si puede pasar ---------------------------------------------------


@pytest.mark.parametrize("campo", sorted(CALENDAR_EXCEPTIONS))
def test_effort_is_not_a_schedule(
    tmp_path: Path, doc: dict[str, Any], campo: str
) -> None:
    """Decir que algo cuesta 45 minutos no dice **cuando** se hace."""
    doc["schema"]["properties"]["pasos"]["items"]["properties"][campo] = {
        "type": ["integer", "null"]
    }
    _escribir(tmp_path, doc)

    assert ContractRegistry.load(tmp_path).get("ejemplo", 1)


def test_ordinary_fields_are_not_bothered(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    for campo in ("summary", "confidence", "warnings", "update", "today_matters"):
        doc["schema"]["properties"][campo] = {"type": "string"}
    _escribir(tmp_path, doc)

    assert ContractRegistry.load(tmp_path).get("ejemplo", 1)


def test_talking_about_not_scheduling_is_allowed(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    """Explicar por que no se agenda es correcto; tener donde escribirlo, no.

    La comprobacion mira **nombres de campo**, no descripciones: si mirara el
    texto, no se podria ni documentar la frontera.
    """
    doc["description"] = "No agenda: las fechas y las horas son del calendario del cliente."
    doc["schema"]["properties"]["warnings"] = {
        "type": "array",
        "description": "Aqui se dice que no se reparten dias ni horas.",
        "items": {"type": "string"},
    }
    _escribir(tmp_path, doc)

    assert ContractRegistry.load(tmp_path).get("ejemplo", 1)


# --- Los contratos que hay hoy -----------------------------------------------


def test_no_published_contract_crosses_the_boundary() -> None:
    registry = ContractRegistry.discover(PROFILES_ROOT)
    assert registry is not None

    for contract in registry.catalogue():
        rendered = json.dumps(contract.schema, ensure_ascii=False).lower()
        # Basta con que carguen: la regla se aplica al cargar. Esto lo deja
        # dicho tambien aqui, donde se lee la frontera.
        assert contract.schema["additionalProperties"] is False, contract.reference
        assert "workblock" not in rendered, contract.reference


def test_no_profile_offers_to_schedule() -> None:
    """Un perfil que aceptara «agendame la semana» ya habria cruzado la linea."""
    for profile in load_profiles(PROFILES_ROOT):
        handles = " ".join(profile.handles).lower()
        for verbo in ("schedule", "plan calendar", "assign days", "assign hours"):
            assert verbo not in handles, f"{profile.name}: {verbo}"


def test_the_planning_profiles_say_out_loud_what_they_refuse() -> None:
    """Rechazar en silencio no ensena la frontera a quien integra."""
    for nombre in ("task-intake", "task-decomposer", "review-analyzer", "planning-advisor"):
        profile = next(p for p in load_profiles(PROFILES_ROOT) if p.name == nombre)
        assert "schedule" in profile.metadata["refuses"], nombre


def test_every_skill_says_it_does_not_schedule() -> None:
    for profile in load_profiles(PROFILES_ROOT):
        for skill in load_profile_skills(profile.source.parent, profile.skills):
            cuerpo = skill.instructions.lower()
            assert "no agenda" in cuerpo or "no agendes" in cuerpo, skill.name


# --- La superficie publica ---------------------------------------------------


def test_the_api_surface_has_no_client_domain_words(tmp_path: Path) -> None:
    """La frontera tambien se cruza por el API, no solo por los contratos."""
    token = "a13-boundary-token-0123456789"
    application = AgoraApplication(tmp_path)
    application.initialize()
    write_profile(tmp_path / "AGENTS", "resumidor", handles=["summarize"])
    app = create_api(
        application, ApiSettings(token=token, principal="cliente", require_https=True)
    )
    client = TestClient(
        app, base_url="https://testserver", headers={"Authorization": f"Bearer {token}"}
    )

    superficie = json.dumps(
        {
            "profiles": client.get("/api/v1/profiles").json(),
            "board": client.get("/api/v1/board").json(),
            "health": client.get("/api/v1/health").json(),
            "events": client.get("/api/v1/events").json(),
        },
        ensure_ascii=False,
    ).lower()

    for concepto in (
        "workblock",
        "work_block",
        "pomodoro",
        "availability",
        "disponibilidad",
        "calendar",
        "scheduler",
        "vacacion",
        "gestion de tareas",
    ):
        assert concepto not in superficie, concepto


def test_no_endpoint_talks_about_the_calendar(tmp_path: Path) -> None:
    token = "a13-boundary-token-0123456789"
    application = AgoraApplication(tmp_path)
    application.initialize()
    app = create_api(
        application, ApiSettings(token=token, principal="cliente", require_https=True)
    )

    rutas = " ".join(route.path for route in app.routes).lower()

    for concepto in ("calendar", "schedul", "availab", "slot", "workblock"):
        assert concepto not in rutas, concepto


def test_the_boundary_vocabulary_is_not_empty() -> None:
    """Una guardia vacia pasaria todas las pruebas sin proteger nada."""
    assert len(CALENDAR_WORDS) + len(CALENDAR_TOKENS) >= 20
    assert "calendar" in CALENDAR_WORDS
    assert "date" in CALENDAR_TOKENS
    assert CALENDAR_EXCEPTIONS


def test_a_separator_does_not_get_a_field_past_the_guard() -> None:
    """`work_block`, `workBlock` y `workblock` son lo mismo para la guardia."""
    from agora.contracts import _calendar_word

    for escrito in ("workblock", "work_block", "workBlock", "work-block"):
        assert _calendar_word(escrito) == "workblock", escrito


def test_a_field_that_merely_contains_a_word_is_left_alone() -> None:
    """La primera version rechazaba `today_matters` por llevar «day» dentro."""
    from agora.contracts import _calendar_word

    for inocente in ("today_matters", "holidays_policy_text", "update", "endorsement"):
        assert _calendar_word(inocente) in (None, *CALENDAR_WORDS), inocente
