"""Broker-compatible client for the OAuth-protected Agora Gateway."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from agora.broker.client import BrokerApiError, BrokerTimeout
from agora.broker.contracts import (
    BrokerArtifact,
    BrokerFileState,
    BrokerInvocation,
    BrokerTaskState,
    validate_capabilities,
)


@dataclass(slots=True)
class GatewayClient:
    base_url: str
    access_token: Callable[[], str] = field(repr=False)
    timeout: float = 30.0
    transport: httpx.BaseTransport | None = None
    sleep: Callable[[float], None] = time.sleep
    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https":
            raise ValueError("Agora Gateway requires HTTPS")
        self._client = httpx.Client(
            base_url=self.base_url.rstrip("/"), timeout=self.timeout, transport=self.transport
        )

    def close(self) -> None:
        self._client.close()

    def health(self) -> bool:
        payload: dict[str, Any] = self._request("GET", "/api/v1/health")
        return payload.get("status") == "ok"

    def capabilities(self) -> dict[str, Any]:
        payload: dict[str, Any] = self._request("GET", "/api/v1/capabilities")
        validate_capabilities(payload)
        return payload

    def contract(self) -> Any:
        return validate_capabilities(self._request("GET", "/api/v1/capabilities"))

    def upload_file(self, path: Path) -> BrokerFileState:
        payload = self._request(
            "POST", "/api/v1/files", content=path.read_bytes(), headers={"X-Filename": path.name}
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
        task_id = self._request("POST", "/api/v1/tasks", json=payload).get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise BrokerApiError(500, "gateway task response omitted task_id")
        return task_id

    def task(self, task_id: str) -> BrokerTaskState:
        return BrokerTaskState.model_validate(self._request("GET", f"/api/v1/tasks/{task_id}"))

    def invocations(self, task_id: str) -> tuple[BrokerInvocation, ...]:
        payload = self._request("GET", f"/api/v1/tasks/{task_id}/invocations")
        return tuple(BrokerInvocation.model_validate(item) for item in payload.get("items", []))

    def artifacts(self, task_id: str) -> tuple[BrokerArtifact, ...]:
        payload = self._request("GET", f"/api/v1/tasks/{task_id}/artifacts")
        return tuple(BrokerArtifact.model_validate(item) for item in payload.get("items", []))

    def download_artifact(self, task_id: str, artifact_id: str) -> bytes:
        headers = {"Authorization": "Bearer " + self.access_token()}
        response = self._client.request(
            "GET", f"/api/v1/tasks/{task_id}/artifacts/{artifact_id}", headers=headers
        )
        if response.is_error:
            raise BrokerApiError(response.status_code, response.text)
        return response.content

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
                raise BrokerTimeout(f"task {task_id} exceeded gateway wait timeout")
            self.sleep(poll_seconds)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {**kwargs.pop("headers", {}), "Authorization": "Bearer " + self.access_token()}
        response = self._client.request(method, path, headers=headers, **kwargs)
        if response.is_error:
            try:
                body = response.json()
                detail = body.get("error_description", body.get("detail", response.text))
            except ValueError:
                detail = response.text
            raise BrokerApiError(response.status_code, str(detail))
        return response.json()
