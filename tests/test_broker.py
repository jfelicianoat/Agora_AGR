from __future__ import annotations

import hashlib
import inspect
import json
import sys
import time
from pathlib import Path

import httpx
import pytest

from agora.api.contracts import WorkItem
from agora.board import Board
from agora.broker import runner as broker_runner_module
from agora.broker.client import BrokerApiError, BrokerClient
from agora.broker.contracts import BrokerPolicy
from agora.broker.executor import BrokerExecutor, broker_idempotency_key
from agora.broker.request_builder import build_broker_request
from agora.broker.supervisor import BrokerSupervisor
from agora.cards import Card
from agora.profiles import Profile
from agora.remote import client as remote_client_module
from agora.skills import load_profile_skills
from conftest import create_card, write_profile

DELIVERABLE = b"Broker completed the Atomic CARD."


class FakeBroker:
    def __init__(
        self,
        token: str = "broker-test-token-0123456789",
        *,
        contract_version: str = "2.10",
    ) -> None:
        self.token = token
        self.contract_version = contract_version
        self.file_statuses = ["ready"]
        self.task_statuses = ["completed"]
        self.submissions: list[dict[str, object]] = []
        self.billed_keys: set[str] = set()
        self.cancelled: list[str] = []
        self.task_id = "task-broker-1"
        self.served_by = {"provider": "ollama", "deployment": "local", "model": "qwen3"}
        self.invocations = [
            {
                "invocation_id": "inv-1",
                "role": "single",
                "contractual": True,
                "model": self.served_by,
                "status": "completed",
                "tokens_input": 100,
                "tokens_output": 20,
                "cost_usd": 0.0125,
                "generation": {
                    "temperature": 0.3,
                    "max_output_tokens": 4000,
                    "seed_status": "not_requested",
                    "top_p_status": "not_requested",
                },
                "prompt_compression": {"requested": "off", "effective": "off"},
                "execution_fingerprint": {"hash": "fingerprint-1", "components": {}},
                "created_at": "2026-09-01T12:00:00Z",
                "updated_at": "2026-09-01T12:00:01Z",
            }
        ]
        self.artifact_bodies = {"art-final": DELIVERABLE}
        self.artifacts = [
            {
                "artifact_id": "art-final",
                "artifact_type": "single_output",
                "filename": "respuesta.md",
                "media_type": "text/markdown",
                "size_bytes": len(DELIVERABLE),
                "sha256": hashlib.sha256(DELIVERABLE).hexdigest(),
                "created_at": "2026-09-01T12:00:02Z",
                "download_url": f"/api/v1/tasks/{self.task_id}/artifacts/art-final",
                "available": True,
                "final": True,
            }
        ]

    @property
    def capabilities_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "contract_version": self.contract_version,
            "prompt_compression_override": True,
            "invocation_telemetry": True,
            "generation_determinism": True,
            "execution_fingerprint": True,
            "task_artifacts": True,
        }
        if tuple(int(part) for part in self.contract_version.split(".")) >= (2, 10):
            payload.update(
                {
                    "invocation_contract": True,
                    "prompt_compression_echo": True,
                    "canonical_artifacts": True,
                    "auxiliary_invocations": True,
                    "auxiliary_invocations_optout": True,
                }
            )
        return payload

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health/live":
            return httpx.Response(200, json={"status": "ok"})
        if request.headers.get("x-admin-token") != self.token:
            return httpx.Response(403, json={"detail": "ADMIN_AUTH_REQUIRED"})
        if path == "/api/v1/capabilities":
            return httpx.Response(200, json=self.capabilities_payload)
        if path == "/api/v1/files" and request.method == "POST":
            return httpx.Response(
                202,
                json={"file_id": "file-1", "status": "received", "filename": "input.txt"},
            )
        if path == "/api/v1/files/file-1":
            status = self._next(self.file_statuses)
            return httpx.Response(
                200,
                json={"file_id": "file-1", "status": status, "filename": "input.txt"},
            )
        if path == "/api/v1/tasks" and request.method == "POST":
            payload = json.loads(request.content)
            self.submissions.append(payload)
            self.billed_keys.add(payload["idempotency_key"])
            return httpx.Response(202, json={"task_id": self.task_id, "status": "queued"})
        if path == f"/api/v1/tasks/{self.task_id}" and request.method == "GET":
            status = self._next(self.task_statuses)
            # Claves reales del contrato 2.9 (app/coordinator.py del broker).
            result = (
                {
                    "result_markdown": "Broker completed the Atomic CARD.",
                    "assistant_content": "Broker completed the Atomic CARD.",
                    "inference_kind": "chat",
                    "output_format": "markdown",
                }
                if status == "completed"
                else None
            )
            return httpx.Response(
                200,
                json={
                    "task_id": self.task_id,
                    "status": status,
                    "result": result,
                    "execution_summary": {
                        "served_by": self.served_by,
                        "models_used": [self.served_by],
                        "fallback_used": False,
                    },
                },
            )
        if path == f"/api/v1/tasks/{self.task_id}/invocations":
            return httpx.Response(200, json={"task_id": self.task_id, "items": self.invocations})
        if path == f"/api/v1/tasks/{self.task_id}/artifacts":
            return httpx.Response(200, json={"task_id": self.task_id, "items": self.artifacts})
        if path.startswith(f"/api/v1/tasks/{self.task_id}/artifacts/"):
            body = self.artifact_bodies.get(path.rsplit("/", 1)[-1])
            if body is None:
                return httpx.Response(404, json={"detail": "artifact not found"})
            return httpx.Response(200, content=body)
        if path == f"/api/v1/tasks/{self.task_id}" and request.method == "DELETE":
            self.cancelled.append(self.task_id)
            self.task_statuses = ["cancelled"]
            return httpx.Response(200, json={"task_id": self.task_id, "status": "cancelled"})
        return httpx.Response(404, json={"detail": "not found"})

    @staticmethod
    def _next(values: list[str]) -> str:
        return values.pop(0) if len(values) > 1 else values[0]


def _broker_client(fake: FakeBroker) -> BrokerClient:
    return BrokerClient(
        "http://127.0.0.1:8765",
        fake.token,
        transport=httpx.MockTransport(fake.handle),
        sleep=lambda _seconds: None,
    )


def test_broker_client_is_loopback_only_and_redacts_echoed_token() -> None:
    token = "secret-broker-token-0123456789"
    with pytest.raises(ValueError, match="loopback"):
        BrokerClient("http://192.168.1.52:8765", token)

    def echo(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": f"bad {request.headers['x-admin-token']}"})

    client = BrokerClient(
        "http://localhost:8765",
        token,
        transport=httpx.MockTransport(echo),
    )
    with pytest.raises(BrokerApiError) as caught:
        client.task("missing")

    assert token not in repr(client)
    assert token not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)


def test_broker_admin_token_cannot_enter_agora_network_client() -> None:
    remote_source = inspect.getsource(remote_client_module)
    runner_source = inspect.getsource(broker_runner_module)

    assert "admin_token" not in remote_source
    assert "X-Admin-Token" not in remote_source
    assert "admin_token" not in runner_source


def test_old_broker_token_stops_working_after_rotation() -> None:
    fake = FakeBroker("first-broker-token-0123456789")
    old = _broker_client(fake)
    assert old.health()
    fake.token = "second-broker-token-012345678"
    new = _broker_client(fake)

    with pytest.raises(BrokerApiError) as caught:
        old.task(fake.task_id)

    assert caught.value.status_code == 403
    assert new.task(fake.task_id).task_id == fake.task_id


def test_supervisor_rotates_token_and_redacts_child_output(tmp_path: Path) -> None:
    script = tmp_path / "print_token.py"
    script.write_text(
        "import os, time\nprint(os.environ['AI_BROKER_ADMIN_TOKEN'], flush=True)\ntime.sleep(60)\n",
        encoding="utf-8",
    )
    tokens = iter(("first-supervisor-token-012345", "second-supervisor-token-12345"))
    supervisor = BrokerSupervisor(
        [sys.executable, str(script)],
        tmp_path,
        token_factory=lambda: next(tokens),
    )
    first = supervisor.start()
    _wait_for_log(supervisor)
    first_token = first.admin_token
    assert supervisor.logs == ("[REDACTED]",)

    second = supervisor.restart()
    _wait_for_log(supervisor)
    try:
        assert first_token != second.admin_token
        assert all(
            first_token not in line and second.admin_token not in line for line in supervisor.logs
        )
        assert supervisor.logs == ("[REDACTED]",)
    finally:
        first.close()
        second.close()
        supervisor.stop()


def test_profile_precedes_skills_and_card_and_compression_is_off(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    card_path = create_card(board)
    profile_path = write_profile(
        tmp_path / "AGENTS",
        "summarizer",
        handles=["summarize"],
        skills=["summarize-text"],
    )
    profile = Profile.load(profile_path)
    skills = load_profile_skills(profile_path.parent, profile.skills)
    card = Card.load(card_path)
    strict = BrokerPolicy(
        determinism="strict",
        target_model={"provider": "ollama", "deployment": "local", "model": "qwen3"},
    )

    payload = build_broker_request(
        profile,
        skills,
        card,
        policy=strict,
        idempotency_key="agora:test",
    )
    prompt = payload["content"]["prompt"]

    assert prompt.index("# Atomic PROFILE") < prompt.index("# Declared SKILLS")
    assert prompt.index("# Declared SKILLS") < prompt.index("# CARD")
    assert payload["prompt_compression"] == "off"
    assert payload["generation"] == {
        "temperature": 0.0,
        "max_output_tokens": 4000,
        "seed": 0,
        "top_p": 1.0,
    }
    assert payload["model_requirements"]["fallback_allowed"] is False
    assert payload["model_requirements"]["target_model"]["model"] == "qwen3"


def test_broker_idempotency_key_avoids_double_billing_for_same_card_attempt(tmp_path: Path) -> None:
    fake = FakeBroker()
    client = _broker_client(fake)
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    card = Card.create(function="transform", request="summarize", origin="agora")
    work = _work(card)
    executor = BrokerExecutor(client, tmp_path / "AGENTS", BrokerPolicy())
    checkpoints: list[tuple[str, str]] = []

    executor.execute(work, (), checkpoint=lambda task, key: checkpoints.append((task, key)))
    executor.execute(work, (), checkpoint=lambda task, key: checkpoints.append((task, key)))

    assert len(fake.submissions) == 2
    assert len(fake.billed_keys) == 1
    assert checkpoints[0][1] == checkpoints[1][1] == broker_idempotency_key(work)


def _work(card: Card) -> WorkItem:
    from agora.api.contracts import WorkItem

    return WorkItem(
        filename="task.md",
        function=card.function,
        request=card.request,
        priority="normal",
        attempts=card.attempts,
        profile="summarizer",
        card_document=card.serialize(),
    )


def _wait_for_log(supervisor: BrokerSupervisor) -> None:
    deadline = time.monotonic() + 3
    while not supervisor.logs and time.monotonic() < deadline:
        time.sleep(0.01)
    assert supervisor.logs
