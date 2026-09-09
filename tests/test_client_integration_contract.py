"""A07: el flujo que ve un cliente externo, de punta a punta y sin trampas.

Crear la tarjeta, preguntar su estado, recoger el artefacto, cancelarla. Y las
tres cosas que un cliente necesita que esten escritas y no cambien: **que se
puede pedir**, **que se va a recibir** y **que pasa cuando algo sale mal**.

Ningun concepto de Gestion de Tareas aparece aqui, y hay una prueba que lo
comprueba: el mismo flujo lo puede usar cualquier cliente.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agora.api import ApiSettings, create_api
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.output_contract import validate_payload
from conftest import create_card, write_profile

TOKEN = "a07-integration-token-0123456789"

#: Lo minimo que manda un cliente. `filename` lo elige el, que es lo que le
#: permite volver a preguntar por la tarjeta sin guardar nada mas.
PETICION: dict[str, Any] = {
    "filename": "encargo-cliente.md",
    "function": "transform",
    "request": "summarize document",
}

CONTRATO: dict[str, Any] = {
    "name": "demo-brief",
    "version": 1,
    "description": "Un documento de prueba para el contrato de integracion.",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["contract", "contract_version", "summary"],
        "properties": {
            "contract": {"const": "demo-brief"},
            "contract_version": {"const": 1},
            "summary": {"type": "string", "minLength": 1},
        },
    },
    "example": {
        "contract": "demo-brief",
        "contract_version": 1,
        "summary": "Lo que se entendio del encargo.",
    },
}


def _api(tmp_path: Path) -> tuple[AgoraApplication, FastAPI]:
    application = AgoraApplication(tmp_path)
    application.initialize()
    app = create_api(
        application,
        ApiSettings(token=TOKEN, principal="cliente-externo", require_https=True),
    )
    return application, app


def _client(app: FastAPI, *, authorized: bool = True) -> TestClient:
    headers = {"Authorization": f"Bearer {TOKEN}"} if authorized else {}
    return TestClient(app, base_url="https://testserver", headers=headers)


def _write_contract(agents_root: Path, doc: dict[str, Any] | None = None) -> None:
    contracts = agents_root / "CONTRACTS"
    contracts.mkdir(parents=True, exist_ok=True)
    payload = doc or CONTRATO
    (contracts / f"{payload['name']}.v{payload['version']}.yml").write_text(
        yaml.dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


@pytest.fixture
def integracion(tmp_path: Path) -> tuple[AgoraApplication, TestClient]:
    application, app = _api(tmp_path)
    agents = tmp_path / "AGENTS"
    _write_contract(agents)
    write_profile(
        agents,
        "resumidor",
        function="transform",
        handles=["summarize document"],
        refuses=["schedule"],
        skills=["resumir"],
    )
    skill = agents / "resumidor" / "skills" / "resumir" / "SKILL.md"
    skill.write_text(
        "---\nname: resumir\nversion: 1.0.0\ndescription: resume\nmodes:\n  - card\n"
        "output_contract: demo-brief\noutput_contract_version: 1\n---\n\ncuerpo\n",
        encoding="utf-8",
    )
    return application, _client(app)


# --- Que se puede pedir ------------------------------------------------------


def test_the_client_can_discover_what_to_ask_for(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    """`handles` es vocabulario publico: una peticion fuera de el no empareja."""
    _, client = integracion

    response = client.get("/api/v1/profiles")

    assert response.status_code == 200
    perfil = next(p for p in response.json()["profiles"] if p["name"] == "resumidor")
    assert perfil["handles"] == ["summarize document"]
    assert perfil["refuses"] == ["schedule"]
    assert perfil["function"] == "transform"


def test_the_client_can_discover_what_it_will_receive(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = integracion

    perfiles = client.get("/api/v1/profiles").json()["profiles"]
    perfil = next(p for p in perfiles if p["name"] == "resumidor")

    assert perfil["produces"] == ["demo-brief@1"]


def test_a_profile_that_promises_nothing_says_so(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    application, client = integracion
    write_profile(application.profiles_root, "sin-contrato", handles=["otra cosa"])

    perfiles = client.get("/api/v1/profiles").json()["profiles"]
    perfil = next(p for p in perfiles if p["name"] == "sin-contrato")

    assert perfil["produces"] == []


def test_the_catalogue_gives_the_shape_of_every_document(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    """Con esto el cliente valida por su cuenta lo que recibe."""
    _, client = integracion

    response = client.get("/api/v1/contracts")

    assert response.status_code == 200
    contratos = response.json()["contracts"]
    assert [c["reference"] for c in contratos] == ["demo-brief@1"]
    contrato = contratos[0]
    assert contrato["schema"]["properties"]["contract"]["const"] == "demo-brief"
    assert contrato["example"]["summary"]
    assert contrato["description"]


def test_a_board_with_no_contracts_answers_an_empty_catalogue(
    tmp_path: Path,
) -> None:
    """Un despliegue anterior a A06 responde vacio, no un error."""
    _, app = _api(tmp_path)
    write_profile(tmp_path / "AGENTS", "simple")

    response = _client(app).get("/api/v1/contracts")

    assert response.status_code == 200
    assert response.json() == {"contracts": []}


def test_a_corrected_contract_is_seen_without_restarting(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    """Un contrato es una promesa publica: corregirla no puede exigir un reinicio."""
    application, client = integracion
    assert len(client.get("/api/v1/contracts").json()["contracts"]) == 1

    segundo = dict(CONTRATO, name="otro-brief")
    segundo["schema"] = json.loads(json.dumps(CONTRATO["schema"]))
    segundo["schema"]["properties"]["contract"]["const"] = "otro-brief"
    segundo["example"] = dict(CONTRATO["example"], contract="otro-brief")
    _write_contract(application.profiles_root, segundo)

    referencias = [c["reference"] for c in client.get("/api/v1/contracts").json()["contracts"]]
    assert referencias == ["demo-brief@1", "otro-brief@1"]


# --- Crear -> estado -> artefacto -------------------------------------------


def test_the_whole_flow_a_client_needs(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    application, client = integracion

    creada = client.post(
        "/api/v1/cards",
        json=PETICION,
        headers={"Idempotency-Key": "flujo-1"},
    )
    assert creada.status_code == 201
    filename = creada.json()["filename"]
    assert filename == PETICION["filename"]
    assert creada.json()["state"] == BoardState.PENDING.value
    assert creada.json()["replayed"] is False

    estado = client.get(f"/api/v1/cards/{filename}")
    assert estado.status_code == 200
    assert estado.json()["state"] == BoardState.PENDING.value

    cancelada = client.post(
        f"/api/v1/cards/{filename}/cancel",
        json={"reason": "el usuario cambio de idea"},
        headers={"Idempotency-Key": "cancel-1"},
    )
    assert cancelada.status_code == 200

    final = client.get(f"/api/v1/cards/{filename}")
    assert final.json()["state"] == BoardState.ARCHIVE.value


def test_the_state_of_an_unknown_card_is_a_clear_404(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = integracion

    response = client.get("/api/v1/cards/no-existe.md")

    assert response.status_code == 404


# --- Idempotencia ------------------------------------------------------------


def test_repeating_a_creation_does_not_create_a_second_card(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    """Un cliente que reintenta tras un corte de red no duplica trabajo."""
    application, client = integracion
    cuerpo = PETICION

    primera = client.post("/api/v1/cards", json=cuerpo, headers={"Idempotency-Key": "k"})
    segunda = client.post("/api/v1/cards", json=cuerpo, headers={"Idempotency-Key": "k"})

    assert primera.status_code == 201
    assert segunda.json()["filename"] == primera.json()["filename"]
    assert segunda.json()["replayed"] is True
    pendientes = list((application.board.root / BoardState.PENDING.value).glob("*.md"))
    assert len(pendientes) == 1


def test_the_same_key_with_a_different_body_is_a_conflict(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    """Reusar la clave para otra cosa es un error del cliente, y se dice."""
    _, client = integracion
    client.post(
        "/api/v1/cards",
        json=PETICION,
        headers={"Idempotency-Key": "k"},
    )

    otra = client.post(
        "/api/v1/cards",
        json=dict(PETICION, body="otro cuerpo"),
        headers={"Idempotency-Key": "k"},
    )

    assert otra.status_code == 409


def test_creating_without_a_key_is_refused(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    """Sin clave no hay forma de reintentar sin duplicar, asi que se exige."""
    _, client = integracion

    response = client.post(
        "/api/v1/cards",
        json=PETICION,
    )

    assert response.status_code in {400, 422}


def test_cancelling_twice_with_the_same_key_is_the_same_answer(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = integracion
    filename = client.post(
        "/api/v1/cards",
        json=PETICION,
        headers={"Idempotency-Key": "crear"},
    ).json()["filename"]

    una = client.post(
        f"/api/v1/cards/{filename}/cancel",
        json={"reason": "ya no hace falta"},
        headers={"Idempotency-Key": "cancelar"},
    )
    otra = client.post(
        f"/api/v1/cards/{filename}/cancel",
        json={"reason": "ya no hace falta"},
        headers={"Idempotency-Key": "cancelar"},
    )

    assert una.status_code == otra.status_code == 200
    assert otra.json().get("replayed") is True


# --- Seguridad y frontera ----------------------------------------------------


def test_nothing_answers_without_credentials(tmp_path: Path) -> None:
    _, app = _api(tmp_path)
    client = _client(app, authorized=False)

    for path in ("/api/v1/profiles", "/api/v1/contracts", "/api/v1/board"):
        assert client.get(path).status_code in {401, 403}, path


def test_http_is_refused(tmp_path: Path) -> None:
    _, app = _api(tmp_path)
    client = TestClient(
        app, base_url="http://testserver", headers={"Authorization": f"Bearer {TOKEN}"}
    )

    assert client.get("/api/v1/contracts").status_code in {400, 403, 426}


def test_the_api_knows_nothing_about_the_client_domain(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    """La frontera: Agora ejecuta trabajo de IA, el cliente tiene el calendario.

    Si alguna de estas palabras aparece en lo que el API responde, es que un
    concepto del cliente se ha colado en el contrato publico.
    """
    _, client = integracion
    superficie = json.dumps(
        {
            "profiles": client.get("/api/v1/profiles").json(),
            "contracts": client.get("/api/v1/contracts").json(),
            "board": client.get("/api/v1/board").json(),
            "health": client.get("/api/v1/health").json(),
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
        "gestion_tareas",
        "gestion de tareas",
    ):
        assert concepto not in superficie, concepto


def test_the_health_check_says_who_owns_the_board(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = integracion

    payload = client.get("/api/v1/health").json()

    assert payload == {"status": "ok", "api": "v1", "board_owner": "agora"}


# --- El ciclo entero, hasta recoger el documento -----------------------------


def test_the_client_gets_back_a_document_that_honours_the_contract(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    """Crear -> estado -> artefacto, y el artefacto cumple lo que `produces` dijo.

    El claim y el close los hace un runner, no el cliente; aqui se simulan para
    poder recorrer el ciclo entero sin depender del PC de IA.
    """
    _, client = integracion

    creada = client.post(
        "/api/v1/cards", json=PETICION, headers={"Idempotency-Key": "ciclo"}
    )
    filename = creada.json()["filename"]

    reclamada = client.post(
        f"/api/v1/cards/{filename}/claim",
        json={"runner_id": "runner-1", "profile": "resumidor"},
        headers={"Idempotency-Key": "ciclo-claim"},
    )
    assert reclamada.status_code == 200
    assert client.get(f"/api/v1/cards/{filename}").json()["state"] == (
        BoardState.IN_PROGRESS.value
    )

    documento = json.dumps(CONTRATO["example"], ensure_ascii=False)
    cerrada = client.post(
        f"/api/v1/cards/{filename}/close",
        json={
            "runner_id": "runner-1",
            "artifacts": [
                {
                    "name": "final.json",
                    "content_base64": base64.b64encode(documento.encode()).decode(),
                }
            ],
        },
        headers={"Idempotency-Key": "ciclo-close"},
    )
    assert cerrada.status_code == 200
    assert client.get(f"/api/v1/cards/{filename}").json()["state"] == BoardState.DONE.value

    artefacto = client.get(f"/api/v1/cards/{filename}/artifacts/0")
    assert artefacto.status_code == 200

    recibido = json.loads(artefacto.content)
    contrato = client.get("/api/v1/contracts").json()["contracts"][0]
    assert recibido["contract"] == contrato["name"]
    assert recibido["contract_version"] == contrato["version"]
    assert validate_payload(contrato["schema"], recibido) == []


def test_asking_for_an_artifact_that_is_not_there_is_a_404(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = integracion
    creada = client.post(
        "/api/v1/cards", json=PETICION, headers={"Idempotency-Key": "sin-artefacto"}
    )
    filename = creada.json()["filename"]

    assert client.get(f"/api/v1/cards/{filename}/artifacts/0").status_code == 404


def test_a_card_already_finished_cannot_be_claimed_again(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    """Los estados son una maquina, no una sugerencia."""
    _, client = integracion
    filename = client.post(
        "/api/v1/cards", json=PETICION, headers={"Idempotency-Key": "estados"}
    ).json()["filename"]
    client.post(
        f"/api/v1/cards/{filename}/cancel",
        json={"reason": "ya no hace falta"},
        headers={"Idempotency-Key": "estados-cancel"},
    )

    otra = client.post(
        f"/api/v1/cards/{filename}/claim",
        json={"runner_id": "runner-1", "profile": "resumidor"},
        headers={"Idempotency-Key": "estados-claim"},
    )

    assert otra.status_code in {404, 409, 422}


# --- El origen: quien puede depositar trabajo --------------------------------


def test_a_card_created_through_the_api_is_dispatchable(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    """Autenticarse y recibir 201 tiene que significar que el trabajo va a correr.

    Antes no lo significaba: el API pone `origin` al principal autenticado y el
    despachador solo confiaba en cuatro nombres fijos, asi que la tarjeta de
    cualquier cliente real moria en `blocked` **despues** de responderle 201.
    """
    application, client = integracion
    client.post("/api/v1/cards", json=PETICION, headers={"Idempotency-Key": "origen"})

    disponible = client.get("/api/v1/work", params={"profiles": "resumidor"}).json()

    assert [item["filename"] for item in disponible] == [PETICION["filename"]]
    assert "cliente-externo" in application.trusted_origins


def test_a_card_dropped_by_hand_with_a_strange_origin_is_still_blocked(
    integracion: tuple[AgoraApplication, TestClient],
) -> None:
    """La lista sigue protegiendo el sistema de ficheros, que es para lo que esta."""
    application, client = integracion
    create_card(
        application.board,
        "colada.md",
        function="transform",
        request="summarize document",
        origin="quien-sea",
    )

    disponible = client.get("/api/v1/work", params={"profiles": "resumidor"}).json()

    assert [item["filename"] for item in disponible] == []


def test_trusting_an_origin_twice_changes_nothing(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)

    application.trust_origin("cliente")
    antes = application.trusted_origins
    application.trust_origin("CLIENTE")
    application.trust_origin("  ")

    assert application.trusted_origins == antes


def test_the_default_trusted_origins_are_unchanged(tmp_path: Path) -> None:
    """Compatibilidad: un despliegue que no configura nada se comporta igual."""
    from agora.dispatcher import DEFAULT_TRUSTED_ORIGINS

    application = AgoraApplication(tmp_path)

    assert application.trusted_origins == DEFAULT_TRUSTED_ORIGINS
