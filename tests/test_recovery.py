"""A10: que un trabajo de IA sobreviva a que se caiga cualquiera de las piezas.

Lo caro aqui no es que algo falle: es que al recuperarse **se ejecute dos
veces**. Una tarea de IA cuesta minutos y dinero, y un artefacto duplicado o un
estado reescrito llegan al cliente como si fueran buenos.

Por eso casi todas estas pruebas terminan mirando lo mismo: cuantas veces se
mando trabajo al broker de verdad.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from agora.board import BoardState
from agora.broker.client import BrokerClient
from agora.broker.contracts import BrokerPolicy
from agora.broker.runner import AiRunner
from agora.cards import Card
from conftest import create_card
from test_ai_runner import _runner
from test_broker import FakeBroker

#: Las pruebas de reanudacion miden envios al broker, no relojes. La fixture de
#: `_runner` trae `zombie_timeout_seconds=3` —bien para las pruebas de zombi— y
#: eso hacia que bajo carga el reinicio cruzara el umbral y la tarjeta se
#: cancelara como zombi en vez de reanudarse: un fallo intermitente que no decia
#: nada del codigo. Con una ventana amplia, lo que se prueba es lo que se quiere
#: probar. El umbral tiene sus propias pruebas mas abajo.
SIN_PRISA = BrokerPolicy(timeout_seconds=1, zombie_timeout_seconds=3600)


def _reclaimed(application: Any, runner: AiRunner) -> AiRunner:
    """Un runner nuevo con el mismo id: lo que queda tras reiniciar el proceso.

    Comparte el cliente del tablero y el ejecutor porque eso es lo que sobrevive
    a un reinicio de verdad —el tablero y el disco—, no el objeto en memoria.
    """
    return AiRunner(
        runner.agora,
        runner.broker,
        runner.executor,
        runner.runner_id,
        runner.profiles,
        runner.attachment_wait_seconds,
        runner.renew_broker,
    )


def _claim_with_task(application: Any, fake: FakeBroker, *, when: datetime) -> None:
    """Deja una tarjeta reclamada y con tarea del broker en marcha."""
    create_card(application.board)
    claimed = application.board.claim("task.md", "ai-runner", when=when)
    card = Card.load(claimed)
    card.metadata["profile"] = "summarizer"
    card.metadata["remote"] = {
        "system": "ai_broker",
        "task_id": fake.task_id,
        "idempotency_key": "broker-key",
    }
    card.save()


# --- Estados recuperables y terminales ---------------------------------------


def test_every_state_falls_in_exactly_one_category() -> None:
    """Sin esto, la clasificacion seria una opinion y no una particion."""
    for state in BoardState:
        categorias = [state.is_terminal, state.is_in_flight, state.needs_intervention]
        assert sum(categorias) == 1, state


def test_finished_work_is_terminal() -> None:
    assert BoardState.DONE.is_terminal
    assert BoardState.ARCHIVE.is_terminal


def test_blocked_is_stopped_but_not_finished() -> None:
    """Se paro, que no es lo mismo que haber terminado: alguien puede reanudarla."""
    assert BoardState.BLOCKED.is_terminal is False
    assert BoardState.BLOCKED.needs_intervention is True


def test_work_that_moves_on_its_own_is_in_flight() -> None:
    for state in (BoardState.PENDING, BoardState.IN_PROGRESS, BoardState.SCHEDULED):
        assert state.is_in_flight, state
        assert state.is_terminal is False, state


# --- El runner se reinicia ---------------------------------------------------


def test_a_restarted_runner_resumes_instead_of_submitting_again(
    tmp_path: Path,
) -> None:
    """La prueba central de la fase.

    El runner manda el trabajo, se muere antes de recogerlo, y vuelve. Tiene que
    **retomar la misma tarea del broker**, no mandar otra. Si mandara otra, el
    usuario pagaria dos veces y recibiria un resultado distinto del que ya se
    estaba calculando.
    """
    fake = FakeBroker()
    fake.task_statuses = ["running"]
    application, runner = _runner(tmp_path, fake, policy=SIN_PRISA)
    create_card(application.board)

    primero = runner.run_once()
    assert primero.status == "broker_running", primero.detail
    assert len(fake.submissions) == 1
    claimed = Card.load(application.board.directory(BoardState.IN_PROGRESS) / "task.md")
    task_id = claimed.metadata["remote"]["task_id"]

    # El trabajo termina mientras el runner esta muerto.
    fake.task_statuses = ["completed"]
    resucitado = _reclaimed(application, runner)
    segundo = resucitado.run_once()

    assert segundo.status == "completed", segundo.detail
    assert len(fake.submissions) == 1, "el runner reiniciado volvio a mandar el trabajo"
    cerrada = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    assert cerrada.metadata["remote"]["task_id"] == task_id


def test_a_restarted_runner_takes_its_own_claimed_card_before_looking_for_new_work(
    tmp_path: Path,
) -> None:
    """Si buscara trabajo nuevo primero, dejaria el suyo huerfano y cogeria otro."""
    fake = FakeBroker()
    fake.task_statuses = ["running"]
    application, runner = _runner(tmp_path, fake, policy=SIN_PRISA)
    create_card(application.board, "primera.md")
    runner.run_once()
    create_card(application.board, "segunda.md")

    resucitado = _reclaimed(application, runner)
    outcome = resucitado.run_once()

    assert outcome.card == "primera.md"
    assert application.board.paths(BoardState.PENDING)[0].name == "segunda.md"


def test_a_restarted_runner_with_the_broker_down_touches_nothing(
    tmp_path: Path,
) -> None:
    """Recuperarse con el broker caido no puede consumir un intento."""
    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake, policy=SIN_PRISA)
    _claim_with_task(application, fake, when=datetime.now(UTC))

    def offline(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("PC de IA apagado")

    resucitado = _reclaimed(application, runner)
    resucitado.broker = BrokerClient(
        "http://127.0.0.1:8765", fake.token, transport=httpx.MockTransport(offline)
    )

    outcome = resucitado.run_once()

    assert outcome.status == "broker_unavailable"
    en_curso = Card.load(application.board.directory(BoardState.IN_PROGRESS) / "task.md")
    assert en_curso.attempts == 0
    assert fake.cancelled == []


def test_recovering_the_same_card_twice_still_submits_once(tmp_path: Path) -> None:
    """Varios reinicios seguidos no multiplican el trabajo."""
    fake = FakeBroker()
    fake.task_statuses = ["running"]
    application, runner = _runner(tmp_path, fake, policy=SIN_PRISA)
    create_card(application.board)
    runner.run_once()

    for _ in range(2):
        assert _reclaimed(application, runner).run_once().status == "broker_running"
    fake.task_statuses = ["completed"]
    final = _reclaimed(application, runner).run_once()

    assert final.status == "completed", final.detail
    assert len(fake.submissions) == 1


# --- Idempotencia de progress y close ----------------------------------------


def test_closing_twice_with_the_same_key_does_not_duplicate_the_artifact(
    tmp_path: Path,
) -> None:
    """Un `close` que se pierde en la red se reintenta, y no puede duplicar nada."""
    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)
    runner.agora.claim(  # type: ignore[attr-defined]
        "task.md", runner_id="ai-runner", profile="summarizer", idempotency_key="c"
    )
    cierre = {
        "runner_id": "ai-runner",
        "artifacts": [{"name": "final.md", "content_base64": "cHJpbWVybw=="}],
        "model": None,
        "idempotency_key": "cierre-unico",
    }

    primero = runner.agora.close_card("task.md", **cierre)  # type: ignore[attr-defined]
    segundo = runner.agora.close_card(  # type: ignore[attr-defined]
        "task.md",
        **{**cierre, "artifacts": [{"name": "final.md", "content_base64": "cHJpbWVybw=="}]},
    )

    assert primero["replayed"] is False
    assert segundo["replayed"] is True
    assert segundo["state"] == primero["state"] == BoardState.DONE.value
    cerrada = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    assert len(cerrada.metadata["paths"]) == 1


def test_closing_with_the_same_key_but_other_artifacts_is_a_conflict(
    tmp_path: Path,
) -> None:
    """Reusar la clave para otra cosa no puede pasar por un reintento."""
    from agora.remote.client import AgoraApiError

    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)
    runner.agora.claim(  # type: ignore[attr-defined]
        "task.md", runner_id="ai-runner", profile="summarizer", idempotency_key="c"
    )
    runner.agora.close_card(  # type: ignore[attr-defined]
        "task.md",
        runner_id="ai-runner",
        artifacts=[{"name": "final.md", "content_base64": "cHJpbWVybw=="}],
        model=None,
        idempotency_key="cierre-unico",
    )

    with pytest.raises(AgoraApiError) as error:
        runner.agora.close_card(  # type: ignore[attr-defined]
            "task.md",
            runner_id="ai-runner",
            artifacts=[{"name": "otro.md", "content_base64": "c2VndW5kbw=="}],
            model=None,
            idempotency_key="cierre-unico",
        )

    assert error.value.status_code == 409


def test_progress_twice_with_the_same_key_is_recorded_once(tmp_path: Path) -> None:
    fake = FakeBroker()
    fake.task_statuses = ["running"]
    application, runner = _runner(tmp_path, fake, policy=SIN_PRISA)
    create_card(application.board)
    runner.run_once()

    antes = Card.load(application.board.directory(BoardState.IN_PROGRESS) / "task.md")
    hitos_antes = antes.body.count("Remote profile selected")

    for _ in range(2):
        runner.agora.progress(  # type: ignore[attr-defined]
            "task.md",
            runner_id="ai-runner",
            milestones=["un hito repetido"],
            idempotency_key="progreso-repetido",
        )

    despues = Card.load(application.board.directory(BoardState.IN_PROGRESS) / "task.md")
    assert despues.body.count("un hito repetido") == 1
    assert despues.body.count("Remote profile selected") == hitos_antes


def test_a_different_key_records_a_new_milestone(tmp_path: Path) -> None:
    """La idempotencia no puede silenciar progreso legitimo."""
    fake = FakeBroker()
    fake.task_statuses = ["running"]
    application, runner = _runner(tmp_path, fake, policy=SIN_PRISA)
    create_card(application.board)
    runner.run_once()

    for indice in range(2):
        runner.agora.progress(  # type: ignore[attr-defined]
            "task.md",
            runner_id="ai-runner",
            milestones=[f"hito {indice}"],
            idempotency_key=f"progreso-{indice}",
        )

    card = Card.load(application.board.directory(BoardState.IN_PROGRESS) / "task.md")
    assert "hito 0" in card.body and "hito 1" in card.body


# --- No resucitar lo terminado -----------------------------------------------


def test_a_finished_card_is_not_offered_as_work_again(tmp_path: Path) -> None:
    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)
    assert runner.run_once().status == "completed"

    otra_vuelta = runner.run_once()

    assert otra_vuelta.status == "idle"
    assert len(fake.submissions) == 1


def test_a_restarted_runner_does_not_resurrect_a_finished_card(
    tmp_path: Path,
) -> None:
    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)
    runner.run_once()

    resucitado = _reclaimed(application, runner)
    outcome = resucitado.run_once()

    assert outcome.status == "idle"
    assert len(fake.submissions) == 1
    assert application.board.paths(BoardState.DONE)[0].name == "task.md"


def test_closing_a_card_nobody_claimed_is_refused(tmp_path: Path) -> None:
    from agora.remote.client import AgoraApiError

    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)

    with pytest.raises(AgoraApiError) as error:
        runner.agora.close_card(  # type: ignore[attr-defined]
            "task.md",
            runner_id="ai-runner",
            artifacts=[{"name": "x.md", "content_base64": "eA=="}],
            model=None,
            idempotency_key="cierre-sin-claim",
        )

    assert error.value.status_code in {404, 409}


# --- Zombies -----------------------------------------------------------------


def test_a_zombie_is_cancelled_in_the_broker_before_being_retried(
    tmp_path: Path,
) -> None:
    """Devolver la tarjeta sin cancelar dejaria la tarea corriendo y pagandose."""
    fake = FakeBroker()
    fake.task_statuses = ["generating"]
    application, runner = _runner(tmp_path, fake)
    viejo = datetime(2026, 9, 1, 12, tzinfo=UTC)
    _claim_with_task(application, fake, when=viejo)
    work = runner.agora.claimed("ai-runner")[0]

    outcome = runner._recover(work, now=viejo + timedelta(hours=99))

    assert outcome.status == "failed"
    assert fake.cancelled == [fake.task_id]


def test_the_zombie_threshold_comes_from_the_profile_policy(tmp_path: Path) -> None:
    """Un perfil lento no puede compartir umbral con uno rapido."""
    fake = FakeBroker()
    fake.task_statuses = ["generating"]
    application, runner = _runner(tmp_path, fake)
    viejo = datetime(2026, 9, 1, 12, tzinfo=UTC)
    _claim_with_task(application, fake, when=viejo)
    work = runner.agora.claimed("ai-runner")[0]
    umbral = runner.executor.policy_for_profile_name("summarizer").zombie_timeout_seconds

    dentro = runner._recover(work, now=viejo + timedelta(seconds=umbral - 1))

    assert dentro.status != "failed"
    assert fake.cancelled == []


def _claim_without_task(application: Any, *, when: datetime) -> None:
    """Reclamada y **sin** `task_id`: el runner murio antes de enviar nada.

    Es la ventana de `executor.py`, donde el `task_id` se apunta despues del
    `submit()`. Todo lo anterior —el contrato, que es red; las skills, que son
    disco; los adjuntos; el propio envio— puede colgarse ahi.
    """
    create_card(application.board)
    claimed = application.board.claim("task.md", "ai-runner", when=when)
    card = Card.load(claimed)
    card.metadata["profile"] = "summarizer"
    card.save()


def test_a_claim_that_never_reached_the_broker_still_ages_out(tmp_path: Path) -> None:
    """La caducidad no puede vivir dentro de la rama del `task_id`.

    Vivia ahi, y por eso una tarjeta reclamada por un runner que murio antes de
    `submit()` se quedaba `in-progress` con `attempts: 0` para siempre. Visto en
    vivo el 19-09-2026: nueve horas y media.
    Ver `docs/HALLAZGO_20260919_TARJETA_HUERFANA.md`.
    """
    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake)
    viejo = datetime(2026, 9, 1, 12, tzinfo=UTC)
    _claim_without_task(application, when=viejo)
    work = runner.agora.claimed("ai-runner")[0]

    outcome = runner._recover(work, now=viejo + timedelta(hours=99))

    assert outcome.detail == "abandoned claim released"
    # Gasta intento: a los tres acaba en `blocked`, donde decide una persona.
    assert Card.load(application.board.directory(BoardState.PENDING) / "task.md").attempts == 1
    # Y no se paga nada por rescatarla: nunca hubo tarea que cancelar.
    assert fake.submissions == []
    assert fake.cancelled == []


def test_a_fresh_claim_without_task_id_is_executed_not_released(tmp_path: Path) -> None:
    """El arreglo no puede llevarse por delante el arranque normal.

    Entre reclamar y enviar hay un hueco legitimo de segundos; soltar ahi seria
    cambiar una tarjeta huerfana por trabajo perdido en cada latido.
    """
    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake)
    ahora = datetime(2026, 9, 1, 12, tzinfo=UTC)
    _claim_without_task(application, when=ahora)
    work = runner.agora.claimed("ai-runner")[0]

    outcome = runner._recover(work, now=ahora + timedelta(seconds=1))

    assert outcome.detail != "abandoned claim released"
    assert fake.submissions


# --- Lo que el cliente ve de todo esto ---------------------------------------


def _api_client(tmp_path: Path):
    from fastapi.testclient import TestClient

    from agora.api import ApiSettings, create_api
    from agora.application import AgoraApplication
    from conftest import write_profile

    token = "a10-recovery-token-0123456789"
    application = AgoraApplication(tmp_path)
    application.initialize()
    write_profile(
        tmp_path / "AGENTS", "resumidor", function="transform", handles=["summarize document"]
    )
    app = create_api(
        application, ApiSettings(token=token, principal="cliente", require_https=True)
    )
    return application, TestClient(
        app, base_url="https://testserver", headers={"Authorization": f"Bearer {token}"}
    )


def test_the_client_is_told_when_to_stop_polling(tmp_path: Path) -> None:
    """Sin esto, cada cliente tendria que llevar escrita la lista de estados finales."""
    _, client = _api_client(tmp_path)
    peticion = {
        "filename": "encargo.md",
        "function": "transform",
        "request": "summarize document",
    }
    client.post("/api/v1/cards", json=peticion, headers={"Idempotency-Key": "k"})

    en_vuelo = client.get("/api/v1/cards/encargo.md").json()
    assert en_vuelo["terminal"] is False

    client.post(
        "/api/v1/cards/encargo.md/cancel",
        json={"reason": "ya no hace falta"},
        headers={"Idempotency-Key": "c"},
    )

    acabada = client.get("/api/v1/cards/encargo.md").json()
    assert acabada["state"] == BoardState.ARCHIVE.value
    assert acabada["terminal"] is True


def test_the_event_also_says_whether_the_work_is_over(tmp_path: Path) -> None:
    """Un cliente que sigue el cursor no deberia tener que preguntar ademas."""
    _, client = _api_client(tmp_path)
    peticion = {
        "filename": "encargo.md",
        "function": "transform",
        "request": "summarize document",
    }
    client.post("/api/v1/cards", json=peticion, headers={"Idempotency-Key": "k"})
    client.post(
        "/api/v1/cards/encargo.md/cancel",
        json={"reason": "ya no hace falta"},
        headers={"Idempotency-Key": "c"},
    )

    eventos = client.get("/api/v1/events").json()["events"]

    assert eventos[-2]["data"]["terminal"] is False
    assert eventos[-1]["kind"] == "card.cancelled"
    assert eventos[-1]["data"]["terminal"] is True
