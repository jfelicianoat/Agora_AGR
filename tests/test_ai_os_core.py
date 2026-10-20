"""AI OS Core dirige el runner: sin su ALLOW ninguna tarjeta llega al AI_Broker.

Core se simula con `httpx.MockTransport` (formas de Core 0.2.1); el recorrido contra
el Core real está en `scripts/verify_ai_os_core.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from agora.board import BoardState
from agora.broker.ai_os_core import AgoraCoreGate
from agora.cards import Card
from conftest import create_card
from test_ai_runner import _runner
from test_broker import FakeBroker


class FakeCore:
    def __init__(
        self, authorization: str = "ALLOW", *, down: bool = False, priority: int | None = None
    ) -> None:
        self.authorization = authorization
        self.priority = priority
        self.down = down
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("Core apagado")
        body = json.loads(request.content or b"{}")
        assert request.headers["X-Principal-Id"] == "agora"
        assert request.headers["Authorization"] == "Bearer tok-agora"
        self.calls.append((request.url.path, body))
        if request.url.path == "/api/v1/policies/evaluate":
            allow = self.authorization == "ALLOW"
            return httpx.Response(
                200,
                json={
                    "authorization": self.authorization,
                    "executable": allow,
                    "unknown_inputs": [],
                    "broker_priority": self.priority,
                },
            )
        return httpx.Response(201, json={})

    def gate(self) -> AgoraCoreGate:
        transport = httpx.MockTransport(self.handler)
        return AgoraCoreGate("http://core", "tok-agora", transport=transport)

    def receipts(self) -> list[dict[str, Any]]:
        return [body for path, body in self.calls if path == "/api/v1/operations/receipts"]

    def hops(self) -> list[dict[str, Any]]:
        return [body for path, body in self.calls if path == "/api/v1/flows/hops"]


@pytest.mark.parametrize("decision", ["DENY", "REQUIRE_APPROVAL", "INDETERMINATE"])
def test_denied_card_stays_pending_and_never_reaches_broker(tmp_path: Path, decision: str) -> None:
    fake, core = FakeBroker(), FakeCore(decision)
    application, runner = _runner(tmp_path, fake)
    runner.core = core.gate()
    create_card(application.board)

    outcome = runner.run_once()

    assert outcome.status == "core_denied"
    pending = Card.load(application.board.directory(BoardState.PENDING) / "task.md")
    assert pending.attempts == 0
    assert fake.submissions == []
    (evaluation,) = [body for path, body in core.calls]
    assert evaluation["scope"] == "agora:broker"
    context = dict(evaluation["context"])
    # Sin flujo propio, el de la tarea en el Broker (monitor de Core)
    assert context.pop("flow_id").startswith("agora:")
    assert context == {
        "operation_type": "card",
        "profile": "summarizer",
        "data_classification": "internal",
    }


def test_card_urgency_goes_to_the_broker_with_its_scale_and_the_core_turn() -> None:
    from agora.broker.request_builder import broker_priority

    # Tablero: mayor = más urgente. Broker: menor = antes.
    assert [broker_priority(s) for s in (1000, 500, 100, 50)] == [10, 20, 100, 200]
    assert broker_priority(0) == 1000 and broker_priority(7) == 1000  # nunca fuera de 0–1000
    # El turno del monitor de Core es la base: una normal va en él, una urgente antes
    assert broker_priority(100, 500) == 500 and broker_priority(1000, 500) == 50
    assert broker_priority(100, 0) == 0


def test_the_core_turn_reaches_the_broker_body(tmp_path: Path) -> None:
    fake, core = FakeBroker(), FakeCore(priority=500)
    application, runner = _runner(tmp_path, fake)
    runner.core = core.gate()
    create_card(application.board, priority="urgent")

    assert runner.run_once().status == "completed"
    assert fake.submissions[0]["priority"] == 50


def test_core_down_claims_nothing(tmp_path: Path) -> None:
    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake)
    runner.core = FakeCore(down=True).gate()
    create_card(application.board)

    outcome = runner.run_once()

    assert outcome.status == "core_unavailable"
    assert (application.board.directory(BoardState.PENDING) / "task.md").exists()
    assert fake.submissions == []


def test_allowed_card_runs_and_leaves_receipts(tmp_path: Path) -> None:
    fake, core = FakeBroker(), FakeCore()
    application, runner = _runner(tmp_path, fake)
    runner.core = core.gate()
    create_card(application.board)

    outcome = runner.run_once()

    assert outcome.status == "completed"
    assert len(fake.submissions) == 1
    receipts = core.receipts()
    assert [r["status"] for r in receipts] == ["accepted", "succeeded"]
    # La operación en Core es la misma clave con la que se mandó al broker
    assert {r["operation_id"] for r in receipts} == {fake.submissions[0]["idempotency_key"]}
    assert receipts[0]["evidence"]["card"] == "task.md"


def test_claimed_unsent_card_is_released_without_attempt_when_denied(tmp_path: Path) -> None:
    """El runner murió entre el claim y el envío: al volver, Core decide igual."""
    fake, core = FakeBroker(), FakeCore("DENY")
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)
    work = runner.agora.work(("summarizer",))[0]
    runner.agora.claim(
        work.filename, runner_id="ai-runner", profile=work.profile, idempotency_key="k:claim"
    )
    runner.core = core.gate()

    outcome = runner.run_once()

    assert outcome.status == "core_denied"
    pending = Card.load(application.board.directory(BoardState.PENDING) / "task.md")
    assert pending.attempts == 0
    assert fake.submissions == []


def test_receipt_lost_does_not_fail_the_card(tmp_path: Path) -> None:
    fake, core = FakeBroker(), FakeCore()
    application, runner = _runner(tmp_path, fake)
    gate = core.gate()

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/receipts"):
            return httpx.Response(503, json={})
        return core.handler(request)

    gate._http._transport = httpx.MockTransport(flaky)
    runner.core = gate
    create_card(application.board)

    assert runner.run_once().status == "completed"


def test_card_from_another_app_keeps_its_flow_to_the_broker(tmp_path: Path) -> None:
    """Gestión Tareas IA -> Agora -> Broker se ve como una sola petición en el monitor."""
    fake, core = FakeBroker(), FakeCore()
    application, runner = _runner(tmp_path, fake)
    runner.core = core.gate()
    card = Card.create(function="transform", request="summarize document", origin="test")
    card.metadata["flow_id"] = "gti_tarea_17"
    application.board.create("task.md", card)

    assert runner.run_once().status == "completed"

    evaluation = next(body for path, body in core.calls if path.endswith("/evaluate"))
    assert evaluation["context"]["flow_id"] == "gti_tarea_17"
    assert {r["evidence"]["flow_id"] for r in core.receipts()} == {"gti_tarea_17"}
    hops = core.hops()
    assert [h["kind"] for h in hops] == ["request", "response"]
    assert {(h["flow_id"], h["source_system"], h["target_system"]) for h in hops} == {
        ("gti_tarea_17", "agora", "ai_broker")
    }
    assert hops[1]["details"]["latency_ms"] >= 0


def test_monitor_down_does_not_touch_the_card(tmp_path: Path) -> None:
    fake, core = FakeBroker(), FakeCore()
    application, runner = _runner(tmp_path, fake)
    gate = core.gate()

    def no_monitor(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/flows/hops":
            raise httpx.ConnectError("monitor caído")
        return core.handler(request)

    gate._http._transport = httpx.MockTransport(no_monitor)
    runner.core = gate
    create_card(application.board)

    assert runner.run_once().status == "completed"
    assert [r["status"] for r in core.receipts()] == ["accepted", "succeeded"]


def test_flow_id_enters_through_the_api(tmp_path: Path) -> None:
    """La app que deposita la tarjeta manda su flow_id y el runner lo recibe en el WorkItem."""
    fake, core = FakeBroker(), FakeCore()
    _application, runner = _runner(tmp_path, fake)
    runner.core = core.gate()
    client = runner.agora.client  # type: ignore[attr-defined]
    body = {"filename": "flujo.md", "function": "transform", "request": "summarize"}
    bad = client.post(
        "/api/v1/cards", headers={"Idempotency-Key": "c0"}, json={**body, "flow_id": "con espacios"}
    )
    assert bad.status_code == 422
    ok = client.post(
        "/api/v1/cards", headers={"Idempotency-Key": "c1"}, json={**body, "flow_id": "gti_tarea_9"}
    )
    assert ok.status_code == 201, ok.text
    (work,) = runner.agora.work(("summarizer",))
    assert work.flow_id == "gti_tarea_9"
    assert runner.run_once().status == "completed"
    assert {h["flow_id"] for h in core.hops()} == {"gti_tarea_9"}


def test_runner_shares_broker_token_on_change_and_every_minute() -> None:
    """Las apps del PC principal piden a Core el token del broker; el runner lo repone."""
    core, now = FakeCore(), [0.0]
    gate = AgoraCoreGate(
        "http://core", "tok-agora", transport=httpx.MockTransport(core.handler),
        clock=lambda: now[0],
    )

    def shared() -> list[str]:
        return [b["token"] for p, b in core.calls if p == "/api/v1/credentials/broker"]

    assert gate.share_broker_token("broker-1") is True
    now[0] = 30.0
    assert gate.share_broker_token("broker-1") is False  # nada nuevo: no se molesta a Core
    assert gate.share_broker_token("broker-2") is True  # el broker se reinició
    now[0] = 95.0
    assert gate.share_broker_token("broker-2") is True  # Core pudo reiniciarse y perderlo
    assert shared() == ["broker-1", "broker-2", "broker-2"]

    core.down = True
    now[0] = 500.0
    assert gate.share_broker_token("broker-3") is False  # Core caído no tumba al runner
    core.down = False
    assert gate.share_broker_token("broker-3") is True  # y se reintenta en la siguiente vuelta
