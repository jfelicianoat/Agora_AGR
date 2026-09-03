"""AI-side pull runner coordinating Agora API and a loopback AI_Broker."""

from __future__ import annotations

import base64
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from agora.api.contracts import RunnerOutcome, WorkItem
from agora.broker.client import BrokerApiError, BrokerClient, BrokerTimeout
from agora.broker.contracts import BrokerContractUnsupported
from agora.broker.credentials import SessionTokenUnavailable
from agora.broker.executor import (
    BrokerExecution,
    BrokerExecutor,
    BrokerPolicyViolation,
    BrokerTaskFailed,
)
from agora.cards import Card
from agora.documents import atomic_write_bytes
from agora.remote.client import AgoraApiClient, AgoraApiError


@dataclass(slots=True)
class AiRunner:
    agora: AgoraApiClient
    broker: BrokerClient
    executor: BrokerExecutor
    runner_id: str
    profiles: tuple[str, ...]
    attachment_wait_seconds: float = 5.0
    renew_broker: Callable[[], BrokerClient] | None = None
    # Adjuntos ya subidos, por tarjeta y digest. Un adjunto que tarda en
    # convertirse hace que el runner vuelva a sondear; sin esto se reenviaban
    # los bytes en cada vuelta. El broker deduplica por SHA-256, así que un
    # reinicio del runner cuesta una subida más, no un fichero huérfano.
    _uploads: dict[str, dict[str, str]] = field(default_factory=dict, repr=False)

    def run_once(self, *, now: datetime | None = None) -> RunnerOutcome:
        try:
            self._check_broker()
        except BrokerApiError as exc:
            if exc.status_code not in {401, 403} or self.renew_broker is None:
                return RunnerOutcome(status="broker_unavailable", detail=str(exc))
            try:
                replacement = self.renew_broker()
                self.broker.close()
                self.broker = replacement
                self.executor.client = replacement
                self._check_broker()
            except (
                httpx.HTTPError,
                BrokerApiError,
                SessionTokenUnavailable,
                ValueError,
            ) as renewed:
                return RunnerOutcome(status="broker_unavailable", detail=str(renewed))
        except (httpx.HTTPError, ValueError) as exc:
            return RunnerOutcome(status="broker_unavailable", detail=str(exc))
        try:
            claimed = self.agora.claimed(self.runner_id)
        except (httpx.HTTPError, AgoraApiError) as exc:
            return RunnerOutcome(status="offline", detail=str(exc))
        if claimed:
            return self._recover(claimed[0], now=now)
        try:
            work = self.agora.work(self.profiles)
        except (httpx.HTTPError, AgoraApiError) as exc:
            return RunnerOutcome(status="offline", detail=str(exc))
        if not work:
            return RunnerOutcome(status="idle", detail="no compatible CARD available")
        selected = work[0]
        try:
            attachments = self._prepare_inputs(selected)
        except BrokerTimeout as exc:
            return RunnerOutcome(
                status="waiting_attachment", card=selected.filename, detail=str(exc)
            )
        except (httpx.HTTPError, BrokerApiError, AgoraApiError, OSError) as exc:
            return RunnerOutcome(
                status="broker_unavailable", card=selected.filename, detail=str(exc)
            )
        attempt = _attempt_prefix(self.runner_id, selected)
        try:
            self.agora.claim(
                selected.filename,
                runner_id=self.runner_id,
                profile=selected.profile,
                idempotency_key=f"{attempt}:claim",
            )
        except AgoraApiError as exc:
            # Otro runner se la llevó primero: no es un fallo de esta tarjeta.
            if exc.status_code == 409:
                return RunnerOutcome(
                    status="lost_claim", card=selected.filename, detail=str(exc)
                )
            return RunnerOutcome(status="failed", card=selected.filename, detail=str(exc))
        return self._execute(selected, attachments, attempt)

    def _check_broker(self) -> None:
        if not self.broker.health():
            raise BrokerApiError(503, "broker health failed")
        self.broker.capabilities()

    def _recover(self, work: WorkItem, *, now: datetime | None) -> RunnerOutcome:
        card = Card.parse(work.card_document, source=f"claimed CARD {work.filename}")
        remote = card.metadata.get("remote")
        task_id = remote.get("task_id") if isinstance(remote, dict) else None
        if isinstance(task_id, str) and task_id:
            claimed_at = _timestamp(card.metadata.get("claimed"))
            current = (now or datetime.now(UTC)).astimezone(UTC)
            if claimed_at is not None:
                age = (current - claimed_at).total_seconds()
                zombie_after = self.executor.policy_for_profile_name(
                    work.profile
                ).zombie_timeout_seconds
                if age > zombie_after:
                    return self._cancel_zombie(work, task_id)
            try:
                execution = self.executor.resume(work, task_id)
            except (BrokerTaskFailed, BrokerPolicyViolation) as exc:
                return self._yield_failed(work, str(exc))
            except BrokerTimeout:
                return RunnerOutcome(
                    status="broker_running",
                    card=work.filename,
                    detail="broker task remains within the legitimate execution window",
                )
            except (httpx.HTTPError, BrokerApiError, AgoraApiError) as exc:
                return RunnerOutcome(
                    status="broker_unavailable", card=work.filename, detail=str(exc)
                )
            return self._close(work, execution, _attempt_prefix(self.runner_id, work))
        try:
            attachments = self._prepare_inputs(work)
        except BrokerTimeout as exc:
            return RunnerOutcome(status="waiting_attachment", card=work.filename, detail=str(exc))
        return self._execute(work, attachments, _attempt_prefix(self.runner_id, work))

    def _execute(
        self,
        work: WorkItem,
        attachments: tuple[dict[str, object], ...],
        attempt: str,
    ) -> RunnerOutcome:
        def checkpoint(task_id: str, broker_key: str) -> None:
            self.agora.progress(
                work.filename,
                runner_id=self.runner_id,
                milestones=[f"AI_Broker task submitted: {task_id}."],
                checkpoint={
                    "system": "ai_broker",
                    "task_id": task_id,
                    "idempotency_key": broker_key,
                },
                idempotency_key=f"{attempt}:checkpoint",
            )

        try:
            execution = self.executor.execute(work, attachments, checkpoint=checkpoint)
        except BrokerContractUnsupported as exc:
            # No es culpa de la tarjeta: devolverla sin gastar intento para que
            # otro runner —o este mismo tras actualizar el broker— la recoja.
            return self._yield_unsupported(work, str(exc))
        except (BrokerTaskFailed, BrokerPolicyViolation) as exc:
            return self._yield_failed(work, str(exc))
        except BrokerTimeout as exc:
            return RunnerOutcome(status="broker_running", card=work.filename, detail=str(exc))
        except (httpx.HTTPError, BrokerApiError, AgoraApiError, OSError, ValueError) as exc:
            return RunnerOutcome(status="failed", card=work.filename, detail=str(exc))
        return self._close(work, execution, attempt)

    def _close(self, work: WorkItem, execution: BrokerExecution, attempt: str) -> RunnerOutcome:
        try:
            self.agora.progress(
                work.filename,
                runner_id=self.runner_id,
                milestones=list(execution.milestones),
                idempotency_key=f"{attempt}:audit",
            )
            artifacts = [
                {
                    "name": name,
                    "content_base64": base64.b64encode(payload).decode("ascii"),
                }
                for name, payload in execution.artifacts.items()
            ]
            self.agora.close_card(
                work.filename,
                runner_id=self.runner_id,
                artifacts=artifacts,
                model=execution.model,
                execution_audit=execution.audit,
                idempotency_key=f"{attempt}:close",
            )
        except (httpx.HTTPError, AgoraApiError) as exc:
            return RunnerOutcome(status="failed", card=work.filename, detail=str(exc))
        self._uploads.pop(work.filename, None)
        return RunnerOutcome(status="completed", card=work.filename)

    def _cancel_zombie(self, work: WorkItem, task_id: str) -> RunnerOutcome:
        try:
            state = self.broker.task(task_id)
            if not state.terminal:
                self.broker.cancel(task_id)
            self.agora.yield_card(
                work.filename,
                runner_id=self.runner_id,
                reason=f"Cancelled stale AI_Broker task {task_id} before redispatch.",
                increment_attempts=True,
                idempotency_key=f"{_attempt_prefix(self.runner_id, work)}:zombie",
            )
        except (httpx.HTTPError, BrokerApiError, AgoraApiError) as exc:
            return RunnerOutcome(status="failed", card=work.filename, detail=str(exc))
        return RunnerOutcome(status="failed", card=work.filename, detail="zombie cancelled")

    def _yield_unsupported(self, work: WorkItem, reason: str) -> RunnerOutcome:
        try:
            self.agora.yield_card(
                work.filename,
                runner_id=self.runner_id,
                reason=f"Broker contract cannot honour this CARD: {reason}",
                increment_attempts=False,
                idempotency_key=f"{_attempt_prefix(self.runner_id, work)}:unsupported",
            )
        except (httpx.HTTPError, AgoraApiError) as exc:
            return RunnerOutcome(status="failed", card=work.filename, detail=str(exc))
        return RunnerOutcome(status="contract_unsupported", card=work.filename, detail=reason)

    def _yield_failed(self, work: WorkItem, reason: str) -> RunnerOutcome:
        try:
            self.agora.yield_card(
                work.filename,
                runner_id=self.runner_id,
                reason=reason,
                increment_attempts=True,
                idempotency_key=f"{_attempt_prefix(self.runner_id, work)}:failed",
            )
        except (httpx.HTTPError, AgoraApiError) as exc:
            return RunnerOutcome(status="failed", card=work.filename, detail=str(exc))
        return RunnerOutcome(status="failed", card=work.filename, detail=reason)

    def _prepare_inputs(self, work: WorkItem) -> tuple[dict[str, object], ...]:
        if not work.inputs:
            return ()
        with tempfile.TemporaryDirectory(prefix="agora-broker-inputs-") as directory:
            paths: list[Path] = []
            for resource in work.inputs:
                payload = self.agora.download_input(resource)
                candidate = Path(resource.filename)
                if candidate.name != resource.filename or resource.filename in {".", ".."}:
                    raise ValueError("remote input filename must be a plain name")
                path = Path(directory) / resource.key / resource.filename
                atomic_write_bytes(path, payload)
                paths.append(path)
            return self.executor.prepare_attachments(
                tuple(paths),
                wait_seconds=self.attachment_wait_seconds,
                uploaded=self._uploads.setdefault(work.filename, {}),
            )


def _attempt_prefix(runner_id: str, work: WorkItem) -> str:
    from agora.remote.runner import _attempt_key

    return _attempt_key(runner_id, work)


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
