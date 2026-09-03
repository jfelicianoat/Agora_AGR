"""Atomic contract execution through AI_Broker with auditable telemetry."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agora.api.contracts import WorkItem
from agora.broker.client import BrokerApiError, BrokerClient
from agora.broker.contracts import BrokerInvocation, BrokerPolicy, BrokerTaskState
from agora.broker.request_builder import build_broker_request
from agora.cards import Card
from agora.profiles import Profile, load_profiles
from agora.skills import load_profile_skills


class BrokerTaskFailed(RuntimeError):
    def __init__(self, state: BrokerTaskState) -> None:
        super().__init__(f"AI_Broker task {state.task_id} ended as {state.status}: {state.error}")
        self.state = state


class BrokerPolicyViolation(RuntimeError):
    """The effective broker execution did not satisfy the requested policy."""


@dataclass(frozen=True, slots=True)
class BrokerExecution:
    task_id: str
    artifacts: dict[str, bytes]
    model: str | None
    audit: dict[str, Any]
    milestones: tuple[str, ...]


@dataclass(slots=True)
class BrokerExecutor:
    client: BrokerClient
    profiles_root: Path
    policy: BrokerPolicy

    def prepare_attachments(
        self,
        paths: tuple[Path, ...],
        *,
        wait_seconds: float,
    ) -> tuple[dict[str, Any], ...]:
        attachments: list[dict[str, Any]] = []
        for path in paths:
            accepted = self.client.upload_file(path)
            ready = self.client.wait_file_ready(
                accepted.file_id,
                timeout_seconds=wait_seconds,
            )
            attachments.append(
                {
                    "type": "broker_file",
                    "name": ready.filename,
                    "uri": f"broker://files/{ready.file_id}",
                    "metadata": {"file_id": ready.file_id},
                }
            )
        return tuple(attachments)

    def execute(
        self,
        work: WorkItem,
        attachments: tuple[dict[str, Any], ...],
        *,
        checkpoint: Callable[[str, str], None],
    ) -> BrokerExecution:
        card, profile = self._contracts(work)
        key = broker_idempotency_key(work)
        skills = load_profile_skills(profile.source.parent, profile.skills)
        payload = build_broker_request(
            profile,
            skills,
            card,
            policy=self.policy,
            idempotency_key=key,
            attachments=attachments,
        )
        task_id = self.client.submit(payload)
        checkpoint(task_id, key)
        state = self.client.wait_task(task_id, timeout_seconds=self.policy.timeout_seconds)
        return self.finalize(state)

    def resume(self, work: WorkItem, task_id: str) -> BrokerExecution:
        self._contracts(work)
        state = self.client.wait_task(task_id, timeout_seconds=self.policy.timeout_seconds)
        return self.finalize(state)

    def finalize(self, state: BrokerTaskState) -> BrokerExecution:
        if state.status != "completed":
            raise BrokerTaskFailed(state)
        invocations = self.client.invocations(state.task_id)
        billable = _contract_invocations(invocations)
        if self.policy.determinism == "strict":
            _validate_strict(state, invocations, self.policy)
        audit = _audit(state, invocations, self.policy)
        model = _model_label(audit.get("served_by"))
        result = _result_bytes(state.result)
        suffix = "json" if self.policy.output_format == "json" else "md"
        milestones = (
            f"AI_Broker task completed: {state.task_id}.",
            f"Effective model: {model or 'not reported'}.",
            f"Broker invocations: {len(billable)}; cost USD: {audit['total_cost_usd']:.8f}.",
            f"Determinism policy: {self.policy.determinism}; prompt compression: off.",
        )
        return BrokerExecution(
            state.task_id,
            {f"broker-result.{suffix}": result},
            model,
            audit,
            milestones,
        )

    def _contracts(self, work: WorkItem) -> tuple[Card, Profile]:
        card = Card.parse(work.card_document, source=f"remote CARD {work.filename}")
        matches = [
            profile for profile in load_profiles(self.profiles_root) if profile.name == work.profile
        ]
        if len(matches) != 1:
            raise ValueError(f"exactly one local PROFILE is required for {work.profile}")
        profile = matches[0]
        if profile.function.strip().lower() != card.function.strip().lower():
            raise ValueError("local PROFILE function differs from CARD")
        return card, profile


def broker_idempotency_key(work: WorkItem) -> str:
    raw = f"agora\0{work.filename}\0{work.attempts}\0{work.profile}"
    return "agora:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# AI_Broker 2.9 materializa el entregable bajo estas claves: app/coordinator.py
# escribe `result_markdown` y `assistant_content` en todas las estrategias. El resto
# se tolera sólo por compatibilidad futura; ninguna es la que el broker emite hoy.
# `shadow_probe` es exploración de enrutado del broker (app/shadow_probe.py): comparte
# task_id con la CARD pero no la ejecuta, puede usar otro modelo y llega a `completed`.
# Ni valida la política estricta ni se factura a la tarjeta.
NON_CONTRACT_ROLES = frozenset({"shadow_probe"})


def _contract_invocations(
    invocations: tuple[BrokerInvocation, ...],
) -> tuple[BrokerInvocation, ...]:
    return tuple(item for item in invocations if item.role not in NON_CONTRACT_ROLES)


_RESULT_KEYS = ("result_markdown", "assistant_content", "content", "text", "output", "answer")


def _result_bytes(result: dict[str, Any] | None) -> bytes:
    if not result:
        raise BrokerApiError(500, "completed task has no result")
    for key in _RESULT_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value:
            return value.encode("utf-8")
    # Fallar de forma visible: cerrar la CARD con un volcado JSON del sobre del
    # broker disfrazaría de entregable algo que no lo es.
    raise BrokerApiError(
        500, "completed task has no textual deliverable in " + ", ".join(_RESULT_KEYS)
    )


def _audit(
    state: BrokerTaskState,
    invocations: tuple[BrokerInvocation, ...],
    policy: BrokerPolicy,
) -> dict[str, Any]:
    summary = state.execution_summary or {}
    return {
        "system": "ai_broker",
        "task_id": state.task_id,
        "policy": policy.determinism,
        "prompt_compression": "off",
        "served_by": summary.get("served_by"),
        "requested_model": summary.get("requested_model"),
        "models_used": summary.get("models_used", []),
        "fallback_used": summary.get("fallback_used"),
        "total_cost_usd": sum(item.cost_usd for item in _contract_invocations(invocations)),
        "exploratory_cost_usd": sum(
            item.cost_usd for item in invocations if item.role in NON_CONTRACT_ROLES
        ),
        "invocations": [item.model_dump(mode="json") for item in invocations],
    }


def _model_label(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    components = [value.get("provider"), value.get("deployment"), value.get("model")]
    return "/".join(str(item) for item in components if item)


def _validate_strict(
    state: BrokerTaskState,
    invocations: tuple[BrokerInvocation, ...],
    policy: BrokerPolicy,
) -> None:
    summary = state.execution_summary or {}
    served = summary.get("served_by")
    target = policy.target_model or {}
    identity = ("provider", "deployment", "model")
    if not isinstance(served, dict) or any(served.get(key) != target.get(key) for key in identity):
        raise BrokerPolicyViolation("strict policy target_model was not the model served")
    successful = [
        item for item in _contract_invocations(invocations) if item.status == "completed"
    ]
    if not successful:
        raise BrokerPolicyViolation("strict policy has no successful invocation telemetry")
    for item in successful:
        generation = item.generation or {}
        if (
            generation.get("temperature") != 0.0
            or generation.get("seed") != policy.seed
            or generation.get("seed_status") != "sent"
            or generation.get("top_p") != 1.0
            or generation.get("top_p_status") != "sent"
        ):
            raise BrokerPolicyViolation("effective generation does not satisfy strict policy")
