"""Optional loopback adapter between Agora CARDs and Athena runs."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from agora.board import Board
from agora.cards import Card
from agora.profiles import Profile
from agora.remote.client import AgoraApiClient
from agora.skills import load_profile_skills

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_TERMINAL = {"completed", "failed", "cancelled", "recovery_pending"}


class AthenaApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"Athena {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class AthenaVerificationError(RuntimeError):
    pass


@dataclass(slots=True)
class AthenaAgoraAdapter:
    """Optional Athena-facing façade over Agora's existing authenticated API."""

    client: AgoraApiClient

    def submit(
        self,
        *,
        filename: str,
        function: str,
        request: str,
        idempotency_key: str,
        body: str = "",
    ) -> dict[str, Any]:
        return self.client.create_card(
            {
                "filename": filename,
                "function": function,
                "request": request,
                "body": body,
            },
            idempotency_key=idempotency_key,
        )

    def status(self, filename: str) -> dict[str, Any]:
        return self.client.card_status(filename)

    def cancel(self, filename: str, *, reason: str, idempotency_key: str) -> dict[str, Any]:
        return self.client.cancel_card(
            filename,
            reason=reason,
            idempotency_key=idempotency_key,
        )

    def artifacts(self, filename: str) -> tuple[bytes, ...]:
        status = self.status(filename)
        metadata = status.get("metadata")
        paths = metadata.get("paths", []) if isinstance(metadata, dict) else []
        if not isinstance(paths, list):
            raise ValueError("Agora CARD paths are malformed")
        return tuple(self.client.card_artifact(filename, index) for index in range(len(paths)))


@dataclass(frozen=True, slots=True)
class AthenaPolicy:
    workspace: Path
    writes: str = "off"
    execution: str = "off"
    profile: str = ""
    model: str = ""
    deliverables: tuple[str, ...] = ()
    timeout_seconds: float = 900.0

    @classmethod
    def from_profile(cls, profile: Profile, fallback_workspace: Path) -> AthenaPolicy:
        raw = profile.metadata.get("athena", {})
        if not isinstance(raw, dict):
            raise ValueError("PROFILE athena policy must be a mapping")
        writes = str(raw.get("writes", "off"))
        execution = str(raw.get("exec", "off"))
        if writes == "ask" or execution == "ask":
            raise ValueError("ask is forbidden in Athena card mode")
        if writes not in {"off", "allow"} or execution not in {"off", "allow"}:
            raise ValueError("Athena card permissions must be off or allow")
        workspace = Path(str(raw.get("workspace") or fallback_workspace)).resolve()
        deliverables_raw = raw.get("deliverables", [])
        if not isinstance(deliverables_raw, list) or any(
            not isinstance(item, str) or not item.strip() for item in deliverables_raw
        ):
            raise ValueError("Athena deliverables must be non-empty path strings")
        timeout = raw.get("timeout_seconds", 900.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("Athena timeout_seconds must be positive")
        return cls(
            workspace=workspace,
            writes=writes,
            execution=execution,
            profile=str(raw.get("profile") or ""),
            model=str(raw.get("model") or ""),
            deliverables=tuple(item.strip() for item in deliverables_raw),
            timeout_seconds=float(timeout),
        )


@dataclass(slots=True)
class AthenaClient:
    base_url: str
    token: str = field(repr=False)
    timeout: float = 15.0
    transport: httpx.BaseTransport | None = None
    sleep: Any = time.sleep
    _client: httpx.Client = field(init=False, repr=False)

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK:
            raise ValueError("Athena card runner is restricted to loopback HTTP")
        if not self.token:
            raise ValueError("Athena bearer token must not be empty")
        self._client = httpx.Client(
            base_url=self.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=self.timeout,
            transport=self.transport,
        )

    def close(self) -> None:
        self._client.close()

    def available(self) -> bool:
        health = self._client.get("/v1/health")
        if health.status_code != 200:
            return False
        auth = self._client.get("/v1/auth/check")
        return auth.status_code == 200

    def submit(self, objective: str, policy: AthenaPolicy, *, idempotency_key: str) -> str:
        payload = {
            "objective": objective,
            "workspace": str(policy.workspace),
            "writes": policy.writes,
            "exec": policy.execution,
            "profile": policy.profile,
            "model": policy.model,
            "deliverables": list(policy.deliverables),
            "session_timeout_seconds": policy.timeout_seconds,
        }
        result = self._request(
            "POST",
            "/v1/runs",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
        )
        run_id = result.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise AthenaApiError(500, "run response omitted run_id")
        return run_id

    def status(self, run_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/runs/{run_id}")

    def cancel(self, run_id: str) -> None:
        self._request("POST", f"/v1/runs/{run_id}/cancel")

    def artifact(self, key: str) -> bytes:
        response = self._client.get(f"/v1/results/{key}")
        if response.is_error:
            raise AthenaApiError(response.status_code, "artifact unavailable")
        return response.content

    def wait(
        self, run_id: str, *, timeout_seconds: float, poll_seconds: float = 1.0
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            snapshot = self.status(run_id)
            if snapshot.get("status") in _TERMINAL:
                return snapshot
            if time.monotonic() >= deadline:
                self.cancel(run_id)
                raise AthenaApiError(408, "Athena run timed out and was cancelled")
            self.sleep(poll_seconds)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._client.request(method, path, **kwargs)
        if response.is_error:
            try:
                payload = response.json()
                detail = payload.get("error", {}).get("message", response.text)
            except ValueError:
                detail = response.text
            raise AthenaApiError(response.status_code, str(detail))
        payload = response.json()
        if not isinstance(payload, dict):
            raise AthenaApiError(500, "Athena response is not an object")
        return payload


@dataclass(slots=True)
class AthenaHarness:
    client: AthenaClient
    harness_name: str = "athena"

    def is_available(self, _profile: Profile) -> bool:
        return self.client.available()

    def launch(self, board: Board, claimed_path: Path, profile: Profile) -> Path:
        card = Card.load(claimed_path)
        policy = AthenaPolicy.from_profile(profile, board.root.parent)
        objective = _objective(profile, card)
        key = "agora-athena:" + hashlib.sha256(
            f"{claimed_path.name}\0{card.attempts}\0{profile.name}".encode()
        ).hexdigest()
        run_id = self.client.submit(objective, policy, idempotency_key=key)
        board.progress(claimed_path, profile.name, [f"Athena run delegated: {run_id}."])
        snapshot = self.client.wait(run_id, timeout_seconds=policy.timeout_seconds)
        artifacts = _verified_artifacts(snapshot, policy)
        board.progress(
            claimed_path,
            profile.name,
            [f"Athena verification passed for run {run_id}."],
        )
        return board.close(
            claimed_path,
            actor=profile.name,
            paths=list(artifacts),
            model="athena",
        )


def _objective(profile: Profile, card: Card) -> str:
    skills = load_profile_skills(profile.source.parent, profile.skills)
    skill_text = "\n\n".join(skill.instructions for skill in skills)
    return (
        "# Athena CARD mode\n"
        "This run is non-interactive. Do not ask questions. If information is missing, "
        "fail visibly and stop. Do not widen permissions.\n\n"
        f"# PROFILE\n{profile.body}\n\n"
        f"# DECLARED SKILLS\n{skill_text or '(none)'}\n\n"
        f"# CARD\n{card.serialize()}"
    )


def _verified_artifacts(snapshot: dict[str, Any], policy: AthenaPolicy) -> tuple[Path, ...]:
    if snapshot.get("status") != "completed":
        raise AthenaVerificationError(f"Athena run ended as {snapshot.get('status')}")
    verification = snapshot.get("verification")
    if not isinstance(verification, dict) or verification.get("status") != "passed":
        raise AthenaVerificationError("Athena verification was inconclusive")
    memory = snapshot.get("working_memory")
    modified = memory.get("files_modified", []) if isinstance(memory, dict) else []
    candidates = policy.deliverables or tuple(str(item) for item in modified)
    if not candidates:
        raise AthenaVerificationError("Athena reported no deliverable")
    paths: list[Path] = []
    for item in candidates:
        candidate = (policy.workspace / item).resolve()
        try:
            candidate.relative_to(policy.workspace)
        except ValueError as exc:
            raise AthenaVerificationError("Athena artifact escapes its workspace") from exc
        if not candidate.is_file() or candidate.stat().st_size == 0:
            raise AthenaVerificationError(f"Athena deliverable is not verifiable: {item}")
        paths.append(candidate)
    return tuple(paths)
