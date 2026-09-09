"""A08: la referencia externa, para reconciliar sin inventar una clave.

Un cliente ya tiene un identificador para su trabajo. Este campo se lo guarda y
se lo devuelve, y le deja buscar por el cuando ha perdido el `filename`.

Lo que mas se protege aqui es lo que la referencia **no** es: ni unica, ni
secreta, ni la identidad de la tarjeta. Cada una de esas tres cosas tiene su
prueba, porque cualquiera de ellas la convertiria en una clave, y una clave
inventada por accidente es muy dificil de quitar despues.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agora.api import ApiSettings, create_api
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.cards import Card
from conftest import write_profile

TOKEN = "a08-reference-token-0123456789"

PETICION: dict[str, Any] = {
    "filename": "encargo.md",
    "function": "transform",
    "request": "summarize document",
}

REFERENCIA: dict[str, Any] = {"system": "cliente-cualquiera", "id": "tarea-4821"}


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


@pytest.fixture
def cliente(tmp_path: Path) -> tuple[AgoraApplication, TestClient]:
    application, app = _api(tmp_path)
    write_profile(
        tmp_path / "AGENTS",
        "resumidor",
        function="transform",
        handles=["summarize document"],
    )
    return application, _client(app)


def _crear(client: TestClient, **overrides: Any) -> dict[str, Any]:
    cuerpo = dict(PETICION)
    cuerpo.update(overrides)
    respuesta = client.post(
        "/api/v1/cards",
        json=cuerpo,
        headers={"Idempotency-Key": f"crear-{cuerpo['filename']}"},
    )
    assert respuesta.status_code == 201, respuesta.text
    return respuesta.json()


# --- Se guarda y se devuelve -------------------------------------------------


def test_the_reference_survives_in_the_card(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    application, client = cliente

    _crear(client, external_reference=REFERENCIA)

    card = Card.load(application.board.directory(BoardState.PENDING) / "encargo.md")
    assert card.metadata["external_reference"] == {**REFERENCIA, "version": 1}


def test_the_client_gets_it_back_when_asking_for_the_card(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    _crear(client, external_reference=REFERENCIA)

    estado = client.get("/api/v1/cards/encargo.md").json()

    assert estado["metadata"]["external_reference"]["id"] == "tarea-4821"


def test_the_version_defaults_to_one(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """Versionado explicito: un cliente sabe que forma esta leyendo."""
    _, client = cliente
    _crear(client, external_reference=REFERENCIA)

    estado = client.get("/api/v1/cards/encargo.md").json()

    assert estado["metadata"]["external_reference"]["version"] == 1


def test_a_declared_version_is_kept(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    _crear(client, external_reference={**REFERENCIA, "version": 1})

    estado = client.get("/api/v1/cards/encargo.md").json()

    assert estado["metadata"]["external_reference"]["version"] == 1


def test_agora_does_not_interpret_the_identifier(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """Generico de verdad: un id con la forma que sea se guarda tal cual."""
    _, client = cliente
    raro = {"system": "otro-sistema", "id": "urn:uuid:9f3c/tarea#7", "version": 1}

    _crear(client, external_reference=raro)

    estado = client.get("/api/v1/cards/encargo.md").json()
    assert estado["metadata"]["external_reference"] == raro


# --- Reconciliar -------------------------------------------------------------


def test_the_client_finds_its_card_after_losing_the_filename(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """El caso real: un corte a mitad, y el cliente solo conserva su id."""
    _, client = cliente
    _crear(client, external_reference=REFERENCIA)

    encontrado = client.get(
        "/api/v1/cards", params={"system": "cliente-cualquiera", "external_id": "tarea-4821"}
    )

    assert encontrado.status_code == 200
    assert [c["filename"] for c in encontrado.json()["cards"]] == ["encargo.md"]
    assert encontrado.json()["cards"][0]["state"] == BoardState.PENDING.value


def test_the_search_is_case_insensitive_for_the_system_only(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """El `system` es un nombre; el `id` es del cliente y no se toca."""
    _, client = cliente
    _crear(client, external_reference=REFERENCIA)

    por_sistema = client.get(
        "/api/v1/cards", params={"system": "CLIENTE-CUALQUIERA", "external_id": "tarea-4821"}
    ).json()["cards"]
    por_id = client.get(
        "/api/v1/cards", params={"system": "cliente-cualquiera", "external_id": "TAREA-4821"}
    ).json()["cards"]

    assert len(por_sistema) == 1
    assert por_id == []


def test_two_cards_may_share_a_reference(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """No es unica, y por eso buscar devuelve una lista.

    Tratarla como unica la convertiria en clave primaria de facto: el dia que
    un cliente parta su trabajo en dos, o reintente, el sistema le mentiria.
    """
    _, client = cliente
    _crear(client, filename="parte-uno.md", external_reference=REFERENCIA)
    _crear(client, filename="parte-dos.md", external_reference=REFERENCIA)

    encontrado = client.get(
        "/api/v1/cards", params={"system": "cliente-cualquiera", "external_id": "tarea-4821"}
    ).json()["cards"]

    assert [c["filename"] for c in encontrado] == ["parte-dos.md", "parte-uno.md"]


def test_a_reference_nobody_used_finds_nothing(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    _crear(client, external_reference=REFERENCIA)

    encontrado = client.get(
        "/api/v1/cards", params={"system": "cliente-cualquiera", "external_id": "otra-cosa"}
    )

    assert encontrado.status_code == 200
    assert encontrado.json()["cards"] == []


def test_the_search_looks_in_every_state(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """Reconciliar sirve sobre todo para trabajo que ya termino o se cancelo."""
    _, client = cliente
    _crear(client, external_reference=REFERENCIA)
    client.post(
        "/api/v1/cards/encargo.md/cancel",
        json={"reason": "el usuario cambio de idea"},
        headers={"Idempotency-Key": "cancelar"},
    )

    encontrado = client.get(
        "/api/v1/cards", params={"system": "cliente-cualquiera", "external_id": "tarea-4821"}
    ).json()["cards"]

    assert [c["state"] for c in encontrado] == [BoardState.ARCHIVE.value]


# --- No es una clave de seguridad -------------------------------------------


def test_knowing_a_reference_grants_nothing_without_credentials(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """Lo que da acceso es el token, no saberse el identificador."""
    application, client = cliente
    _crear(client, external_reference=REFERENCIA)
    sin_credenciales = TestClient(
        client.app, base_url="https://testserver"  # type: ignore[arg-type]
    )

    respuesta = sin_credenciales.get(
        "/api/v1/cards", params={"system": "cliente-cualquiera", "external_id": "tarea-4821"}
    )

    assert respuesta.status_code in {401, 403}


def test_the_reference_is_not_the_identity_of_the_card(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """El `filename` sigue siendo la identidad; la referencia solo acompana."""
    _, client = cliente
    creada = _crear(client, external_reference=REFERENCIA)

    assert creada["filename"] == PETICION["filename"]
    assert "external_reference" not in creada


def test_the_reference_does_not_change_idempotency(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """Dos tarjetas con la misma referencia y claves distintas son dos tarjetas.

    La idempotencia la decide la `Idempotency-Key`, no la referencia. Si la
    referencia deduplicara, seria una clave.
    """
    application, client = cliente
    _crear(client, filename="una.md", external_reference=REFERENCIA)
    _crear(client, filename="otra.md", external_reference=REFERENCIA)

    pendientes = list(
        (application.board.directory(BoardState.PENDING)).glob("*.md")
    )
    assert len(pendientes) == 2


# --- Compatibilidad hacia atras ---------------------------------------------


def test_a_card_without_a_reference_works_exactly_as_before(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    application, client = cliente

    _crear(client)

    card = Card.load(application.board.directory(BoardState.PENDING) / "encargo.md")
    assert "external_reference" not in card.metadata
    assert client.get("/api/v1/cards/encargo.md").json()["state"] == BoardState.PENDING.value


def test_cards_without_a_reference_are_invisible_to_the_search(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    _crear(client)

    encontrado = client.get(
        "/api/v1/cards", params={"system": "cliente-cualquiera", "external_id": "tarea-4821"}
    ).json()["cards"]

    assert encontrado == []


def test_an_empty_reference_is_refused(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente

    respuesta = client.post(
        "/api/v1/cards",
        json={**PETICION, "external_reference": {"system": "", "id": "x"}},
        headers={"Idempotency-Key": "vacia"},
    )

    assert respuesta.status_code == 422


def test_an_invented_field_in_the_reference_is_refused(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """El campo es versionado: colar claves nuevas sin subir version, no."""
    _, client = cliente

    respuesta = client.post(
        "/api/v1/cards",
        json={
            **PETICION,
            "external_reference": {"system": "s", "id": "i", "prioridad_del_cliente": 3},
        },
        headers={"Idempotency-Key": "colada"},
    )

    assert respuesta.status_code == 422


def test_the_search_needs_both_halves(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente

    assert client.get("/api/v1/cards", params={"system": "s"}).status_code == 422
    assert client.get("/api/v1/cards", params={"external_id": "i"}).status_code == 422


def test_the_api_still_knows_nothing_about_the_client_domain(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """`system` e `id` son opacos: Agora no deduce nada de ellos."""
    _, client = cliente
    _crear(client, external_reference={"system": "gestion-tareas-ia", "id": "task-1"})

    estado = client.get("/api/v1/cards/encargo.md").json()

    # Se guarda tal cual, pero no aparece en ningun sitio donde Agora decida.
    assert estado["metadata"]["external_reference"]["system"] == "gestion-tareas-ia"
    assert estado["metadata"]["function"] == "transform"
    assert "external_reference" not in str(estado["metadata"]["request"])
