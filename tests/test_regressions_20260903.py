"""Regresiones de los defectos hallados el 2026-09-03 contra el AI_Broker real.

Cada prueba de este módulo nace de un fallo observado ejecutando Agora contra el
broker vivo (contrato 2.9), no contra un doble. Las respuestas literales que se
usan aquí están copiadas de las respuestas reales del broker.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agora.api import ApiSettings, create_api
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.broker.client import BrokerApiError
from agora.broker.contracts import BrokerInvocation, BrokerPolicy, BrokerTaskState
from agora.broker.executor import (
    BrokerExecutor,
    _contract_invocations,
    _result_bytes,
    _validate_strict,
)
from agora.broker.request_builder import build_broker_request
from agora.cards import Card
from agora.profiles import Profile
from conftest import create_card, write_profile

TOKEN = "f2-test-token-0123456789"

# Respuesta real de AI_Broker 2.9 para una tarea completada (task_fc6c590c…).
REAL_BROKER_RESULT: dict[str, object] = {
    "inference_kind": "chat",
    "output_format": "markdown",
    "usage": {"invocations": 1, "tokens_input": 341, "tokens_output": 1070, "cost_usd": 0.0},
    "model_used": {"provider": "lmstudio", "deployment": "local", "model": "laguna-xs-2.1"},
    "models_used": [{"provider": "lmstudio", "deployment": "local", "model": "laguna-xs-2.1"}],
    "fallback_used": False,
    "assistant_content": "# Resumen\n\nEl entregable real de la CARD.",
    "result_markdown": "# Resumen\n\nEl entregable real de la CARD.",
}


def test_broker_result_extracts_the_real_deliverable_not_the_json_envelope() -> None:
    """D1: el broker entrega en `result_markdown`, nunca en `content`.

    Antes, `_result_bytes` no reconocía ninguna clave real y caía al volcado JSON,
    de modo que la CARD se cerraba con el sobre del broker disfrazado de entregable.
    """
    payload = _result_bytes(REAL_BROKER_RESULT)

    assert payload == b"# Resumen\n\nEl entregable real de la CARD."
    assert b"assistant_content" not in payload
    with pytest.raises(json.JSONDecodeError):
        json.loads(payload)


def test_broker_result_without_deliverable_fails_visibly() -> None:
    """D1: un resultado sin texto no puede cerrarse como si fuera un artefacto."""
    with pytest.raises(BrokerApiError):
        _result_bytes({"usage": {"invocations": 1}})


def test_json_output_policy_carries_the_schema_the_broker_demands() -> None:
    """D2: `output.format=json` sin `json_schema` es 422 CONTRACT_VALIDATION_FAILED."""
    with pytest.raises(ValueError, match="output_schema"):
        BrokerPolicy(
            determinism="routed",
            output_format="json",
            timeout_seconds=600,
            zombie_timeout_seconds=900,
        )

    schema = {"type": "object", "properties": {"resumen": {"type": "string"}}}
    policy = BrokerPolicy(
        determinism="routed",
        output_format="json",
        output_schema=schema,
        timeout_seconds=600,
        zombie_timeout_seconds=900,
    )
    request = _request(policy)

    assert request["output"] == {"format": "json", "language": "es", "json_schema": schema}


def test_markdown_policy_never_sends_a_json_schema() -> None:
    """D2: el broker prohíbe campos ajenos; markdown no lleva esquema."""
    assert "json_schema" not in _request(_policy())["output"]


def test_shadow_probe_invocation_does_not_break_strict_determinism() -> None:
    """D3: los roles auxiliares del broker no son la CARD.

    Verificado en vivo sobre 25 tareas reales: `shadow_probe` y `confidence_judge`
    comparten task_id, llegan a `completed`, reportan `generation` y pueden usar otro
    modelo. `confidence_judge` además corre con temperatura propia, así que validar la
    política estricta contra él invalidaba tarjetas correctas.
    """
    policy = BrokerPolicy(
        determinism="strict",
        timeout_seconds=600,
        zombie_timeout_seconds=900,
        target_model={"provider": "lmstudio", "deployment": "local", "model": "laguna-xs-2.1"},
    )
    state = BrokerTaskState(
        task_id="task_1",
        status="completed",
        result=dict(REAL_BROKER_RESULT),
        execution_summary={
            "served_by": {
                "provider": "lmstudio",
                "deployment": "local",
                "model": "laguna-xs-2.1",
            }
        },
    )
    invocations = (
        BrokerInvocation(
            invocation_id="inv_contract",
            role="single",
            model={"provider": "lmstudio", "deployment": "local", "model": "laguna-xs-2.1"},
            status="completed",
            cost_usd=0.25,
            generation={
                "temperature": 0.0,
                "seed": 0,
                "seed_status": "sent",
                "top_p": 1.0,
                "top_p_status": "sent",
            },
        ),
        BrokerInvocation(
            invocation_id="inv_probe",
            role="shadow_probe",
            model={
                "provider": "lmstudio",
                "deployment": "local",
                "model": "huihui-qwen3.8-27b-abliterated",
            },
            status="completed",
            cost_usd=0.75,
            generation={
                "temperature": 0.0,
                "seed": 0,
                "seed_status": "sent",
                "top_p": 1.0,
                "top_p_status": "sent",
            },
        ),
        BrokerInvocation(
            invocation_id="inv_judge",
            role="confidence_judge",
            model={"provider": "lmstudio", "deployment": "local", "model": "laguna-xs-2.1"},
            status="completed",
            cost_usd=0.50,
            generation={"temperature": 0.7, "seed_status": "not_requested"},
        ),
    )

    _validate_strict(state, invocations, policy)
    assert [item.invocation_id for item in _contract_invocations(invocations)] == ["inv_contract"]


def test_card_is_not_billed_for_the_brokers_routing_exploration(tmp_path: Path) -> None:
    """D3: el coste de la CARD es el que el broker declara en `result.usage`.

    Sumar `/invocations` a mano factura a la tarjeta la exploración del broker: en las
    tareas reales `result.usage.invocations` vale 1 mientras el endpoint devuelve 2.
    """
    executor = BrokerExecutor(
        client=_InvocationsOnlyClient(),
        profiles_root=tmp_path,
        policy=_policy(),
    )
    execution = executor.finalize(
        BrokerTaskState(
            task_id="task_1",
            status="completed",
            result=dict(REAL_BROKER_RESULT),
            execution_summary={"served_by": {"provider": "lmstudio", "model": "laguna-xs-2.1"}},
        )
    )

    # `REAL_BROKER_RESULT.usage.cost_usd` es 0.0: es la cifra contractual del broker.
    assert execution.audit["total_cost_usd"] == pytest.approx(0.0)
    assert execution.audit["exploratory_cost_usd"] == pytest.approx(0.75)
    assert "Broker invocations: 1" in " ".join(execution.milestones)


def test_yield_reports_the_state_the_card_actually_reached(tmp_path: Path) -> None:
    """D4: agotar `max_attempts` al ceder manda la CARD a blocked, no a pending.

    La API contestaba `pending` y además guardaba esa respuesta en el registro de
    idempotencia, así que el runner y la reproducción del evento quedaban mintiendo.
    """
    application = AgoraApplication(tmp_path)
    application.initialize()
    app = create_api(
        application,
        ApiSettings(token=TOKEN, principal="test-client", require_https=True),
    )
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    create_card(application.board, "task.md", max_attempts=1)
    client = TestClient(
        app, base_url="https://testserver", headers={"Authorization": f"Bearer {TOKEN}"}
    )

    claimed = client.post(
        "/api/v1/cards/task.md/claim",
        headers={"Idempotency-Key": "claim-1"},
        json={"runner_id": "runner-1", "profile": "summarizer"},
    )
    assert claimed.status_code == 200, claimed.text

    yielded = client.post(
        "/api/v1/cards/task.md/yield",
        headers={"Idempotency-Key": "yield-1"},
        json={"runner_id": "runner-1", "reason": "harness real falló", "increment_attempts": True},
    )

    assert yielded.status_code == 200, yielded.text
    assert yielded.json()["state"] == BoardState.BLOCKED.value
    assert (application.board.directory(BoardState.BLOCKED) / "task.md").is_file()
    assert not (application.board.directory(BoardState.PENDING) / "task.md").is_file()

    replay = client.post(
        "/api/v1/cards/task.md/yield",
        headers={"Idempotency-Key": "yield-1"},
        json={"runner_id": "runner-1", "reason": "harness real falló", "increment_attempts": True},
    )
    assert replay.json()["state"] == BoardState.BLOCKED.value
    assert replay.json()["replayed"] is True


class _InvocationsOnlyClient:
    """Sustituto mínimo: `finalize` sólo consulta las invocaciones."""

    def invocations(self, _task_id: str) -> tuple[BrokerInvocation, ...]:
        return (
            BrokerInvocation(
                invocation_id="inv_contract",
                role="single",
                model={"provider": "lmstudio", "deployment": "local", "model": "laguna-xs-2.1"},
                status="completed",
                cost_usd=0.25,
            ),
            BrokerInvocation(
                invocation_id="inv_probe",
                role="shadow_probe",
                model={"provider": "ollama", "deployment": "local", "model": "gemma4:12b"},
                status="completed",
                cost_usd=0.75,
            ),
        )


def _policy() -> BrokerPolicy:
    return BrokerPolicy(determinism="routed", timeout_seconds=600, zombie_timeout_seconds=900)


def _request(policy: BrokerPolicy) -> dict[str, object]:
    root = Path(__file__).resolve().parents[1] / "examples" / "f0-demo"
    profile = Profile.load(root / "AGENTS" / "summarizer" / "PROFILE.md")
    card = Card.load(root / "CARD.md")
    return build_broker_request(profile, (), card, policy=policy, idempotency_key="agora:test")


def test_contract_cost_falls_back_to_contractual_invocations(tmp_path: Path) -> None:
    """D3: sin `result.usage`, sólo se suman las invocaciones de la CARD."""
    executor = BrokerExecutor(
        client=_InvocationsOnlyClient(),
        profiles_root=tmp_path,
        policy=_policy(),
    )
    result = {
        key: value for key, value in REAL_BROKER_RESULT.items() if key != "usage"
    }
    execution = executor.finalize(
        BrokerTaskState(
            task_id="task_1",
            status="completed",
            result=result,
            execution_summary={"served_by": {"provider": "lmstudio"}},
        )
    )

    assert execution.audit["total_cost_usd"] == pytest.approx(0.25)
