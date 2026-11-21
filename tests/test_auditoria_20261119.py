"""Regresiones de la auditoría integral del 19-nov-2026 (registro en
`docs/Auditoria 20261119/REGISTRO.md`).

Cada prueba afirma el comportamiento esperado; contra 0.2.17 fallan las de
D-01…D-06 y U-01…U-04.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from fastapi.testclient import TestClient

from agora.api.app import ApiSettings, create_api
from agora.api.contracts import RunnerOutcome, WorkItem
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.cards import Card
from agora.dispatcher import DispatchStatus
from agora.remote.runner import _attempt_key
from conftest import create_card, write_document, write_profile

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _workspace(tmp_path: Path) -> AgoraApplication:
    """Un tablero con los perfiles reales de planificación (con contrato de salida)."""
    shutil.copytree(ROOT / "profiles" / "task-planning", tmp_path / "AGENTS")
    application = AgoraApplication(tmp_path)
    application.initialize()
    return application


_OPEN_CLIENTS: list[TestClient] = []


@pytest.fixture(autouse=True)
def _close_clients() -> Iterator[None]:
    """Cierra los `TestClient`: sus hilos vivos al salir hacían abortar a Qt."""
    yield
    while _OPEN_CLIENTS:
        _OPEN_CLIENTS.pop().close()


def _api(application: AgoraApplication) -> TestClient:
    api = create_api(application, ApiSettings(token=TOKEN, require_https=False))
    client = TestClient(api, base_url="http://agora.test")
    _OPEN_CLIENTS.append(client)
    return client


def _post(client: TestClient, url: str, body: Mapping[str, object], key: str) -> dict[str, object]:
    response = client.post(url, json=body, headers={**AUTH, "Idempotency-Key": key})
    assert response.status_code in {200, 201}, response.text
    payload: dict[str, object] = response.json()
    return payload


def _intake(client: TestClient, filename: str = "job.md") -> None:
    _post(
        client,
        "/api/v1/cards",
        {"filename": filename, "function": "understand", "request": "intake: preparar examen"},
        f"create-{filename}",
    )


# --- D-01: la ronda de la ventana no ejecuta tarjetas de IA -----------------


def test_d01_window_round_leaves_ai_cards_for_the_ai_runner(tmp_path: Path) -> None:
    application = _workspace(tmp_path)
    client = _api(application)
    _intake(client)

    outcomes = application.dispatch_once(dry_run=False)

    outcome = next(item for item in outcomes if item.card == "job.md")
    assert outcome.status is DispatchStatus.SKIPPED
    assert (application.board.directory(BoardState.PENDING) / "job.md").is_file()
    assert not (tmp_path / "artifacts" / "job.txt").exists()
    # Y el runner de IA la sigue viendo.
    work = client.get("/api/v1/work", params={"profiles": ["task-intake"]}, headers=AUTH)
    assert [item["filename"] for item in work.json()] == ["job.md"]


def test_d01_profile_pinned_to_broker_harness_is_not_run_locally(tmp_path: Path) -> None:
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"], harness="broker")
    application = AgoraApplication(tmp_path)
    application.initialize()
    create_card(application.board)

    outcome = application.dispatch_once(dry_run=False)[0]

    assert outcome.status is DispatchStatus.SKIPPED
    assert "broker" in outcome.reason
    work = _api(application).get("/api/v1/work", params={"profiles": ["summarizer"]}, headers=AUTH)
    assert [item["filename"] for item in work.json()] == ["task.md"]


def test_d01_local_demo_profiles_still_run_in_the_window(tmp_path: Path) -> None:
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    application = AgoraApplication(tmp_path)
    application.initialize()
    create_card(application.board)

    outcome = application.dispatch_once(dry_run=False)[0]

    assert outcome.status is DispatchStatus.DISPATCHED


# --- D-02: desbloquear una tarjeta agotada la vuelve a poner en marcha ------


def test_d02_unblocking_an_exhausted_card_gives_it_a_new_budget(tmp_path: Path) -> None:
    application = _workspace(tmp_path)
    client = _api(application)
    _intake(client)
    for round_ in range(3):
        _post(client, "/api/v1/cards/job.md/claim",
              {"runner_id": "pc-ia", "profile": "task-intake"}, f"claim-{round_}")
        _post(client, "/api/v1/cards/job.md/yield",
              {"runner_id": "pc-ia", "reason": "fallo", "increment_attempts": True},
              f"yield-{round_}")
    assert client.get("/api/v1/cards/job.md", headers=AUTH).json()["status"] == "failed"

    _post(client, "/api/v1/admin/blocked/job.md/unblock", {"reason": "reintentar"}, "unblock")

    status = client.get("/api/v1/cards/job.md", headers=AUTH).json()
    assert status["status"] == "queued"
    assert status["metadata"]["attempts"] == 3
    assert status["metadata"]["max_attempts"] == 6
    assert "Attempt budget extended from 3 to 6" in status["record"]
    work = client.get("/api/v1/work", params={"profiles": ["task-intake"]}, headers=AUTH)
    assert [item["filename"] for item in work.json()] == ["job.md"]


def test_d02_unblocking_an_untrusted_card_keeps_its_budget(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    create_card(application.board, origin="stranger")
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    application.dispatch_once(dry_run=False)

    application.board.unblock("task.md", actor="human", reason="lo conozco")

    card = Card.load(application.board.directory(BoardState.PENDING) / "task.md")
    assert card.max_attempts == 3


# --- D-03: soltar sin gastar intento no deja al runner con un claim fantasma --


def _work_item(client: TestClient) -> WorkItem:
    items = client.get("/api/v1/work", params={"profiles": ["task-intake"]}, headers=AUTH).json()
    return WorkItem.model_validate(items[0])


def test_d03_reclaim_after_a_free_yield_really_claims(tmp_path: Path) -> None:
    application = _workspace(tmp_path)
    client = _api(application)
    _intake(client)
    claim = {"runner_id": "pc-ia", "profile": "task-intake"}

    first = _attempt_key("pc-ia", _work_item(client))
    _post(client, "/api/v1/cards/job.md/claim", claim, f"{first}:claim")
    _post(client, "/api/v1/cards/job.md/yield",
          {"runner_id": "pc-ia", "reason": "Core denied", "increment_attempts": False},
          f"{first}:core-denied")

    second = _attempt_key("pc-ia", _work_item(client))
    assert second != first
    reply = _post(client, "/api/v1/cards/job.md/claim", claim, f"{second}:claim")

    assert reply["replayed"] is False
    status = client.get("/api/v1/cards/job.md", headers=AUTH).json()
    assert status["state"] == "in-progress"
    assert status["metadata"]["agent"] == "pc-ia"


def test_d03_cards_that_never_went_back_keep_their_old_key() -> None:
    import hashlib

    item = WorkItem(filename="a.md", function="f", request="r", priority="normal",
                    attempts=1, profile="p", card_document="")
    legacy = hashlib.sha256(b"pc\0a.md\x001").hexdigest()
    assert _attempt_key("pc", item) == legacy


# --- D-04: las plantillas programadas se materializan en el latido ----------


def _template(application: AgoraApplication, name: str, trigger: object) -> None:
    write_document(
        application.board.directory(BoardState.SCHEDULED) / name,
        {
            "function": "understand",
            "request": "intake: repaso semanal",
            "created": "2026-01-01T00:00:00Z",
            "origin": "human",
            "paths": [],
            "attempts": 0,
            "trigger": trigger,
        },
        "## Record\n",
    )


def test_d04_the_api_heartbeat_materializes_due_templates(tmp_path: Path) -> None:
    application = _workspace(tmp_path)
    _template(application, "diario.md", {"every": "day"})
    write_document(application.board.directory(BoardState.SCHEDULED) / "roto.md", {"x": 1})
    client = _api(application)

    work = client.get("/api/v1/work", params={"profiles": ["task-intake"]}, headers=AUTH).json()

    today = datetime.now().astimezone().date().isoformat()
    assert [item["filename"] for item in work] == [f"{today}-diario.md"]
    # Idempotente: un segundo latido no duplica.
    client.get("/api/v1/work", params={"profiles": ["task-intake"]}, headers=AUTH)
    assert len(application.board.paths(BoardState.PENDING)) == 1


def test_d04_window_round_materializes_and_simulation_does_not(tmp_path: Path) -> None:
    application = _workspace(tmp_path)
    _template(application, "diario.md", {"every": "day"})

    application.dispatch_once(dry_run=True)
    assert application.board.paths(BoardState.PENDING) == ()

    outcomes = application.dispatch_once(dry_run=False)
    assert any(item.status is DispatchStatus.MATERIALIZED for item in outcomes)
    assert len(application.board.paths(BoardState.PENDING)) == 1


def test_d04_a_broken_template_is_reported_not_fatal(tmp_path: Path) -> None:
    from agora.scheduler import Scheduler

    application = _workspace(tmp_path)
    write_document(application.board.directory(BoardState.SCHEDULED) / "roto.md", {"x": 1})
    _template(application, "diario.md", {"every": "day"})

    results = Scheduler(application.board).materialize(now=datetime(2026, 11, 19, 9, tzinfo=UTC))

    assert [item.error for item in results] == [False, True]
    assert results[0].created


# --- D-05: lo que mueve el latido del API también es un evento --------------


def test_d05_zombie_sweep_publishes_the_failure(tmp_path: Path) -> None:
    application = _workspace(tmp_path)
    client = _api(application)
    _post(client, "/api/v1/cards",
          {"filename": "z.md", "function": "understand", "request": "intake: x",
           "max_attempts": 1}, "c")
    _post(client, "/api/v1/cards/z.md/claim",
          {"runner_id": "pc-ia", "profile": "task-intake"}, "k")
    path = application.board.directory(BoardState.IN_PROGRESS) / "z.md"
    card = Card.load(path)
    card.metadata["claimed"] = "2026-01-01T00:00:00Z"
    card.save(path)
    cursor = client.get("/api/v1/events", headers=AUTH).json()["newest"]

    client.get("/api/v1/work", params={"profiles": ["task-intake"]}, headers=AUTH)

    events = client.get("/api/v1/events", params={"after": cursor}, headers=AUTH).json()["events"]
    assert [(e["kind"], e["data"]["status"]) for e in events] == [("card.recovered", "failed")]


def test_d05_materialized_template_publishes_a_creation(tmp_path: Path) -> None:
    application = _workspace(tmp_path)
    _template(application, "diario.md", {"every": "day"})
    client = _api(application)

    client.get("/api/v1/work", params={"profiles": ["task-intake"]}, headers=AUTH)

    events = client.get("/api/v1/events", headers=AUTH).json()["events"]
    assert [(e["kind"], e["data"]["principal"]) for e in events] == [
        ("card.created", "scheduler")
    ]


# --- D-06: una excepción inesperada no tumba el runner ----------------------


def test_d06_unexpected_error_becomes_a_failed_round() -> None:
    from agora.broker.main import _guarded_run

    class Exploding:
        def run_once(self) -> RunnerOutcome:
            raise KeyError("forma rara")

    outcome = _guarded_run(Exploding())  # type: ignore[arg-type]

    assert outcome.status == "failed"
    assert "KeyError" in outcome.detail


# --- D-07: el README se puede seguir -----------------------------------------


def test_d07_readme_has_no_control_characters() -> None:
    raw = (ROOT / "README.md").read_bytes()
    assert not [byte for byte in raw if byte < 32 and byte not in (9, 10, 13)]
    assert b".\\scripts\\build-runner-package.ps1" in raw


# --- U-01…U-04: la ventana permite recuperar y entender --------------------


def test_u01_application_unblock_and_cancel_need_a_reason(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    create_card(application.board)

    with pytest.raises(ValueError):
        application.cancel("task.md", "  ")
    application.cancel("task.md", "ya no hace falta")

    archived = Card.load(application.board.directory(BoardState.ARCHIVE) / "task.md")
    assert archived.metadata["cancelled"]["reason"] == "ya no hace falta"
    assert "human: CARD cancelled: ya no hace falta" in archived.body


def test_u04_explain_says_why_a_pending_card_does_not_move(tmp_path: Path) -> None:
    application = _workspace(tmp_path)
    client = _api(application)
    _intake(client)
    _post(client, "/api/v1/cards",
          {"filename": "raro.md", "function": "understand", "request": "hazme algo"}, "r")

    assert "runner del PC IA" in application.explain("job.md")
    assert application.explain("raro.md").startswith("Parada: ningún perfil")


def test_u01_u02_u03_window_actions(tmp_path: Path) -> None:
    from PySide6.QtWidgets import QApplication

    from agora.desktop.window import AgoraMainWindow

    existing = QApplication.instance()
    qt = existing if isinstance(existing, QApplication) else QApplication([])
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    application = AgoraApplication(tmp_path)
    application.initialize()
    create_card(application.board, "hecha.md")
    create_card(application.board, "rara.md", origin="stranger")
    window = AgoraMainWindow(application, refresh_interval_ms=0)
    opened: list[Path] = []

    def record_open(path: Path) -> bool:
        opened.append(path)
        return True

    window.open_path = record_open
    window.ask_reason = lambda _title, _label, default: default
    window.confirm = lambda _title, _text: True

    window.run_dispatcher()
    assert window.round_summary.text().startswith("Ronda:")
    assert "1 ejecutada(s)" in window.round_summary.text()

    def select(name: str) -> None:
        for top in range(window.board_tree.topLevelItemCount()):
            parent = window.board_tree.topLevelItem(top)
            assert parent is not None
            for index in range(parent.childCount()):
                child = parent.child(index)
                if child is not None and child.data(1, 0x0100) == name:
                    window.board_tree.setCurrentItem(child)
                    qt.processEvents()
                    return
        raise AssertionError(name)

    select("hecha.md")
    assert window.open_button.isEnabled() and not window.unblock_button.isEnabled()
    window.open_selected_artifact()
    assert opened and opened[0].name == "hecha.txt"

    select("rara.md")
    assert window.unblock_button.isEnabled()
    window.unblock_selected()
    assert (application.board.directory(BoardState.PENDING) / "rara.md").is_file()

    select("rara.md")
    assert window.cancel_button.isEnabled()
    assert window.card_situation.text() != "—"
    window.confirm = lambda _title, _text: False
    window.cancel_selected()
    assert (application.board.directory(BoardState.PENDING) / "rara.md").is_file()
    window.confirm = lambda _title, _text: True
    window.cancel_selected()
    assert (application.board.directory(BoardState.ARCHIVE) / "rara.md").is_file()
    window.close()
    window.deleteLater()
    qt.processEvents()


# --- Arbitraje, ronda 1 (A-1…A-8) -------------------------------------------


def test_a1_instance_is_not_recreated_once_it_leaves_pending(tmp_path: Path) -> None:
    application = _workspace(tmp_path)
    _template(application, "diario.md", {"every": "day"})
    client = _api(application)
    work = client.get("/api/v1/work", params={"profiles": ["task-intake"]}, headers=AUTH).json()
    name = work[0]["filename"]
    _post(client, f"/api/v1/cards/{name}/claim",
          {"runner_id": "pc-ia", "profile": "task-intake"}, "claim")

    again = client.get("/api/v1/work", params={"profiles": ["task-intake"]}, headers=AUTH).json()

    assert again == []
    assert application.board.paths(BoardState.PENDING) == ()
    kinds = [e["kind"] for e in client.get("/api/v1/events", headers=AUTH).json()["events"]]
    assert kinds == ["card.created", "card.claimed"]


def test_a2_free_releases_are_capped(tmp_path: Path) -> None:
    from agora.board import MAX_FREE_RELEASES

    application = _workspace(tmp_path)
    client = _api(application)
    _intake(client)
    for round_ in range(MAX_FREE_RELEASES + 3):  # noqa: B007 - se mira tras el bucle
        items = client.get("/api/v1/work", params={"profiles": ["task-intake"]},
                           headers=AUTH).json()
        if not items:
            break
        key = _attempt_key("pc-ia", WorkItem.model_validate(items[0]))
        _post(client, "/api/v1/cards/job.md/claim",
              {"runner_id": "pc-ia", "profile": "task-intake"}, f"{key}:claim")
        _post(client, "/api/v1/cards/job.md/yield",
              {"runner_id": "pc-ia", "reason": "contract unsupported",
               "increment_attempts": False}, f"{key}:unsupported")

    assert round_ == MAX_FREE_RELEASES
    status = client.get("/api/v1/cards/job.md", headers=AUTH).json()
    assert (status["status"], status["error"]["code"]) == ("blocked", "repeatedly_released")
    # Desbloquear la vuelve a dejar en marcha con el contador a cero.
    _post(client, "/api/v1/admin/blocked/job.md/unblock", {"reason": "broker actualizado"}, "u")
    status = client.get("/api/v1/cards/job.md", headers=AUTH).json()
    assert status["status"] == "queued" and "free_releases" not in status["metadata"]


def test_a3_artifacts_outside_workspace_or_executables_are_not_opened(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    outside = tmp_path.parent / f"{tmp_path.name}-fuera.txt"
    outside.write_text("x", encoding="utf-8")
    inside = tmp_path / "artifacts" / "tool.exe"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"MZ")
    path = create_card(application.board)
    card = Card.load(path)
    card.metadata["paths"] = [str(outside), str(inside)]
    card.save(path)

    found = application.artifacts(BoardState.PENDING, "task.md")

    assert found == (inside.resolve(),)
    assert application.open_target(found[0]) == inside.resolve().parent
    assert application.open_target(tmp_path / "a.md") == tmp_path / "a.md"


def test_a4_a_held_board_lock_serialises_other_processes(tmp_path: Path) -> None:
    import subprocess
    import sys
    import textwrap
    import time

    application = AgoraApplication(tmp_path)
    application.initialize()
    create_card(application.board)
    script = textwrap.dedent(f"""
        import time
        from pathlib import Path
        from agora.board import Board
        board = Board(Path(r"{application.board.root}"))
        with board._mutation_lock():
            print("held", flush=True)
            time.sleep(1.0)
    """)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, env=env,
                             text=True)
    assert child.stdout is not None and child.stdout.readline().strip() == "held"
    began = time.monotonic()
    application.cancel("task.md", "esperó al otro proceso")
    waited = time.monotonic() - began
    child.wait(timeout=10)

    assert waited > 0.5
    assert (application.board.directory(BoardState.ARCHIVE) / "task.md").is_file()


def test_a5_each_unblock_adds_the_original_budget(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    board = application.board
    create_card(board)
    for cycle in range(2):
        while (board.directory(BoardState.PENDING) / "task.md").is_file():
            claimed = board.claim("task.md", "w")
            board.return_pending(claimed, actor="w", reason="fallo")
        board.unblock("task.md", actor="human", reason=f"ronda {cycle}")
        card = Card.load(board.directory(BoardState.PENDING) / "task.md")
        assert card.max_attempts == 3 * (cycle + 2)


def test_a8_instance_does_not_inherit_execution_fields(tmp_path: Path) -> None:
    from agora.scheduler import Scheduler

    application = _workspace(tmp_path)
    _template(application, "diario.md", {"every": "day"})
    path = application.board.directory(BoardState.SCHEDULED) / "diario.md"
    card = Card.load(path)
    card.metadata["remote"] = {"system": "ai_broker", "task_id": "viejo", "idempotency_key": "k"}
    card.metadata["external_reference"] = {"system": "gti", "id": "1"}
    card.save(path)

    Scheduler(application.board).materialize(now=datetime(2026, 11, 19, 9, tzinfo=UTC))

    instance = Card.load(application.board.paths(BoardState.PENDING)[0])
    assert "remote" not in instance.metadata
    assert "external_reference" not in instance.metadata


# --- Arbitraje, ronda 2 (B-1…B-4) -------------------------------------------


def _hold_lock_in_child(board_root: Path, seconds: float) -> subprocess.Popen[str]:
    import textwrap

    script = textwrap.dedent(f"""
        import time
        from pathlib import Path
        from agora.board import Board
        with Board(Path(r"{board_root}"))._mutation_lock():
            print("held", flush=True)
            time.sleep({seconds})
    """)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, env=env,
                             text=True)
    assert child.stdout is not None and child.stdout.readline().strip() == "held"
    return child


def test_b1_api_close_waits_for_a_window_mutation(tmp_path: Path) -> None:
    import base64

    application = _workspace(tmp_path)
    client = _api(application)
    _intake(client)
    _post(client, "/api/v1/cards/job.md/claim",
          {"runner_id": "pc-ia", "profile": "task-intake"}, "claim")
    # «La ventana» cancela con su propio objeto Board mientras el API cierra.
    from agora.board import Board

    window_board = Board(application.board.root)
    with window_board._mutation_lock():
        window_board.cancel("job.md", actor="human", reason="ya no")
    response = client.post(
        "/api/v1/cards/job.md/close",
        json={"runner_id": "pc-ia", "model": "m", "execution_audit": {"x": 1},
              "artifacts": [{"name": "a.json",
                             "content_base64": base64.b64encode(b"{}").decode()}]},
        headers={**AUTH, "Idempotency-Key": "close"},
    )

    assert response.status_code == 409
    places = [s for s in BoardState if (application.board.directory(s) / "job.md").is_file()]
    assert places == [BoardState.ARCHIVE]


def test_b3_busy_board_is_a_503_not_a_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                ) -> None:
    import agora.board as board_module

    application = _workspace(tmp_path)
    client = _api(application)
    monkeypatch.setattr(board_module, "_LOCK_WAIT_SECONDS", 0.3)
    child = _hold_lock_in_child(application.board.root, 2.0)

    response = client.post(
        "/api/v1/cards",
        json={"filename": "x.md", "function": "understand", "request": "intake: x"},
        headers={**AUTH, "Idempotency-Key": "busy"},
    )
    child.wait(timeout=10)

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "2"
    # Reintentar después funciona.
    retry = client.post(
        "/api/v1/cards",
        json={"filename": "x.md", "function": "understand", "request": "intake: x"},
        headers={**AUTH, "Idempotency-Key": "busy"},
    )
    assert retry.status_code == 201


def test_b2_lock_is_released_when_the_holder_dies(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    create_card(application.board)
    child = _hold_lock_in_child(application.board.root, 60.0)
    subprocess.run(["taskkill", "/F", "/PID", str(child.pid)], capture_output=True) \
        if sys.platform == "win32" else child.kill()
    child.wait(timeout=10)

    application.cancel("task.md", "tras morir el otro proceso")

    assert (application.board.directory(BoardState.ARCHIVE) / "task.md").is_file()


def test_b4_a_real_attempt_resets_the_free_release_count(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    board = application.board
    create_card(board, max_attempts=10)
    pending = board.directory(BoardState.PENDING) / "task.md"
    for increment in (False, False, False, False, True, False):
        board.return_pending(board.claim("task.md", "w"), actor="w", reason="x",
                             increment_attempts=increment)

    assert pending.is_file()
    assert Card.load(pending).metadata["free_releases"] == 1


def test_a7_broken_template_is_logged_by_the_api(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    application = _workspace(tmp_path)
    write_document(application.board.directory(BoardState.SCHEDULED) / "roto.md", {"x": 1})
    client = _api(application)

    with caplog.at_level("WARNING", logger="agora.api"):
        client.get("/api/v1/work", params={"profiles": ["task-intake"]}, headers=AUTH)

    assert any("roto.md" in record.getMessage() for record in caplog.records)
