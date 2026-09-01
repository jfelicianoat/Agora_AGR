"""Strict HTTPS client for Agora API v1."""

from __future__ import annotations

import hashlib
import ssl
from dataclasses import dataclass, field
from typing import Any

import httpx

from agora.api.contracts import InputResource, WorkItem


class AgoraApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"Agora API {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


@dataclass(slots=True)
class AgoraApiClient:
    base_url: str
    token: str = field(repr=False)
    verify: bool | str | ssl.SSLContext = True
    timeout: float = 15.0
    transport: httpx.BaseTransport | None = None
    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.base_url.lower().startswith("https://"):
            raise ValueError("AgoraApiClient requires an https:// base URL")
        self._client = httpx.Client(
            base_url=self.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {self.token}"},
            verify=self.verify,
            timeout=self.timeout,
            transport=self.transport,
        )

    def close(self) -> None:
        self._client.close()

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/health")

    def work(self, profiles: tuple[str, ...]) -> tuple[WorkItem, ...]:
        response = self._request(
            "GET",
            "/api/v1/work",
            params=[("profiles", profile) for profile in profiles],
        )
        if not isinstance(response, list):
            raise AgoraApiError(500, "work response is not a list")
        return tuple(WorkItem.model_validate(item) for item in response)

    def claimed(self, runner_id: str) -> tuple[WorkItem, ...]:
        response = self._request("GET", "/api/v1/claims", params={"runner_id": runner_id})
        if not isinstance(response, list):
            raise AgoraApiError(500, "claims response is not a list")
        return tuple(WorkItem.model_validate(item) for item in response)

    def download_input(self, resource: InputResource) -> bytes:
        response = self._client.get(resource.download_url)
        if response.is_error:
            raise AgoraApiError(response.status_code, "input download failed")
        payload = response.content
        if len(payload) != resource.size_bytes:
            raise AgoraApiError(409, "input size changed during transfer")
        if hashlib.sha256(payload).hexdigest() != resource.sha256:
            raise AgoraApiError(409, "input digest changed during transfer")
        return payload

    def claim(
        self, filename: str, *, runner_id: str, profile: str, idempotency_key: str
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/cards/{_plain_name(filename)}/claim",
            json={"runner_id": runner_id, "profile": profile},
            headers={"Idempotency-Key": idempotency_key},
        )

    def progress(
        self,
        filename: str,
        *,
        runner_id: str,
        milestones: list[str],
        idempotency_key: str,
        checkpoint: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/cards/{_plain_name(filename)}/progress",
            json={
                "runner_id": runner_id,
                "milestones": milestones,
                "checkpoint": checkpoint,
            },
            headers={"Idempotency-Key": idempotency_key},
        )

    def close_card(
        self,
        filename: str,
        *,
        runner_id: str,
        artifacts: list[dict[str, str]],
        model: str | None,
        idempotency_key: str,
        execution_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/cards/{_plain_name(filename)}/close",
            json={
                "runner_id": runner_id,
                "artifacts": artifacts,
                "model": model,
                "execution_audit": execution_audit,
            },
            headers={"Idempotency-Key": idempotency_key},
        )

    def yield_card(
        self,
        filename: str,
        *,
        runner_id: str,
        reason: str,
        idempotency_key: str,
        increment_attempts: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/cards/{_plain_name(filename)}/yield",
            json={
                "runner_id": runner_id,
                "reason": reason,
                "increment_attempts": increment_attempts,
            },
            headers={"Idempotency-Key": idempotency_key},
        )

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        response = self._client.request(method, url, **kwargs)
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise AgoraApiError(response.status_code, str(detail))
        return response.json()

    def __enter__(self) -> AgoraApiClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _plain_name(filename: str) -> str:
    if "/" in filename or "\\" in filename or not filename.lower().endswith(".md"):
        raise ValueError("CARD filename must be a plain .md filename")
    return filename
