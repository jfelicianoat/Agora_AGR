"""A14: las cuatro capacidades viajan por la MISMA peticion generica.

AI_Broker no sabe que existen las tareas personales, ni los cursos, ni las
revisiones semanales. Recibe una peticion de chat, con un prompt dentro, y
devuelve texto. Todo lo que Agora ha construido en trece fases —entrevistar,
descomponer, analizar, aconsejar— cabe ahi sin anadirle al broker ni un tipo de
operacion nuevo.

Esto se comprueba, no se supone: la tentacion de pedirle al broker «un modo
descomposicion» aparece en cuanto algo no sale a la primera, y ceder una vez
convierte un servicio generico en el ayudante de un cliente concreto.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agora.broker.contracts import BrokerPolicy
from agora.broker.executor import apply_skill_output_contract
from agora.broker.request_builder import build_broker_request
from agora.cards import Card
from agora.profiles import Profile, load_profiles
from agora.skills import load_profile_skills

PROFILES_ROOT = Path(__file__).resolve().parents[1] / "profiles" / "task-planning"

#: Un encargo por capacidad, con la peticion que le corresponde.
ENCARGOS = {
    "task-intake": ("understand", "understand request: no se por donde empezar"),
    "task-decomposer": ("decompose", "break down work: impartir un curso"),
    "review-analyzer": ("analyze", "analyze review: mi semana"),
    "planning-advisor": ("advise", "advise on sequencing: cuatro encargos"),
}


def _request(nombre: str) -> dict:
    root = PROFILES_ROOT / nombre
    profile = Profile.load(root / "PROFILE.md")
    skills = load_profile_skills(root, profile.skills)
    function, request = ENCARGOS[nombre]
    card = Card.parse(
        "---\n"
        f"function: {function}\n"
        f"request: '{request}'\n"
        "created: 2026-09-09T00:00:00Z\n"
        "origin: test\npaths: []\nattempts: 0\n---\n\ncuerpo del encargo\n"
    )
    policy = apply_skill_output_contract(BrokerPolicy(), skills)
    return build_broker_request(profile, skills, card, policy=policy, idempotency_key="k")


@pytest.fixture(scope="module")
def peticiones() -> dict[str, dict]:
    return {nombre: _request(nombre) for nombre in ENCARGOS}


# --- Un solo tipo de operacion -----------------------------------------------


def test_every_capability_is_a_chat_request(peticiones: dict[str, dict]) -> None:
    """Cuatro capacidades, un tipo de operacion."""
    assert {p["inference_kind"] for p in peticiones.values()} == {"chat"}


def test_no_capability_invented_an_operation_type(peticiones: dict[str, dict]) -> None:
    """Si alguien anade `inference_kind: decompose`, esto lo caza."""
    for nombre, peticion in peticiones.items():
        assert peticion["inference_kind"] == "chat", nombre


def test_all_four_requests_have_the_same_shape(peticiones: dict[str, dict]) -> None:
    """La forma es una sola; lo unico que cambia es lo que se escribe dentro."""
    formas = {nombre: tuple(sorted(p)) for nombre, p in peticiones.items()}
    assert len(set(formas.values())) == 1, formas


def test_the_content_section_has_the_same_shape_too(
    peticiones: dict[str, dict],
) -> None:
    formas = {tuple(sorted(p["content"])) for p in peticiones.values()}
    assert len(formas) == 1, formas


def test_only_the_prompt_and_the_profile_change(peticiones: dict[str, dict]) -> None:
    """Todo lo demas de la peticion es identico entre capacidades."""
    def sin_lo_propio(peticion: dict) -> dict:
        copia = json.loads(json.dumps(peticion))
        copia["content"]["prompt"] = "<prompt>"
        copia["content"]["metadata"]["profile"] = "<perfil>"
        copia["request_id"] = "<id>"
        return copia

    normalizadas = {json.dumps(sin_lo_propio(p), sort_keys=True) for p in peticiones.values()}
    assert len(normalizadas) == 1


def test_the_output_section_asks_for_nothing_special(
    peticiones: dict[str, dict],
) -> None:
    """Ni un modo json, ni un esquema que el broker tendria que entender.

    Se midio en A03: `output.format: json` con `json_schema` no lo impone el
    broker y ademas rompe con algunos proveedores. El contrato lo hace cumplir
    Agora, despues, sobre el texto que llega.
    """
    for nombre, peticion in peticiones.items():
        assert peticion["output"]["format"] == "markdown", nombre
        assert "json_schema" not in peticion["output"], nombre


# --- El broker no aprende el vocabulario del cliente -------------------------


def test_the_request_envelope_knows_nothing_about_the_client_domain(
    peticiones: dict[str, dict],
) -> None:
    """El prompt lleva el encargo; el sobre, no.

    Un broker que empezara a ver campos como `task_id` o `calendar` dejaria de
    ser generico aunque nadie hubiera cambiado su codigo: sus registros, sus
    metricas y su enrutado se llenarian del dominio de un cliente.

    El nombre del perfil **si** viaja, en `metadata.profile` y en `request_id`.
    Es procedencia —de quien vino esta peticion—, no significado: al broker le
    da igual que `task-decomposer` descomponga, igual que a un servidor de
    correo le da igual de que hable un mensaje. Por eso esos dos campos se
    excluyen del barrido, y tienen su propia prueba de que son etiquetas opacas.
    """
    for nombre, peticion in peticiones.items():
        sobre = json.loads(json.dumps(peticion))
        sobre["content"]["prompt"] = ""
        sobre["content"]["metadata"]["profile"] = ""
        sobre["request_id"] = ""
        texto = json.dumps(sobre, ensure_ascii=False).lower()
        for concepto in (
            "calendar",
            "workblock",
            "pomodoro",
            "availability",
            "vacacion",
            "gestion",
            "task-brief",
            "work-breakdown",
            "review-analysis",
            "plan-advice",
        ):
            assert concepto not in texto, f"{nombre}: {concepto}"


def test_the_contract_name_does_not_leak_into_the_envelope(
    peticiones: dict[str, dict],
) -> None:
    """El broker no tiene por que saber que existe `work-breakdown@1`.

    Es un acuerdo entre Agora y su cliente. Si viajara en el sobre, el broker
    acabaria teniendo opiniones sobre el —enrutando por contrato, midiendo por
    contrato— y ya no seria generico.
    """
    for nombre, peticion in peticiones.items():
        sobre = json.loads(json.dumps(peticion))
        sobre["content"]["prompt"] = ""
        texto = json.dumps(sobre, ensure_ascii=False).lower()
        for contrato in ("task-brief", "work-breakdown", "review-analysis", "plan-advice"):
            assert contrato not in texto, f"{nombre}: {contrato}"


def test_the_metadata_says_agora_not_the_client(peticiones: dict[str, dict]) -> None:
    for peticion in peticiones.values():
        assert peticion["content"]["metadata"]["source"] == "agora-atomic"


def test_the_profile_travels_as_an_opaque_label(peticiones: dict[str, dict]) -> None:
    """El broker registra de quien viene, no que significa."""
    etiqueta = peticiones["task-decomposer"]["content"]["metadata"]["profile"]
    assert etiqueta.startswith("task-decomposer@")


# --- Todo lo que hace falta cabe en el prompt --------------------------------


def test_the_whole_capability_travels_inside_the_prompt(
    peticiones: dict[str, dict],
) -> None:
    """Perfil, skills, tarjeta y contrato: todo en el texto, nada en campos nuevos."""
    prompt = peticiones["task-decomposer"]["content"]["prompt"]

    assert "Atomic PROFILE" in prompt
    assert "Declared SKILLS" in prompt
    assert "CARD" in prompt
    assert "FORMATO OBLIGATORIO" in prompt


def test_the_contract_is_asked_for_in_words_not_in_a_broker_field(
    peticiones: dict[str, dict],
) -> None:
    for nombre in ("task-decomposer", "review-analyzer", "planning-advisor", "task-intake"):
        peticion = peticiones[nombre]
        assert "FORMATO OBLIGATORIO" in peticion["content"]["prompt"], nombre
        assert set(peticion["output"]) == {"format", "language"}, nombre


def test_a_capability_without_a_contract_sends_the_same_envelope(
    tmp_path: Path,
) -> None:
    """El sobre no cambia por declarar contrato: cambia el final del prompt."""
    from conftest import write_profile

    write_profile(tmp_path / "AGENTS", "simple", handles=["do something"])
    profile = Profile.load(tmp_path / "AGENTS" / "simple" / "PROFILE.md")
    card = Card.parse(
        "---\nfunction: transform\nrequest: 'do something'\n"
        "created: 2026-09-09T00:00:00Z\norigin: test\npaths: []\nattempts: 0\n---\n\nx\n"
    )
    peticion = build_broker_request(
        profile, (), card, policy=BrokerPolicy(), idempotency_key="k"
    )

    assert peticion["inference_kind"] == "chat"
    assert set(peticion["output"]) == {"format", "language"}
    assert "FORMATO OBLIGATORIO" not in peticion["content"]["prompt"]


# --- Nada del broker se ha tenido que tocar ----------------------------------


def test_agora_never_sends_a_field_the_contract_does_not_define(
    peticiones: dict[str, dict],
) -> None:
    """Un campo inventado seria una peticion de cambio al broker por la puerta de atras."""
    permitidos = {
        "idempotency_key",
        "request_id",
        "inference_kind",
        "content",
        "output",
        "generation",
        "model_requirements",
        "execution",
        "risk",
        "priority",
        "prompt_compression",
        "exclude_from_model_learning",
        "auxiliary_invocations",
    }
    for nombre, peticion in peticiones.items():
        assert set(peticion) <= permitidos, f"{nombre}: {set(peticion) - permitidos}"


def test_every_profile_in_the_repository_produces_a_chat_request() -> None:
    """No solo los cuatro de la serie: cualquiera que haya."""
    for profile in load_profiles(PROFILES_ROOT):
        skills = load_profile_skills(profile.source.parent, profile.skills)
        card = Card.parse(
            "---\n"
            f"function: {profile.function}\n"
            f"request: '{profile.handles[0]}: algo'\n"
            "created: 2026-09-09T00:00:00Z\n"
            "origin: test\npaths: []\nattempts: 0\n---\n\nx\n"
        )
        policy = apply_skill_output_contract(BrokerPolicy(), skills)
        peticion = build_broker_request(
            profile, skills, card, policy=policy, idempotency_key="k"
        )
        assert peticion["inference_kind"] == "chat", profile.name
