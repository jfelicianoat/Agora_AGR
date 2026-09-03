from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agora.api import ApiSettings, create_api
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.cards import Card
from conftest import create_card, write_profile

TOKEN = "f2-test-token-0123456789"


def _api(tmp_path: Path) -> tuple[AgoraApplication, FastAPI]:
    application = AgoraApplication(tmp_path)
    application.initialize()
    app = create_api(
        application,
        ApiSettings(token=TOKEN, principal="test-client", require_https=True),
    )
    return application, app


def _client(app: FastAPI, *, authorized: bool = True) -> TestClient:
    headers = {"Authorization": f"Bearer {TOKEN}"} if authorized else {}
    return TestClient(app, base_url="https://testserver", headers=headers)


def test_remote_work_returns_only_cards_compatible_with_runner_profiles(tmp_path: Path) -> None:
    application, app = _api(tmp_path)
    write_profile(
        tmp_path / "AGENTS",
        "summarizer",
        function="transform",
        handles=["summarize"],
    )
    write_profile(
        tmp_path / "AGENTS",
        "translator",
        function="transform",
        handles=["translate"],
    )
    create_card(application.board, "summary.md", request="summarize report")
    create_card(application.board, "translation.md", request="translate report")

    response = _client(app).get("/api/v1/work", params=[("profiles", "summarizer")])

    assert response.status_code == 200
    assert [item["filename"] for item in response.json()] == ["summary.md"]
    assert response.json()[0]["profile"] == "summarizer"


def test_two_remote_claims_have_exactly_one_winner(tmp_path: Path) -> None:
    application, app = _api(tmp_path)
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    create_card(application.board)
    barrier = Barrier(2)

    def claim(runner: str) -> int:
        barrier.wait()
        with _client(app) as client:
            response = client.post(
                "/api/v1/cards/task.md/claim",
                headers={"Idempotency-Key": f"claim-{runner}"},
                json={"runner_id": runner, "profile": "summarizer"},
            )
            status: int = response.status_code
            return status

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(claim, ("runner-a", "runner-b")))

    assert sorted(statuses) == [200, 409]
    assert not application.board.paths(BoardState.PENDING)
    assert len(application.board.paths(BoardState.IN_PROGRESS)) == 1


def test_lost_claim_response_can_be_replayed_without_second_execution(tmp_path: Path) -> None:
    application, app = _api(tmp_path)
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    create_card(application.board)
    client = _client(app)
    headers = {"Idempotency-Key": "stable-claim-key"}
    body = {"runner_id": "runner-a", "profile": "summarizer"}

    first = client.post("/api/v1/cards/task.md/claim", headers=headers, json=body)
    replay = client.post("/api/v1/cards/task.md/claim", headers=headers, json=body)

    assert first.status_code == replay.status_code == 200
    assert first.json()["replayed"] is False
    assert replay.json()["replayed"] is True
    assert len(application.board.paths(BoardState.IN_PROGRESS)) == 1
    record = Card.load(application.board.paths(BoardState.IN_PROGRESS)[0]).body
    assert record.count("Claimed CARD atomically") == 1


def test_create_idempotency_does_not_duplicate_card(tmp_path: Path) -> None:
    application, app = _api(tmp_path)
    client = _client(app)
    headers = {"Idempotency-Key": "create-stable"}
    body = {
        "filename": "remote.md",
        "function": "transform",
        "request": "summarize remote input",
    }

    first = client.post("/api/v1/cards", headers=headers, json=body)
    replay = client.post("/api/v1/cards", headers=headers, json=body)

    assert first.status_code == replay.status_code == 201
    assert replay.json()["replayed"] is True
    assert [path.name for path in application.board.paths(BoardState.PENDING)] == ["remote.md"]
    card = Card.load(application.board.paths(BoardState.PENDING)[0])
    assert card.metadata["origin"] == "test-client"
    assert card.metadata["origin_identity"] == "test-client"


def test_idempotency_survives_api_restart_and_rejects_key_reuse(tmp_path: Path) -> None:
    application, app = _api(tmp_path)
    headers = {"Idempotency-Key": "durable-create"}
    body = {
        "filename": "durable.md",
        "function": "transform",
        "request": "summarize durable state",
    }
    assert _client(app).post("/api/v1/cards", headers=headers, json=body).status_code == 201

    restarted_app = create_api(
        AgoraApplication(tmp_path),
        ApiSettings(token=TOKEN, principal="test-client", require_https=True),
    )
    replay = _client(restarted_app).post("/api/v1/cards", headers=headers, json=body)
    conflict = _client(restarted_app).post(
        "/api/v1/cards",
        headers=headers,
        json={**body, "filename": "different.md"},
    )

    assert replay.status_code == 201
    assert replay.json()["replayed"] is True
    assert conflict.status_code == 409
    assert [path.name for path in application.board.paths(BoardState.PENDING)] == ["durable.md"]


def test_spoofed_origin_is_rejected_and_real_origin_comes_from_authentication(
    tmp_path: Path,
) -> None:
    application, app = _api(tmp_path)
    response = _client(app).post(
        "/api/v1/cards",
        headers={"Idempotency-Key": "origin-spoof"},
        json={
            "filename": "spoof.md",
            "function": "transform",
            "request": "summarize",
            "origin": "human",
        },
    )

    assert response.status_code == 422
    assert not application.board.paths(BoardState.PENDING)


def test_path_traversal_and_artifact_escape_are_rejected(tmp_path: Path) -> None:
    application, app = _api(tmp_path)
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    create_card(application.board)
    client = _client(app)
    invalid_create = client.post(
        "/api/v1/cards",
        headers={"Idempotency-Key": "bad-path"},
        json={
            "filename": "../escape.md",
            "function": "transform",
            "request": "summarize",
        },
    )
    claim = client.post(
        "/api/v1/cards/task.md/claim",
        headers={"Idempotency-Key": "safe-claim"},
        json={"runner_id": "runner", "profile": "summarizer"},
    )
    escaped_artifact = client.post(
        "/api/v1/cards/task.md/close",
        headers={"Idempotency-Key": "bad-artifact"},
        json={
            "runner_id": "runner",
            "artifacts": [
                {
                    "name": "../escape.txt",
                    "content_base64": base64.b64encode(b"bad").decode("ascii"),
                }
            ],
        },
    )

    assert invalid_create.status_code == 400
    assert claim.status_code == 200
    assert escaped_artifact.status_code == 400
    assert not (tmp_path / "artifacts" / "remote" / "escape.txt").exists()
    assert application.board.paths(BoardState.IN_PROGRESS)


def test_only_authenticated_endpoints_can_write_board(tmp_path: Path) -> None:
    application, app = _api(tmp_path)
    response = _client(app, authorized=False).post(
        "/api/v1/cards",
        headers={"Idempotency-Key": "unauthorized"},
        json={"filename": "no.md", "function": "transform", "request": "summarize"},
    )

    assert response.status_code == 401
    assert not application.board.paths(BoardState.PENDING)


def test_api_rejects_plain_http_even_with_valid_credentials(tmp_path: Path) -> None:
    _application, app = _api(tmp_path)
    client = TestClient(
        app,
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )

    response = client.get("/api/v1/health")

    assert response.status_code == 400
    assert response.json()["detail"] == "HTTPS is required"


def test_progress_close_and_events_are_durable_and_human_readable(tmp_path: Path) -> None:
    application, app = _api(tmp_path)
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    create_card(application.board)
    client = _client(app)
    assert (
        client.post(
            "/api/v1/cards/task.md/claim",
            headers={"Idempotency-Key": "claim"},
            json={"runner_id": "runner", "profile": "summarizer"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/cards/task.md/progress",
            headers={"Idempotency-Key": "progress"},
            json={"runner_id": "runner", "milestones": ["Remote work is half complete."]},
        ).status_code
        == 200
    )
    closed = client.post(
        "/api/v1/cards/task.md/close",
        headers={"Idempotency-Key": "close"},
        json={
            "runner_id": "runner",
            "artifacts": [
                {
                    "name": "result.txt",
                    "content_base64": base64.b64encode(b"verified remote result").decode("ascii"),
                }
            ],
            "model": "none",
        },
    )
    events = client.get("/api/v1/events").json()["events"]

    assert closed.status_code == 200
    done = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    assert "Remote work is half complete." in done.body
    assert "CARD closed." in done.body
    assert Path(done.metadata["paths"][0]).read_bytes() == b"verified remote result"
    assert [event["kind"] for event in events] == [
        "card.claimed",
        "card.progressed",
        "card.closed",
    ]


def test_yield_and_admin_unblock_use_domain_transitions(tmp_path: Path) -> None:
    application, app = _api(tmp_path)
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    create_card(application.board, "yielded.md")
    client = _client(app)
    assert (
        client.post(
            "/api/v1/cards/yielded.md/claim",
            headers={"Idempotency-Key": "claim-yield"},
            json={"runner_id": "runner", "profile": "summarizer"},
        ).status_code
        == 200
    )
    yielded = client.post(
        "/api/v1/cards/yielded.md/yield",
        headers={"Idempotency-Key": "yield"},
        json={"runner_id": "runner", "reason": "capacity changed"},
    )
    pending = Card.load(application.board.directory(BoardState.PENDING) / "yielded.md")
    assert pending.source is not None
    application.board.block_pending(
        pending.source,
        actor="test",
        reason="manual review",
    )
    unblocked = client.post(
        "/api/v1/admin/blocked/yielded.md/unblock",
        headers={"Idempotency-Key": "unblock"},
        json={"reason": "review completed"},
    )
    restored = Card.load(application.board.directory(BoardState.PENDING) / "yielded.md")

    assert yielded.status_code == 200
    assert pending.attempts == 0
    assert unblocked.status_code == 200
    assert not restored.blocked
    assert "CARD unblocked: review completed" in restored.body
