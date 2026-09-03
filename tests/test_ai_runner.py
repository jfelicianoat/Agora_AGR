from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn

import httpx
from fastapi.testclient import TestClient

from agora.api import ApiSettings, create_api
from agora.api.contracts import InputResource, WorkItem
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.broker.client import BrokerClient, BrokerTimeout
from agora.broker.contracts import BrokerPolicy
from agora.broker.executor import BrokerExecutor
from agora.broker.runner import AiRunner
from agora.cards import Card
from agora.remote.client import AgoraApiError
from conftest import create_card, write_profile
from test_broker import FakeBroker, _broker_client

TOKEN = "ai-runner-agora-token-012345"


class InProcessAgoraClient:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def work(self, profiles: tuple[str, ...]) -> tuple[WorkItem, ...]:
        return self._items("/api/v1/work", [("profiles", profile) for profile in profiles])

    def claimed(self, runner_id: str) -> tuple[WorkItem, ...]:
        return self._items("/api/v1/claims", {"runner_id": runner_id})

    def download_input(self, resource: InputResource) -> bytes:
        response = self.client.get(resource.download_url)
        if response.is_error:
            raise AgoraApiError(response.status_code, response.text)
        raw: bytes = response.content
        return raw

    def claim(self, filename: str, **kwargs: Any) -> dict[str, Any]:
        return self._post(filename, "claim", kwargs)

    def progress(self, filename: str, **kwargs: Any) -> dict[str, Any]:
        return self._post(filename, "progress", kwargs)

    def close_card(self, filename: str, **kwargs: Any) -> dict[str, Any]:
        return self._post(filename, "close", kwargs)

    def yield_card(self, filename: str, **kwargs: Any) -> dict[str, Any]:
        return self._post(filename, "yield", kwargs)

    def _items(self, path: str, params: Any) -> tuple[WorkItem, ...]:
        response = self.client.get(path, params=params)
        if response.is_error:
            raise AgoraApiError(response.status_code, response.text)
        return tuple(WorkItem.model_validate(item) for item in response.json())

    def _post(self, filename: str, action: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        payload = dict(kwargs)
        key = payload.pop("idempotency_key")
        response = self.client.post(
            f"/api/v1/cards/{filename}/{action}",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        if response.is_error:
            raise AgoraApiError(response.status_code, response.json().get("detail", response.text))
        body: dict[str, Any] = response.json()
        return body


def _runner(
    tmp_path: Path,
    fake: FakeBroker,
    *,
    policy: BrokerPolicy | None = None,
    attachment_wait: float = 5,
) -> tuple[AgoraApplication, AiRunner]:
    application = AgoraApplication(tmp_path)
    application.initialize()
    write_profile(
        tmp_path / "AGENTS",
        "summarizer",
        handles=["summarize"],
        skills=["summarize-text"],
        harness="broker",
    )
    app = create_api(application, ApiSettings(token=TOKEN, require_https=True))
    test_client = TestClient(
        app,
        base_url="https://testserver",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    agora = InProcessAgoraClient(test_client)
    broker = _broker_client(fake)
    selected_policy = policy or BrokerPolicy(timeout_seconds=2, zombie_timeout_seconds=3)
    executor = BrokerExecutor(broker, tmp_path / "AGENTS", selected_policy)
    return application, AiRunner(
        agora,  # type: ignore[arg-type]
        broker,
        executor,
        "ai-runner",
        ("summarizer",),
        attachment_wait,
    )


def test_broker_offline_leaves_card_pending_without_attempt(tmp_path: Path) -> None:
    fake = FakeBroker()

    def offline(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("AI PC unavailable")

    application, runner = _runner(tmp_path, fake)
    runner.broker = BrokerClient(
        "http://127.0.0.1:8765",
        fake.token,
        transport=httpx.MockTransport(offline),
    )
    create_card(application.board)

    outcome = runner.run_once()

    pending = Card.load(application.board.directory(BoardState.PENDING) / "task.md")
    assert outcome.status == "broker_unavailable"
    assert pending.attempts == 0


def test_broker_auth_rotation_is_recovered_locally_before_claim(tmp_path: Path) -> None:
    fake = FakeBroker("new-broker-token-0123456789")
    application, runner = _runner(tmp_path, fake)
    old = BrokerClient(
        "http://127.0.0.1:8765",
        "old-broker-token-0123456789",
        transport=httpx.MockTransport(fake.handle),
    )
    runner.broker.close()
    runner.broker = old
    runner.executor.client = old
    renewals: list[str] = []

    def renew() -> BrokerClient:
        renewals.append("local")
        return _broker_client(fake)

    runner.renew_broker = renew
    create_card(application.board)

    outcome = runner.run_once()

    assert outcome.status == "completed"
    assert renewals == ["local"]
    assert application.board.paths(BoardState.DONE)


def test_attachment_converting_waits_before_claim_and_does_not_consume_attempt(
    tmp_path: Path,
) -> None:
    fake = FakeBroker()
    fake.file_statuses = ["converting"]
    application, runner = _runner(tmp_path, fake, attachment_wait=0)
    input_path = tmp_path / "inputs" / "source.txt"
    input_path.parent.mkdir()
    input_path.write_text("source", encoding="utf-8")
    create_card(application.board, inputs={"source": "inputs/source.txt"})

    outcome = runner.run_once()

    pending = Card.load(application.board.directory(BoardState.PENDING) / "task.md")
    assert outcome.status == "waiting_attachment"
    assert pending.attempts == 0
    assert not application.board.paths(BoardState.IN_PROGRESS)


def test_ready_attachment_is_uploaded_before_claim_and_sent_to_broker(tmp_path: Path) -> None:
    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake)
    input_path = tmp_path / "inputs" / "source.txt"
    input_path.parent.mkdir()
    input_path.write_text("source", encoding="utf-8")
    create_card(application.board, inputs={"source": "inputs/source.txt"})

    outcome = runner.run_once()

    assert outcome.status == "completed"
    done = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    assert done.attempts == 0
    content = fake.submissions[0]["content"]
    assert isinstance(content, dict)
    attachments = content["attachments"]
    assert attachments == [
        {
            "type": "broker_file",
            "name": "input.txt",
            "uri": "broker://files/file-1",
            "metadata": {"file_id": "file-1"},
        }
    ]


def test_end_to_end_routed_records_real_model_cost_invocations_and_artifact(
    tmp_path: Path,
) -> None:
    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)

    outcome = runner.run_once()

    assert outcome.status == "completed"
    done = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    artifact = Path(done.metadata["paths"][0])
    execution = done.metadata["execution"]
    assert artifact.read_text(encoding="utf-8") == "Broker completed the Atomic CARD."
    assert done.metadata["model"] == "ollama/local/qwen3"
    assert execution["served_by"] == fake.served_by
    assert execution["total_cost_usd"] == 0.0125
    assert execution["invocations"][0]["invocation_id"] == "inv-1"
    assert execution["invocations"][0]["generation"]["temperature"] == 0.3
    assert execution["invocations"][0]["execution_fingerprint"]["hash"] == "fingerprint-1"
    assert fake.submissions[0]["prompt_compression"] == "off"
    assert "Effective model: ollama/local/qwen3" in done.body
    assert "cost USD: 0.01250000" in done.body
    assert done.attempts == 0


def test_strict_execution_records_verified_effective_parameters(tmp_path: Path) -> None:
    fake = FakeBroker()
    fake.invocations[0]["generation"] = {
        "temperature": 0.0,
        "seed": 0,
        "seed_status": "sent",
        "top_p": 1.0,
        "top_p_status": "sent",
    }
    strict = BrokerPolicy(
        determinism="strict",
        target_model=fake.served_by,
        timeout_seconds=2,
        zombie_timeout_seconds=3,
    )
    application, runner = _runner(tmp_path, fake, policy=strict)
    create_card(application.board)

    outcome = runner.run_once()

    done = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    assert outcome.status == "completed"
    assert done.metadata["execution"]["policy"] == "strict"
    assert done.metadata["execution"]["invocations"][0]["generation"]["seed_status"] == "sent"


def test_strict_mismatch_is_visible_and_consumes_only_agora_failure_attempt(
    tmp_path: Path,
) -> None:
    fake = FakeBroker()
    strict = BrokerPolicy(
        determinism="strict",
        target_model={"provider": "azure", "deployment": "fixed", "model": "gpt-fixed"},
        timeout_seconds=2,
        zombie_timeout_seconds=3,
    )
    application, runner = _runner(tmp_path, fake, policy=strict)
    create_card(application.board)

    outcome = runner.run_once()

    pending = Card.load(application.board.directory(BoardState.PENDING) / "task.md")
    assert outcome.status == "failed"
    assert "target_model" in outcome.detail
    assert pending.attempts == 1
    assert "target_model" in pending.body


def test_broker_internal_retries_do_not_increment_card_attempts(tmp_path: Path) -> None:
    fake = FakeBroker()
    fake.task_statuses = ["routing", "generating", "completed"]
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)

    outcome = runner.run_once()

    done = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    assert outcome.status == "completed"
    assert done.attempts == 0


def test_legitimate_running_task_is_not_cancelled_before_zombie_threshold(tmp_path: Path) -> None:
    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake)
    old = datetime(2026, 9, 1, 12, tzinfo=UTC)
    create_card(application.board)
    claimed = application.board.claim("task.md", "ai-runner", when=old)
    card = Card.load(claimed)
    card.metadata["profile"] = "summarizer"
    card.metadata["remote"] = {
        "system": "ai_broker",
        "task_id": fake.task_id,
        "idempotency_key": "broker-key",
    }
    card.save()
    work = runner.agora.claimed("ai-runner")[0]
    runner.executor = TimeoutExecutor(runner.executor.policy)  # type: ignore[assignment]

    outcome = runner._recover(work, now=old + timedelta(seconds=2))

    assert outcome.status == "broker_running"
    assert fake.cancelled == []
    assert application.board.paths(BoardState.IN_PROGRESS)


def test_real_zombie_is_cancelled_before_card_returns_to_pending(tmp_path: Path) -> None:
    fake = FakeBroker()
    fake.task_statuses = ["generating"]
    application, runner = _runner(tmp_path, fake)
    old = datetime(2026, 9, 1, 12, tzinfo=UTC)
    create_card(application.board)
    claimed = application.board.claim("task.md", "ai-runner", when=old)
    card = Card.load(claimed)
    card.metadata["profile"] = "summarizer"
    card.metadata["remote"] = {
        "system": "ai_broker",
        "task_id": fake.task_id,
        "idempotency_key": "broker-key",
    }
    card.save()
    work = runner.agora.claimed("ai-runner")[0]

    outcome = runner._recover(work, now=old + timedelta(seconds=4))

    pending = Card.load(application.board.directory(BoardState.PENDING) / "task.md")
    assert outcome.detail == "zombie cancelled"
    assert fake.cancelled == [fake.task_id]
    assert pending.attempts == 1
    assert "Cancelled stale AI_Broker task" in pending.body


class TimeoutExecutor:
    def __init__(self, policy: BrokerPolicy) -> None:
        self.policy = policy

    def policy_for_profile_name(self, _name: str) -> BrokerPolicy:
        return self.policy

    def resume(self, _work: WorkItem, _task_id: str) -> NoReturn:
        raise BrokerTimeout("still running")
