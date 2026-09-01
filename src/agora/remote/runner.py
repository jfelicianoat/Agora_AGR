"""Generic pull runner for API v1."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Protocol

import httpx

from agora.api.contracts import RunnerOutcome, WorkItem
from agora.remote.client import AgoraApiClient, AgoraApiError


@dataclass(frozen=True, slots=True)
class RemoteOutput:
    artifacts: dict[str, bytes]
    model: str | None = None


class RemoteHarness(Protocol):
    def execute(self, work: WorkItem) -> RemoteOutput: ...


@dataclass(slots=True)
class DeterministicRemoteHarness:
    def execute(self, work: WorkItem) -> RemoteOutput:
        content = (
            "Agora deterministic remote artifact\n"
            f"profile: {work.profile}\n"
            f"function: {work.function}\n"
            f"request: {work.request}\n"
        ).encode()
        return RemoteOutput({f"{work.filename.removesuffix('.md')}.txt": content}, "remote-f0")


@dataclass(slots=True)
class RemoteRunner:
    client: AgoraApiClient
    runner_id: str
    profiles: tuple[str, ...]
    harness: RemoteHarness

    def run_once(self) -> RunnerOutcome:
        try:
            work = self.client.work(self.profiles)
        except httpx.HTTPError as exc:
            return RunnerOutcome(status="offline", detail=f"{type(exc).__name__}: {exc}")
        except AgoraApiError as exc:
            return RunnerOutcome(status="failed", detail=str(exc))
        if not work:
            return RunnerOutcome(status="idle", detail="no compatible CARD available")
        selected = work[0]
        attempt_key = _attempt_key(self.runner_id, selected)
        try:
            self.client.claim(
                selected.filename,
                runner_id=self.runner_id,
                profile=selected.profile,
                idempotency_key=f"{attempt_key}:claim",
            )
        except AgoraApiError as exc:
            if exc.status_code == 409:
                return RunnerOutcome(status="lost_claim", card=selected.filename, detail=str(exc))
            return RunnerOutcome(status="failed", card=selected.filename, detail=str(exc))
        except httpx.HTTPError as exc:
            return RunnerOutcome(
                status="offline", card=selected.filename, detail=f"{type(exc).__name__}: {exc}"
            )
        try:
            output = self.harness.execute(selected)
            self.client.progress(
                selected.filename,
                runner_id=self.runner_id,
                milestones=["Remote harness completed; uploading verified artifacts."],
                idempotency_key=f"{attempt_key}:progress",
            )
            artifacts = [
                {
                    "name": name,
                    "content_base64": base64.b64encode(payload).decode("ascii"),
                }
                for name, payload in output.artifacts.items()
            ]
            self.client.close_card(
                selected.filename,
                runner_id=self.runner_id,
                artifacts=artifacts,
                model=output.model,
                idempotency_key=f"{attempt_key}:close",
            )
        except (AgoraApiError, httpx.HTTPError, OSError, ValueError) as exc:
            return RunnerOutcome(status="failed", card=selected.filename, detail=str(exc))
        return RunnerOutcome(status="completed", card=selected.filename)


def _attempt_key(runner_id: str, work: WorkItem) -> str:
    value = f"{runner_id}\0{work.filename}\0{work.attempts}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
