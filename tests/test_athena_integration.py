from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from agora.api import ApiSettings, create_api
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.cards import Card
from agora.dispatcher import Dispatcher, DispatchStatus
from agora.integrations.athena import (
    AthenaAgoraAdapter,
    AthenaClient,
    AthenaHarness,
)
from agora.profiles import Profile
from conftest import create_card, write_document, write_profile

TOKEN = "athena-service-token"


class FakeAthena:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.available = True
        self.payloads: list[dict[str, Any]] = []
        self.status = "completed"
        self.verification = {"status": "passed", "summary": "checks passed"}
        self.cancelled = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/health":
            return httpx.Response(200 if self.available else 503, json={"status": "ok"})
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"error": {"message": "Bad token"}})
        if request.url.path == "/v1/auth/check":
            return httpx.Response(200, json={"authenticated": True})
        if request.url.path == "/v1/runs" and request.method == "POST":
            self.payloads.append(json.loads(request.content))
            return httpx.Response(201, json={"run_id": "athena-run-1"})
        if request.url.path == "/v1/runs/athena-run-1" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "run_id": "athena-run-1",
                    "status": self.status,
                    "verification": self.verification,
                    "working_memory": {"files_modified": ["result.txt"]},
                },
            )
        if request.url.path.endswith("/cancel"):
            self.cancelled = True
            return httpx.Response(202, json={"cancelling": True})
        return httpx.Response(404, json={"error": {"message": "not found"}})


def _athena_profile(tmp_path: Path, *, writes: str = "allow", execution: str = "off") -> Path:
    path = write_profile(
        tmp_path / "AGENTS",
        "athena-worker",
        handles=["summarize"],
        skills=["summarize-text"],
        harness="athena",
    )
    profile = Profile.load(path)
    metadata = dict(profile.metadata)
    metadata["athena"] = {
        "workspace": str(tmp_path),
        "writes": writes,
        "exec": execution,
        "deliverables": ["result.txt"],
        "timeout_seconds": 2,
    }
    return write_document(path, metadata, profile.body)


def _athena_client(fake: FakeAthena) -> AthenaClient:
    return AthenaClient(
        "http://127.0.0.1:8420",
        TOKEN,
        transport=httpx.MockTransport(fake.handle),
        sleep=lambda _seconds: None,
    )


def test_athena_client_is_loopback_only_and_checks_authenticated_availability(
    tmp_path: Path,
) -> None:
    fake = FakeAthena(tmp_path)
    client = _athena_client(fake)
    assert client.available()
    fake.available = False
    assert not client.available()

    try:
        AthenaClient("http://192.168.1.12:8420", TOKEN)
    except ValueError as exc:
        assert "loopback" in str(exc)
    else:
        raise AssertionError("Athena LAN binding was accepted")


def test_unavailable_athena_runner_leaves_card_pending_without_attempt(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    _athena_profile(tmp_path)
    fake = FakeAthena(tmp_path)
    fake.available = False
    create_card(application.board)

    outcome = Dispatcher(
        application.board,
        tmp_path / "AGENTS",
        AthenaHarness(_athena_client(fake)),
    ).run_once()[0]

    pending = Card.load(application.board.directory(BoardState.PENDING) / "task.md")
    assert outcome.status is DispatchStatus.SKIPPED
    assert pending.attempts == 0


def test_athena_card_mode_uses_profile_permissions_and_closes_only_verified_work(
    tmp_path: Path,
) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    _athena_profile(tmp_path, writes="allow", execution="off")
    (tmp_path / "result.txt").write_text("verified result", encoding="utf-8")
    fake = FakeAthena(tmp_path)
    path = create_card(application.board)
    requested = Card.load(path)
    requested.metadata["athena"] = {"writes": "allow", "exec": "allow"}
    requested.save()

    outcome = Dispatcher(
        application.board,
        tmp_path / "AGENTS",
        AthenaHarness(_athena_client(fake)),
    ).run_once()[0]

    done = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    submitted = fake.payloads[0]
    assert outcome.status is DispatchStatus.DISPATCHED
    assert submitted["writes"] == "allow"
    assert submitted["exec"] == "off"
    assert "Do not ask questions" in submitted["objective"]
    assert submitted["objective"].index("# PROFILE") < submitted["objective"].index("# CARD")
    assert done.metadata["model"] == "athena"
    assert done.metadata["paths"] == [str((tmp_path / "result.txt").resolve())]


def test_ask_is_rejected_in_card_mode_without_starting_athena(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    _athena_profile(tmp_path, writes="ask")
    fake = FakeAthena(tmp_path)
    create_card(application.board)

    outcome = Dispatcher(
        application.board,
        tmp_path / "AGENTS",
        AthenaHarness(_athena_client(fake)),
    ).run_once()[0]

    pending = Card.load(application.board.directory(BoardState.PENDING) / "task.md")
    assert outcome.status is DispatchStatus.ERROR
    assert "ask is forbidden" in outcome.reason
    assert pending.attempts == 1
    assert fake.payloads == []


def test_inconclusive_athena_verification_never_creates_false_done(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    _athena_profile(tmp_path)
    (tmp_path / "result.txt").write_text("unverified", encoding="utf-8")
    fake = FakeAthena(tmp_path)
    fake.verification = {"status": "inconclusive"}
    create_card(application.board)

    outcome = Dispatcher(
        application.board,
        tmp_path / "AGENTS",
        AthenaHarness(_athena_client(fake)),
    ).run_once()[0]

    pending = Card.load(application.board.directory(BoardState.PENDING) / "task.md")
    assert outcome.status is DispatchStatus.ERROR
    assert pending.attempts == 1
    assert not application.board.paths(BoardState.DONE)


class InProcessAgoraFacade:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def create_card(self, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        response = self.client.post(
            "/api/v1/cards", json=payload, headers={"Idempotency-Key": idempotency_key}
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    def card_status(self, filename: str) -> dict[str, Any]:
        response = self.client.get(f"/api/v1/cards/{filename}")
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    def cancel_card(self, filename: str, *, reason: str, idempotency_key: str) -> dict[str, Any]:
        response = self.client.post(
            f"/api/v1/cards/{filename}/cancel",
            json={"reason": reason},
            headers={"Idempotency-Key": idempotency_key},
        )
        response.raise_for_status()
        body: dict[str, Any] = response.json()
        return body

    def card_artifact(self, filename: str, index: int) -> bytes:
        response = self.client.get(f"/api/v1/cards/{filename}/artifacts/{index}")
        response.raise_for_status()
        raw: bytes = response.content
        return raw


def test_athena_can_delegate_disconnect_recover_artifact_and_cancel(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    app = create_api(
        application,
        ApiSettings(
            token="agora-athena-token-012345",
            principal="athena",
            require_https=True,
        ),
    )
    client = TestClient(
        app,
        base_url="https://testserver",
        headers={"Authorization": "Bearer agora-athena-token-012345"},
    )
    adapter = AthenaAgoraAdapter(InProcessAgoraFacade(client))  # type: ignore[arg-type]

    submitted = adapter.submit(
        filename="delegated.md",
        function="transform",
        request="summarize document",
        idempotency_key="athena-submit-1",
    )
    assert submitted["state"] == "pending"
    persisted = Card.load(application.board.directory(BoardState.PENDING) / "delegated.md")
    assert persisted.metadata["origin"] == "athena"

    claimed = application.board.claim("delegated.md", "worker")
    artifact = tmp_path / "artifacts" / "delegated.txt"
    artifact.parent.mkdir()
    artifact.write_text("delegated result", encoding="utf-8")
    application.board.close(claimed, actor="worker", paths=[artifact])

    assert adapter.status("delegated.md")["state"] == "done"
    assert adapter.artifacts("delegated.md") == (b"delegated result",)

    adapter.submit(
        filename="cancelled.md",
        function="transform",
        request="cancel me",
        idempotency_key="athena-submit-2",
    )
    cancelled = adapter.cancel(
        "cancelled.md",
        reason="Athena no longer needs the delegation",
        idempotency_key="athena-cancel-2",
    )
    assert cancelled["state"] == "archive"
    archived = Card.load(application.board.directory(BoardState.ARCHIVE) / "cancelled.md")
    assert "CARD cancelled" in archived.body
