"""Auditoria humana del 14-oct-2026 (AG-01…AG-06): cada hallazgo, reproducido.

Informe: `D:\\Desarrollo\\docs\\Pruebas\\Auditoria_Agora_2026-10-14`. Los fallos
de mas peso eran de **coherencia entre procesos**: el API y la ventana de
escritorio son dos `AgoraApplication` distintas sobre el mismo workspace, y
aqui se montan asi, no compartiendo un objeto que en produccion no comparten.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from fastapi.testclient import TestClient

from agora.api import ApiSettings, create_api
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.cards import Card
from conftest import create_card, write_profile

ARTEFACTO = [{"name": "out.txt", "content_base64": "aG9sYQ=="}]

TOKEN = "auditoria-20261014-token-0123456789"

PETICION: dict[str, Any] = {
    "filename": "encargo.md",
    "function": "transform",
    "request": "summarize document",
}


@pytest.fixture
def api(tmp_path: Path) -> tuple[AgoraApplication, TestClient]:
    write_profile(
        tmp_path / "AGENTS", "summarizer", handles=["summarize"], skills=["summarize-text"]
    )
    application = AgoraApplication(tmp_path)
    app = create_api(
        application, ApiSettings(token=TOKEN, principal="provisional-client")
    )
    return application, TestClient(
        app, base_url="https://testserver", headers={"Authorization": f"Bearer {TOKEN}"}
    )


def _crear(client: TestClient, **extra: Any) -> Any:
    cuerpo = {**PETICION, **extra}
    return client.post(
        "/api/v1/cards", json=cuerpo, headers={"Idempotency-Key": f"k-{sorted(extra.items())}"}
    )


# --- AG-01: la ventana confia en lo que el API ya autentico -------------------


def test_desktop_round_does_not_block_a_card_created_through_the_api(
    api: tuple[AgoraApplication, TestClient], tmp_path: Path
) -> None:
    """Reproducia 2/2: «Untrusted origin: provisional-client» al pulsar Ejecutar ronda."""
    _, client = api
    assert _crear(client).status_code == 201

    escritorio = AgoraApplication(tmp_path)  # otro proceso: no comparte memoria
    escritorio.dispatch_once()

    assert not (escritorio.board.directory(BoardState.BLOCKED) / "encargo.md").exists()
    assert (escritorio.board.directory(BoardState.DONE) / "encargo.md").is_file()


def test_an_oauth_subject_that_creates_a_card_becomes_trusted(tmp_path: Path) -> None:
    """No solo el principal provisional: cualquier sujeto autenticado con cards:write."""
    from agora.api.contracts import CreateCardRequest
    from agora.api.service import RemoteWorkService

    application = AgoraApplication(tmp_path)
    application.initialize()
    RemoteWorkService(application).create_card(
        CreateCardRequest(**PETICION), principal="gestion-tareas"
    )

    assert "gestion-tareas" in AgoraApplication(tmp_path).trusted_origins


def test_a_hand_dropped_card_is_still_blocked_by_the_desktop(
    api: tuple[AgoraApplication, TestClient], tmp_path: Path
) -> None:
    escritorio = AgoraApplication(tmp_path)
    create_card(escritorio.board, "colada.md", origin="quien-sea")

    escritorio.dispatch_once()

    assert (escritorio.board.directory(BoardState.BLOCKED) / "colada.md").is_file()


def test_an_unreadable_trust_file_closes_and_is_visible(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    application.trust_origin("cliente")
    (tmp_path / ".agora" / "trusted-origins.json").write_text("{roto", encoding="utf-8")

    assert "cliente" not in application.trusted_origins
    assert any("trusted-origins.json" in error.source for error in application.snapshot().errors)


# --- AG-02: Errores y detalle reflejan la ronda -------------------------------


def test_errors_tab_and_detail_follow_a_card_the_round_blocked(tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from agora.desktop.window import AgoraMainWindow

    qt = QApplication.instance() or QApplication([])
    service = AgoraApplication(tmp_path)
    service.initialize()
    create_card(service.board, "colada.md", origin="quien-sea")
    window = AgoraMainWindow(service, refresh_interval_ms=0)
    pendientes = window.board_tree.topLevelItem(0)
    assert pendientes is not None
    window.board_tree.setCurrentItem(pendientes.child(0))
    qt.processEvents()
    assert window.card_location.text().startswith("pending")

    window.run_dispatcher()
    qt.processEvents()

    assert window.card_location.text() == "blocked / colada.md"
    assert "Untrusted origin: quien-sea" in window.card_blocked.text()
    assert window.error_summary.text() != "Sin errores"
    assert "colada.md" in window.error_view.toPlainText()
    assert window.dispatcher_badge.text() == "Despachador: en espera"
    window.close()


# --- AG-03: errores del API sin rutas del servidor ----------------------------


def test_closing_a_card_that_is_not_in_progress_is_a_clean_409(
    api: tuple[AgoraApplication, TestClient], tmp_path: Path
) -> None:
    _, client = api
    _crear(client)

    respuesta = client.post(
        "/api/v1/cards/encargo.md/close",
        json={"runner_id": "r1", "artifacts": ARTEFACTO, "model": "m"},
        headers={"Idempotency-Key": "cerrar"},
    )

    assert respuesta.status_code == 409
    assert respuesta.json()["detail"] == "CARD is not in progress (state: pending)"


@pytest.mark.parametrize("accion", ["close", "progress", "yield"])
def test_no_error_detail_reveals_a_server_path(
    api: tuple[AgoraApplication, TestClient], tmp_path: Path, accion: str
) -> None:
    _, client = api
    cuerpos = {
        "close": {"runner_id": "r1", "artifacts": ARTEFACTO, "model": "m"},
        "progress": {"runner_id": "r1", "milestones": ["x"]},
        "yield": {"runner_id": "r1", "reason": "x"},
    }

    respuesta = client.post(
        f"/api/v1/cards/no-existe.md/{accion}",
        json=cuerpos[accion],
        headers={"Idempotency-Key": f"nada-{accion}"},
    )

    assert respuesta.status_code == 404
    detalle = respuesta.json()["detail"]
    assert ":\\" not in detalle and str(tmp_path) not in detalle


def test_a_wrong_state_lookup_does_not_leak_the_path(
    api: tuple[AgoraApplication, TestClient], tmp_path: Path
) -> None:
    _, client = api
    _crear(client)

    respuesta = client.get("/api/v1/cards/done/encargo.md")

    assert respuesta.status_code == 404
    assert ":\\" not in respuesta.json()["detail"]


# --- AG-04: «nadie la va a recoger» no es «en cola» ---------------------------


def test_a_request_without_any_handle_is_projected_as_unmatched(
    api: tuple[AgoraApplication, TestClient],
) -> None:
    _, client = api
    _crear(client, request="Desglosar el informe en pasos")

    estado = client.get("/api/v1/cards/encargo.md").json()

    assert estado["state"] == BoardState.PENDING.value
    assert estado["status"] == "blocked"
    assert estado["terminal"] is False
    assert estado["error"]["code"] == "no_matching_profile"


# --- AG-05: prioridad con dominio ---------------------------------------------


@pytest.mark.parametrize("valor", ["baja", 1001, -1, True, "7"])
def test_priority_outside_the_domain_is_rejected(
    api: tuple[AgoraApplication, TestClient], valor: object
) -> None:
    _, client = api

    assert _crear(client, priority=valor).status_code == 422


@pytest.mark.parametrize("valor", ["low", "normal", "high", "urgent", 0, 7, 1000])
def test_priority_inside_the_domain_is_accepted(
    api: tuple[AgoraApplication, TestClient], valor: object
) -> None:
    _, client = api

    assert _crear(client, priority=valor).status_code == 201


def test_named_priorities_order_the_queue() -> None:
    def puntuacion(valor: str) -> int:
        return Card.create(function="f", request="r", origin="o", priority=valor).priority_score

    assert puntuacion("low") < puntuacion("normal") < puntuacion("high") < puntuacion("urgent")


def test_the_window_shows_priorities_in_spanish() -> None:
    from agora.desktop.window import _priority_label

    assert _priority_label("high") == "alta"
    assert _priority_label("7") == "7 /1000"
    assert "no reconocida" in _priority_label("baja")
