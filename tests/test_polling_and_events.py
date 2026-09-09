"""A09: un cliente puede vivir solo sondeando, sin escuchar nada.

Lo que se protege aqui es que sondear sea **suficiente** y **barato**: que el
cliente no necesite abrir un puerto, que no tenga que leer una tarjeta por cada
evento, y —lo mas importante— que si su cursor se queda atras **se entere**, en
vez de creerse al dia con un hueco dentro.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agora.api import ApiSettings, create_api
from agora.api.storage import EventStore
from agora.application import AgoraApplication
from agora.board import BoardState
from conftest import write_profile

TOKEN = "a09-polling-token-0123456789"

PETICION: dict[str, Any] = {
    "filename": "encargo.md",
    "function": "transform",
    "request": "summarize document",
}


def _api(tmp_path: Path) -> tuple[AgoraApplication, FastAPI]:
    application = AgoraApplication(tmp_path)
    application.initialize()
    app = create_api(
        application,
        ApiSettings(token=TOKEN, principal="cliente-externo", require_https=True),
    )
    return application, app


@pytest.fixture
def cliente(tmp_path: Path) -> tuple[AgoraApplication, TestClient]:
    application, app = _api(tmp_path)
    write_profile(
        tmp_path / "AGENTS",
        "resumidor",
        function="transform",
        handles=["summarize document"],
    )
    return application, TestClient(
        app, base_url="https://testserver", headers={"Authorization": f"Bearer {TOKEN}"}
    )


def _crear(client: TestClient, filename: str = "encargo.md", **extra: Any) -> None:
    cuerpo = {**PETICION, "filename": filename, **extra}
    assert client.post(
        "/api/v1/cards", json=cuerpo, headers={"Idempotency-Key": f"crear-{filename}"}
    ).status_code == 201


# --- Sondear basta -----------------------------------------------------------


def test_a_client_can_follow_everything_from_a_cursor(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """Sin listener, sin puerto abierto, sin webhook: solo un numero guardado."""
    _, client = cliente

    inicio = client.get("/api/v1/events").json()
    _crear(client, "una.md")
    _crear(client, "otra.md")

    nuevos = client.get("/api/v1/events", params={"after": inicio["cursor"]}).json()

    assert [e["kind"] for e in nuevos["events"]] == ["card.created", "card.created"]
    assert [e["data"]["filename"] for e in nuevos["events"]] == ["una.md", "otra.md"]


def test_an_event_says_the_state_without_a_second_request(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """Sondear seria caro si cada evento obligara a leer la tarjeta."""
    _, client = cliente
    _crear(client)

    evento = client.get("/api/v1/events").json()["events"][-1]

    assert evento["data"]["state"] == BoardState.PENDING.value


def test_an_event_carries_the_client_own_identifier(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """Con esto el cliente ata el evento a su trabajo sin leer nada mas."""
    _, client = cliente
    referencia = {"system": "cliente", "id": "tarea-7", "version": 1}
    _crear(client, external_reference=referencia)

    evento = client.get("/api/v1/events").json()["events"][-1]

    assert evento["data"]["external_reference"] == referencia


def test_a_card_without_a_reference_produces_a_clean_event(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    _crear(client)

    evento = client.get("/api/v1/events").json()["events"][-1]

    assert "external_reference" not in evento["data"]


def test_the_whole_life_of_a_card_is_visible_from_the_cursor(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    inicio = client.get("/api/v1/events").json()["cursor"]
    _crear(client)
    client.post(
        "/api/v1/cards/encargo.md/cancel",
        json={"reason": "el usuario cambio de idea"},
        headers={"Idempotency-Key": "cancelar"},
    )

    eventos = client.get("/api/v1/events", params={"after": inicio}).json()["events"]

    assert [e["kind"] for e in eventos] == ["card.created", "card.cancelled"]
    assert [e["data"]["state"] for e in eventos] == [
        BoardState.PENDING.value,
        BoardState.ARCHIVE.value,
    ]


def test_polling_with_nothing_new_costs_almost_nothing(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """El caso normal: el cliente pregunta y no ha pasado nada."""
    _, client = cliente
    _crear(client)
    cursor = client.get("/api/v1/events").json()["cursor"]

    vacia = client.get("/api/v1/events", params={"after": cursor}).json()

    assert vacia["events"] == []
    assert vacia["cursor"] == cursor
    assert vacia["more"] is False
    assert vacia["missed"] is False


def test_the_cursor_does_not_move_backwards_when_there_is_nothing(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    _crear(client)
    cursor = client.get("/api/v1/events").json()["cursor"]

    for _ in range(3):
        cursor = client.get("/api/v1/events", params={"after": cursor}).json()["cursor"]

    assert cursor == client.get("/api/v1/events").json()["newest"]


# --- Paginacion --------------------------------------------------------------


def test_a_page_is_bounded_and_says_there_is_more(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    for indice in range(5):
        _crear(client, f"carta-{indice}.md")

    pagina = client.get("/api/v1/events", params={"after": 0, "limit": 2}).json()

    assert len(pagina["events"]) == 2
    assert pagina["more"] is True


def test_following_the_cursor_walks_every_event_once(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    for indice in range(5):
        _crear(client, f"carta-{indice}.md")

    vistos: list[int] = []
    cursor = 0
    while True:
        pagina = client.get("/api/v1/events", params={"after": cursor, "limit": 2}).json()
        vistos.extend(e["id"] for e in pagina["events"])
        cursor = pagina["cursor"]
        if not pagina["more"]:
            break

    assert vistos == sorted(set(vistos))
    assert len(vistos) == 5


def test_an_absurd_limit_is_refused(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente

    assert client.get("/api/v1/events", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/events", params={"limit": 100_000}).status_code == 422


# --- El hueco: lo que no se puede callar -------------------------------------


def test_a_cursor_left_behind_is_told_it_missed_events(tmp_path: Path) -> None:
    """Un cliente que estuvo apagado demasiado tiempo tiene que enterarse.

    El tablero guarda una ventana. Si el cursor queda por detras, hay eventos
    que el cliente no vera nunca. Devolverle solo lo que queda lo dejaria
    creyendo que esta al dia con un hueco dentro, que es peor que un error.
    """
    store = EventStore(tmp_path / "events.json", limit=3)
    for indice in range(6):
        store.emit("card.created", {"filename": f"c{indice}.md"})

    pagina = store.page(after=1)

    assert pagina.oldest_available == 4
    assert pagina.missed is True


def test_a_cursor_inside_the_window_missed_nothing(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.json", limit=10)
    for indice in range(4):
        store.emit("card.created", {"filename": f"c{indice}.md"})

    assert store.page(after=2).missed is False


def test_a_client_starting_from_scratch_has_not_missed_anything(
    tmp_path: Path,
) -> None:
    """`after=0` es un cliente nuevo: no tenia cursor que quedarse atras."""
    store = EventStore(tmp_path / "events.json", limit=2)
    for indice in range(5):
        store.emit("card.created", {"filename": f"c{indice}.md"})

    assert store.page(after=0).missed is False


def test_an_empty_board_answers_without_inventing_a_gap(tmp_path: Path) -> None:
    store = EventStore(tmp_path / "events.json")

    pagina = store.page(after=0)

    assert pagina.events == ()
    assert pagina.missed is False
    assert pagina.cursor == 0
    assert pagina.newest == 0


def test_the_gap_reaches_the_client_through_the_api(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    _crear(client)

    lejano = client.get("/api/v1/events", params={"after": 0}).json()

    assert lejano["missed"] is False
    assert lejano["oldest_available"] >= 1


# --- Nada de esto exige un listener ------------------------------------------


def test_there_is_no_endpoint_that_registers_a_callback(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """A09 disena los callbacks; no los impone. Hoy no hay ninguno, a proposito.

    Si algun dia se anaden, esta prueba fallara y habra que decidirlo a
    conciencia en vez de que aparezca uno sin querer.
    """
    _, client = cliente
    rutas = {route.path for route in client.app.routes}  # type: ignore[attr-defined]

    for sospechosa in ("/api/v1/webhooks", "/api/v1/callbacks", "/api/v1/subscriptions"):
        assert sospechosa not in rutas


def test_polling_needs_the_same_credentials_as_everything_else(
    tmp_path: Path,
) -> None:
    _, app = _api(tmp_path)
    sin_credenciales = TestClient(app, base_url="https://testserver")

    assert sin_credenciales.get("/api/v1/events").status_code in {401, 403}
