"""Loopback-only client for AI_Broker, including System-1 contract 2.11."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from agora.broker.contracts import (
    BrokerArtifact,
    BrokerCapabilities,
    BrokerFileState,
    BrokerInvocation,
    BrokerTaskState,
    System1Judgment,
    validate_capabilities,
)

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class BrokerApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"AI_Broker {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class BrokerTimeout(TimeoutError):
    pass


@dataclass(slots=True)
class BrokerClient:
    base_url: str
    admin_token: str = field(repr=False)
    timeout: float = 30.0
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
            raise ValueError("X-Admin-Token client is restricted to loopback HTTP")
        if len(self.admin_token) < 16:
            raise ValueError("broker credential is unexpectedly short")
        self._client = httpx.Client(
            base_url=self.base_url.rstrip("/"),
            headers={"X-Admin-Token": self.admin_token},
            timeout=self.timeout,
            transport=self.transport,
        )

    def close(self) -> None:
        self._client.close()

    def health(self) -> bool:
        response = self._client.get("/health/live")
        return response.status_code == 200

    def capabilities(self) -> dict[str, Any]:
        return self.contract().raw

    def contract(self) -> BrokerCapabilities:
        """Lo que promete el broker en marcha ahora mismo, validado."""
        return validate_capabilities(self._request("GET", "/api/v1/capabilities"))

    def judge_system1(
        self,
        *,
        use_case: str,
        inputs: dict[str, Any],
        instructions: str,
        timeout_seconds: float = 75.0,
    ) -> System1Judgment:
        """One synchronous binary judgment; no task creation, polling or retries.

        Provider order belongs to the broker. Keep the content local, regardless
        of the generation policy used for the separate reviewer task.
        """
        payload = self._request(
            "POST",
            "/api/v1/system1/judge",
            json={
                "use_case": use_case,
                "input": inputs,
                "decision_type": "binary",
                "instructions": instructions,
                "cloud_allowed": False,
            },
            timeout=timeout_seconds,
        )
        judgment = System1Judgment.model_validate(payload)
        if judgment.use_case != use_case:
            raise ValueError("System-1 response use_case differs from request")
        return judgment

    def upload_file(self, path: Path) -> BrokerFileState:
        with path.open("rb") as stream:
            payload = self._request(
                "POST",
                "/api/v1/files",
                files={"file": (path.name, stream, "application/octet-stream")},
            )
        return BrokerFileState.model_validate(payload)

    def file_state(self, file_id: str) -> BrokerFileState:
        return BrokerFileState.model_validate(self._request("GET", f"/api/v1/files/{file_id}"))

    def wait_file_ready(
        self,
        file_id: str,
        *,
        timeout_seconds: float,
        poll_seconds: float = 1.0,
    ) -> BrokerFileState:
        deadline = time.monotonic() + timeout_seconds
        while True:
            state = self.file_state(file_id)
            if state.status == "ready":
                return state
            if state.status == "failed":
                raise BrokerApiError(409, f"file conversion failed: {state.error}")
            if time.monotonic() >= deadline:
                raise BrokerTimeout(f"file {file_id} was not ready before timeout")
            self.sleep(poll_seconds)

    def submit(self, payload: dict[str, Any]) -> str:
        response = self._request("POST", "/api/v1/tasks", json=payload)
        task_id = response.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise BrokerApiError(500, "task response omitted task_id")
        return task_id

    def task(self, task_id: str) -> BrokerTaskState:
        return BrokerTaskState.model_validate(self._request("GET", f"/api/v1/tasks/{task_id}"))

    def invocations(self, task_id: str) -> tuple[BrokerInvocation, ...]:
        payload = self._request("GET", f"/api/v1/tasks/{task_id}/invocations")
        return tuple(BrokerInvocation.model_validate(item) for item in payload.get("items", []))

    def artifacts(self, task_id: str) -> tuple[BrokerArtifact, ...]:
        """Vía canónica del entregable (Client_API.md, 8.3)."""
        payload = self._request("GET", f"/api/v1/tasks/{task_id}/artifacts")
        return tuple(BrokerArtifact.model_validate(item) for item in payload.get("items", []))

    def download_artifact(self, task_id: str, artifact_id: str) -> bytes:
        """Los bytes exactos que produjo el modelo, sin reescrituras de plataforma."""
        return self._bytes("GET", f"/api/v1/tasks/{task_id}/artifacts/{artifact_id}")

    def cancel(self, task_id: str) -> BrokerTaskState:
        return BrokerTaskState.model_validate(self._request("DELETE", f"/api/v1/tasks/{task_id}"))

    def wait_task(
        self,
        task_id: str,
        *,
        timeout_seconds: float,
        poll_seconds: float = 1.0,
    ) -> BrokerTaskState:
        deadline = time.monotonic() + timeout_seconds
        while True:
            state = self.task(task_id)
            if state.terminal:
                return state
            if time.monotonic() >= deadline:
                raise BrokerTimeout(f"task {task_id} exceeded client wait timeout")
            self.sleep(poll_seconds)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._send(method, path, **kwargs).json()

    def _bytes(self, method: str, path: str, **kwargs: Any) -> bytes:
        return self._send(method, path, **kwargs).content

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, path, **kwargs)
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            safe = str(detail).replace(self.admin_token, "[REDACTED]")
            raise BrokerApiError(response.status_code, safe)
        return response

    def __enter__(self) -> BrokerClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
