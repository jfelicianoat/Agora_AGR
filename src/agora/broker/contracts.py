"""Stable Agora-side view of AI_Broker contract 2.9."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class BrokerModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class BrokerFileState(BrokerModel):
    file_id: str
    status: Literal["received", "converting", "ready", "failed"]
    filename: str
    error: dict[str, Any] | None = None


class BrokerTaskState(BrokerModel):
    task_id: str
    status: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    execution_summary: dict[str, Any] | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {"completed", "failed", "cancelled"}


class BrokerInvocation(BrokerModel):
    invocation_id: str
    role: str
    model: dict[str, Any]
    status: str
    error_code: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    latency_ms: float | None = None
    generation: dict[str, Any] | None = None
    execution_fingerprint: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class BrokerPolicy:
    determinism: Literal["strict", "routed"] = "routed"
    data_classification: Literal["public", "internal", "confidential", "local_only"] = "internal"
    timeout_seconds: int = 3000
    zombie_timeout_seconds: int = 3300
    target_model: dict[str, Any] | None = None
    max_cost_usd: float | None = None
    output_format: Literal["markdown", "text", "json"] = "markdown"
    output_language: str = "es"
    seed: int = 0

    def __post_init__(self) -> None:
        if self.timeout_seconds < 1:
            raise ValueError("broker timeout must be positive")
        if self.zombie_timeout_seconds <= self.timeout_seconds:
            raise ValueError("Agora zombie timeout must exceed broker task timeout")
        if self.determinism == "strict" and self.target_model is None:
            raise ValueError("strict policy requires an exact target_model")
        if self.determinism == "routed" and self.target_model is not None:
            raise ValueError("routed policy must let AI_Broker choose the model")
        if self.target_model is not None:
            required = {"provider", "deployment", "model"}
            if not required.issubset(self.target_model) or any(
                not isinstance(self.target_model.get(key), str)
                or not self.target_model[key].strip()
                for key in required
            ):
                raise ValueError("target_model requires provider, deployment and model")


REQUIRED_CAPABILITIES = {
    "prompt_compression_override": True,
    "invocation_telemetry": True,
    "generation_determinism": True,
    "execution_fingerprint": True,
}


def validate_capabilities(payload: dict[str, Any]) -> None:
    version = str(payload.get("contract_version", "0"))
    try:
        version_tuple = tuple(int(part) for part in version.split(".")[:2])
    except ValueError as exc:
        raise ValueError(f"invalid broker contract version: {version}") from exc
    if version_tuple < (2, 9):
        raise ValueError(f"AI_Broker contract 2.9 or newer is required; received {version}")
    missing = [
        name for name, expected in REQUIRED_CAPABILITIES.items() if payload.get(name) != expected
    ]
    if missing:
        raise ValueError("AI_Broker lacks required capabilities: " + ", ".join(missing))
