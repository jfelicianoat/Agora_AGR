"""Stable Agora-side view of the AI_Broker contract (2.9 mínimo, 2.10 preferido)."""

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
    # `created: false` = el broker ya tenía estos bytes (dedupe por SHA-256) y
    # devuelve el mismo `file_id`. Comprobado en vivo el 2026-09-04.
    created: bool | None = None


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
    # Contrato 2.10 (Client_API.md, 8.1). Ausente en 2.9: `None` significa
    # «este broker no se pronuncia», no «no es contractual».
    contractual: bool | None = None
    # Contrato 2.10 (8.5): {"requested": ..., "effective": ...}. `None` en filas
    # anteriores al 2.10 y en llamadas que no envían prompt de usuario.
    prompt_compression: dict[str, Any] | None = None
    content_source: str | None = None
    excluded_from_model_learning: bool | None = None


class BrokerArtifact(BrokerModel):
    """Fichero producido por una tarea (Client_API.md, 8.3)."""

    artifact_id: str
    artifact_type: str
    filename: str
    media_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    created_at: str | None = None
    download_url: str | None = None
    available: bool = True
    # `final: true` marca el entregable; una tarea completada tiene exactamente uno.
    # Ausente en brokers anteriores al 2.10, donde no hay forma de distinguirlo.
    final: bool | None = None


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
    # AI_Broker rechaza con 422 CONTRACT_VALIDATION_FAILED un output.format json
    # sin esquema (app/schemas.py: TaskOutput.require_schema_for_json).
    output_schema: dict[str, Any] | None = None
    seed: int = 0
    # Contrato 2.10, 8.4. `False` pide exclusividad de contenido: sólo el modelo
    # que responde ve el prompt. `None` = decidir por clasificación de datos.
    auxiliary_invocations: bool | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds < 1:
            raise ValueError("broker timeout must be positive")
        if self.zombie_timeout_seconds <= self.timeout_seconds:
            raise ValueError("Agora zombie timeout must exceed broker task timeout")
        if self.output_format == "json" and not self.output_schema:
            raise ValueError("json output requires an explicit output_schema")
        if self.output_format != "json" and self.output_schema is not None:
            raise ValueError("output_schema only applies to json output")
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

    @property
    def confidential(self) -> bool:
        return self.data_classification in {"confidential", "local_only"}

    @property
    def auxiliary_invocations_allowed(self) -> bool:
        """Si esta tarjeta tolera que otro modelo vea su contenido.

        Contenido confidencial no lo tolera: el sondeo en sombra respeta la
        clasificación de datos (sólo modelos locales), pero «local» no es «el
        modelo aprobado». Ver Client_API.md, 8.4.
        """
        if self.auxiliary_invocations is not None:
            return self.auxiliary_invocations
        return not self.confidential

    @property
    def requires_content_exclusivity(self) -> bool:
        """Tarjetas que exigen que sólo el modelo aprobado vea el contenido.

        `strict` lo exige por definición: fija `target_model` con
        `fallback_allowed: false`, que en 2.10 apaga el sondeo por garantía
        implícita. En 2.9 no había tal garantía, así que estas tarjetas deben
        rechazarse antes de encolarse.
        """
        return self.determinism == "strict" or not self.auxiliary_invocations_allowed


class BrokerContractUnsupported(RuntimeError):
    """El broker en marcha no puede prometer lo que la tarjeta exige."""


REQUIRED_CAPABILITIES = {
    "prompt_compression_override": True,
    "invocation_telemetry": True,
    "generation_determinism": True,
    "execution_fingerprint": True,
}


@dataclass(frozen=True, slots=True)
class BrokerCapabilities:
    """Lo que promete el broker que está en marcha, no lo que dice el documento."""

    version: tuple[int, ...]
    raw: dict[str, Any]

    @property
    def invocation_contract(self) -> bool:
        """`role`/`status` enumerados y `contractual` en cada invocación (8.1)."""
        return bool(self.raw.get("invocation_contract"))

    @property
    def prompt_compression_echo(self) -> bool:
        """Eco `{requested, effective}` por invocación (8.5)."""
        return bool(self.raw.get("prompt_compression_echo"))

    @property
    def canonical_artifacts(self) -> bool:
        """`final: true` marca el entregable en /artifacts (8.3)."""
        return bool(self.raw.get("canonical_artifacts"))

    @property
    def task_artifacts(self) -> bool:
        return bool(self.raw.get("task_artifacts"))

    @property
    def auxiliary_invocations(self) -> bool:
        """Si este broker hace sondeo en sombra. Ausente en 2.9: lo hacía."""
        value = self.raw.get("auxiliary_invocations")
        return True if value is None else bool(value)

    @property
    def auxiliary_invocations_optout(self) -> bool:
        return bool(self.raw.get("auxiliary_invocations_optout"))

    def ensure_supports(self, policy: BrokerPolicy) -> None:
        """Rechaza aquí lo que el broker no puede prometer, no al leer telemetría.

        Client_API.md, 8.4: «Si tus contratos no las toleran y el broker no ofrece
        el opt-out, rechaza la tarjeta ahí».
        """
        if not policy.requires_content_exclusivity:
            return
        if not self.auxiliary_invocations:
            return  # El operador las tiene apagadas: no hay nada que apagar.
        if self.version < (2, 10):
            raise BrokerContractUnsupported(
                "exclusive-content CARDs require AI_Broker 2.10: contract "
                f"{'.'.join(str(part) for part in self.version)} probes other "
                "models under this task_id"
            )
        if policy.determinism == "strict":
            return  # Garantía implícita: target_model + fallback_allowed: false.
        if not self.auxiliary_invocations_optout:
            raise BrokerContractUnsupported(
                "broker performs auxiliary invocations and does not accept the opt-out"
            )


def validate_capabilities(payload: dict[str, Any]) -> BrokerCapabilities:
    version = str(payload.get("contract_version", "0"))
    try:
        version_tuple = tuple(int(part) for part in version.split(".")[:2])
    except ValueError as exc:
        raise ValueError(f"invalid broker contract version: {version}") from exc
    if len(version_tuple) != 2:
        raise ValueError(f"invalid broker contract version: {version}")
    if version_tuple < (2, 9):
        raise ValueError(f"AI_Broker contract 2.9 or newer is required; received {version}")
    missing = [
        name for name, expected in REQUIRED_CAPABILITIES.items() if payload.get(name) != expected
    ]
    if missing:
        raise ValueError("AI_Broker lacks required capabilities: " + ", ".join(missing))
    return BrokerCapabilities(version_tuple, payload)
