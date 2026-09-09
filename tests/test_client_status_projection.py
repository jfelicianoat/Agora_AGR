"""A11: el estado que ve un cliente, sin obligarle a conocer el tablero.

Un cliente no deberia tener que aprenderse las carpetas de otro sistema. Lo que
necesita saber es si su trabajo espera, corre, salio bien, salio mal, esta
parado o se cancelo. Y cuando algo va mal, necesita un **codigo** sobre el que
ramificar, no una frase que alguien puede reescribir manana.

El estado interno sigue viajando al lado: la proyeccion no oculta nada, traduce.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agora.api import ApiSettings, create_api
from agora.api.projection import project
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.cards import Card
from conftest import create_card, write_profile

TOKEN = "a11-projection-token-0123456789"

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


# --- El mapeo, estado por estado ---------------------------------------------


@pytest.mark.parametrize(
    ("interno", "esperado"),
    [
        (BoardState.PENDING, "queued"),
        (BoardState.SCHEDULED, "queued"),
        (BoardState.IN_PROGRESS, "running"),
        (BoardState.DONE, "completed"),
        (BoardState.ARCHIVE, "cancelled"),
    ],
)
def test_the_documented_mapping(interno: BoardState, esperado: str) -> None:
    assert project(interno, {})["status"] == esperado


def test_every_internal_state_has_a_projection() -> None:
    """Un estado sin traducir seria un cliente sin respuesta."""
    for state in BoardState:
        proyectado = project(state, {})
        assert proyectado["status"], state
        assert "terminal" in proyectado, state


def test_waiting_its_turn_and_waiting_in_queue_are_the_same_for_a_client() -> None:
    """Que Agora distinga `scheduled` de `pending` es asunto de Agora."""
    assert project(BoardState.SCHEDULED, {})["status"] == "queued"
    assert project(BoardState.PENDING, {})["status"] == "queued"


def test_the_projection_agrees_with_the_board_about_what_is_final() -> None:
    """La terminalidad no se reescribe aqui: se toma de `BoardState`."""
    for state in BoardState:
        assert project(state, {})["terminal"] is state.is_terminal, state


# --- `blocked` se parte en dos, y esa es la decision del mapeo ----------------


def test_running_out_of_attempts_is_a_failure() -> None:
    proyectado = project(
        BoardState.BLOCKED,
        {
            "blocked": {"reason": "sin intentos", "code": "attempts_exhausted"},
            "attempts": 3,
            "max_attempts": 3,
        },
    )

    assert proyectado["status"] == "failed"
    assert proyectado["error"]["code"] == "attempts_exhausted"
    assert proyectado["error"]["attempts"] == 3
    assert proyectado["error"]["max_attempts"] == 3


def test_being_stopped_for_another_reason_is_not_a_failure() -> None:
    """Decir «fallo» de un encargo que ni se intento seria mentirle al cliente."""
    proyectado = project(
        BoardState.BLOCKED,
        {"blocked": {"reason": "Untrusted origin: x", "code": "untrusted_origin"}},
    )

    assert proyectado["status"] == "blocked"
    assert proyectado["error"]["code"] == "untrusted_origin"


def test_a_stopped_card_is_not_terminal() -> None:
    """Se paro, que no es lo mismo que haber terminado: alguien puede reanudarla."""
    for code in ("attempts_exhausted", "untrusted_origin"):
        proyectado = project(BoardState.BLOCKED, {"blocked": {"reason": "x", "code": code}})
        assert proyectado["terminal"] is False, code


# --- Errores tipados ---------------------------------------------------------


def test_the_code_is_written_when_blocking_not_guessed_afterwards(
    tmp_path: Path,
) -> None:
    """Deducirlo leyendo la frase se rompe en cuanto alguien reescribe la frase."""
    application, _ = _api(tmp_path)
    create_card(application.board)
    pendiente = application.board.directory(BoardState.PENDING) / "task.md"

    bloqueada = application.board.block_pending(
        pendiente, actor="dispatcher", reason="Untrusted origin: quien-sea",
        code="untrusted_origin",
    )

    card = Card.load(bloqueada)
    assert card.metadata["blocked"]["code"] == "untrusted_origin"
    assert card.metadata["blocked"]["reason"].startswith("Untrusted origin")


def test_an_old_blocked_card_without_a_code_says_it_does_not_know() -> None:
    """Compatibilidad honesta: lo de antes de A11 no lleva codigo, y no se inventa."""
    proyectado = project(BoardState.BLOCKED, {"blocked": {"reason": "de antes"}})

    assert proyectado["status"] == "blocked"
    assert proyectado["error"]["code"] == "unknown"
    assert proyectado["error"]["message"] == "de antes"


def test_an_invented_code_is_not_trusted() -> None:
    proyectado = project(
        BoardState.BLOCKED, {"blocked": {"reason": "x", "code": "lo-que-sea"}}
    )

    assert proyectado["error"]["code"] == "unknown"


def test_a_cancelled_card_says_who_cancelled_it_and_why(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    client.post("/api/v1/cards", json=PETICION, headers={"Idempotency-Key": "k"})
    client.post(
        "/api/v1/cards/encargo.md/cancel",
        json={"reason": "el usuario cambio de idea"},
        headers={"Idempotency-Key": "c"},
    )

    estado = client.get("/api/v1/cards/encargo.md").json()

    assert estado["status"] == "cancelled"
    assert estado["error"]["code"] == "cancelled_by_client"
    assert estado["error"]["message"] == "el usuario cambio de idea"


def test_work_that_went_well_carries_no_error(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    client.post("/api/v1/cards", json=PETICION, headers={"Idempotency-Key": "k"})

    estado = client.get("/api/v1/cards/encargo.md").json()

    assert estado["status"] == "queued"
    assert "error" not in estado


# --- El estado interno no se pierde ------------------------------------------


def test_the_internal_state_travels_alongside(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """La proyeccion traduce; no oculta. Quien necesite el detalle lo tiene."""
    _, client = cliente
    client.post("/api/v1/cards", json=PETICION, headers={"Idempotency-Key": "k"})

    estado = client.get("/api/v1/cards/encargo.md").json()

    assert estado["state"] == BoardState.PENDING.value
    assert estado["status"] == "queued"
    assert estado["metadata"]["function"] == "transform"
    assert estado["metadata"]["attempts"] == 0


def test_the_projection_never_replaces_the_internal_state(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    client.post("/api/v1/cards", json=PETICION, headers={"Idempotency-Key": "k"})
    client.post(
        "/api/v1/cards/encargo.md/cancel",
        json={"reason": "ya no"},
        headers={"Idempotency-Key": "c"},
    )

    estado = client.get("/api/v1/cards/encargo.md").json()

    assert estado["state"] == BoardState.ARCHIVE.value
    assert estado["status"] == "cancelled"


# --- Tambien en el cursor ----------------------------------------------------


def test_the_event_carries_the_client_status_too(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """Quien sondea no deberia traducir carpetas por su cuenta."""
    _, client = cliente
    client.post("/api/v1/cards", json=PETICION, headers={"Idempotency-Key": "k"})
    client.post(
        "/api/v1/cards/encargo.md/cancel",
        json={"reason": "ya no"},
        headers={"Idempotency-Key": "c"},
    )

    eventos = client.get("/api/v1/events").json()["events"]

    assert eventos[-2]["data"]["status"] == "queued"
    assert eventos[-1]["data"]["status"] == "cancelled"
    assert eventos[-1]["data"]["terminal"] is True


def test_an_event_of_a_cancelled_card_explains_itself(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = cliente
    client.post("/api/v1/cards", json=PETICION, headers={"Idempotency-Key": "k"})
    client.post(
        "/api/v1/cards/encargo.md/cancel",
        json={"reason": "se acabo el plazo"},
        headers={"Idempotency-Key": "c"},
    )

    ultimo = client.get("/api/v1/events").json()["events"][-1]

    assert ultimo["data"]["error"]["code"] == "cancelled_by_client"
    assert ultimo["data"]["error"]["message"] == "se acabo el plazo"


def test_the_state_key_is_still_there_for_whoever_used_it(
    cliente: tuple[AgoraApplication, TestClient],
) -> None:
    """Compatibilidad: `status` es una clave mas, no un reemplazo."""
    _, client = cliente
    client.post("/api/v1/cards", json=PETICION, headers={"Idempotency-Key": "k"})

    evento = client.get("/api/v1/events").json()["events"][-1]

    assert evento["data"]["state"] == BoardState.PENDING.value
    assert evento["data"]["status"] == "queued"
