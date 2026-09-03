from __future__ import annotations

import ast
import base64
import inspect
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

import agora.remote.client as client_module
import agora.remote.runner as runner_module
from agora.api import ApiSettings, create_api
from agora.api.contracts import WorkItem
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.remote.client import AgoraApiError
from agora.remote.runner import DeterministicRemoteHarness, RemoteRunner
from conftest import create_card, write_profile

TOKEN = "runner-test-token-012345"


class InProcessClient:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def work(self, profiles: tuple[str, ...]) -> tuple[WorkItem, ...]:
        response = self.client.get(
            "/api/v1/work", params=[("profiles", profile) for profile in profiles]
        )
        return tuple(WorkItem.model_validate(item) for item in response.json())

    def claim(self, filename: str, **kwargs: Any) -> dict[str, Any]:
        return self._post(filename, "claim", kwargs)

    def progress(self, filename: str, **kwargs: Any) -> dict[str, Any]:
        return self._post(filename, "progress", kwargs)

    def close_card(self, filename: str, **kwargs: Any) -> dict[str, Any]:
        payload = dict(kwargs)
        payload.pop("idempotency_key")
        return self._request(
            filename,
            "close",
            payload,
            kwargs["idempotency_key"],
        )

    def _post(self, filename: str, action: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        payload = dict(kwargs)
        key = payload.pop("idempotency_key")
        return self._request(filename, action, payload, key)

    def _request(
        self, filename: str, action: str, payload: dict[str, Any], key: str
    ) -> dict[str, Any]:
        response = self.client.post(
            f"/api/v1/cards/{filename}/{action}",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        if response.is_error:
            raise AgoraApiError(response.status_code, response.json().get("detail", response.text))
        body: dict[str, Any] = response.json()
        return body


class SwitchableClient:
    def __init__(self, delegate: InProcessClient) -> None:
        self.delegate = delegate
        self.online = False

    def work(self, profiles: tuple[str, ...]) -> tuple[WorkItem, ...]:
        if not self.online:
            raise httpx.ConnectError("runner is offline")
        return self.delegate.work(profiles)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


def test_offline_runner_leaves_card_pending_then_reconnects_and_completes(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    create_card(application.board)
    app = create_api(application, ApiSettings(token=TOKEN, require_https=True))
    test_client = TestClient(
        app,
        base_url="https://testserver",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    switchable = SwitchableClient(InProcessClient(test_client))
    runner = RemoteRunner(
        switchable,  # type: ignore[arg-type]
        "runner-1",
        ("summarizer",),
        DeterministicRemoteHarness(),
    )

    offline = runner.run_once()
    assert offline.status == "offline"
    assert application.board.paths(BoardState.PENDING)

    switchable.online = True
    completed = runner.run_once()

    assert completed.status == "completed"
    assert application.board.paths(BoardState.DONE)
    artifact = tmp_path / "artifacts" / "remote" / "task" / "task.txt"
    assert artifact.read_text(encoding="utf-8").startswith("Agora deterministic remote")


def test_remote_runtime_has_no_board_or_card_filesystem_dependency() -> None:
    for module in (client_module, runner_module):
        tree = ast.parse(inspect.getsource(module))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(name.startswith(("agora.board", "agora.cards")) for name in imported)


def test_remote_artifact_payload_is_transport_data_not_a_server_path() -> None:
    work = WorkItem(
        filename="card.md",
        function="transform",
        request="summarize",
        priority="normal",
        attempts=0,
        profile="summarizer",
    )
    output = DeterministicRemoteHarness().execute(work)
    encoded = base64.b64encode(output.artifacts["card.txt"])

    assert b"Agora deterministic remote artifact" in base64.b64decode(encoded)
    assert all(not Path(name).is_absolute() for name in output.artifacts)
