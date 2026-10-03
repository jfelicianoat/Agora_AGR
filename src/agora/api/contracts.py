"""Pydantic wire contracts for API v1."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewVerification(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    status: Literal["passed", "failed", "missing"]
    required: StrictBool = True


class ReviewContext(StrictModel):
    """Explicit result-review input, separate from personal review analysis."""

    goal: str = Field(min_length=1, max_length=20_000)
    task_type: str = Field(min_length=1, max_length=128)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=100)
    agent_output: str = Field(min_length=1, max_length=100_000)
    verifications: list[ReviewVerification] = Field(default_factory=list, max_length=100)
    evidence: dict[str, str] = Field(default_factory=dict)
    required_evidence: list[str] = Field(default_factory=list, max_length=100)
    mandatory_review: StrictBool = False
    explicit_review_requested: StrictBool = False
    sensitive: StrictBool = False
    destructive: StrictBool = False
    publication_requires_review: StrictBool = False
    deployment_requires_review: StrictBool = False


class ExternalReference(StrictModel):
    """El identificador que el cliente ya tiene, para reconciliar despues.

    Agora **no lo interpreta**: ni valida el formato de `id`, ni conoce el
    `system`, ni deduce nada de ninguno de los dos. Solo lo guarda y deja
    buscarlo.

    Tres cosas que este campo **no** es:

    - **No es una clave de seguridad.** Conocer una referencia no da acceso a
      nada; el acceso lo dan el token y los ambitos, como en todo lo demas.
      Buscar por referencia exige `cards:read` igual que leer una tarjeta.
    - **No es unica.** Dos tarjetas pueden compartirla —un reintento, un
      encargo partido en dos— y por eso buscar devuelve una **lista**. Tratarla
      como unica la convertiria en una clave primaria de facto, que es
      justamente lo que no debe ser.
    - **No es la identidad de la tarjeta.** Esa sigue siendo el `filename`.
    """

    system: str = Field(min_length=1, max_length=120)
    id: str = Field(min_length=1, max_length=200)
    #: La forma de este campo, no la del trabajo del cliente. Sube si algun dia
    #: se le anaden campos, para que un cliente antiguo sepa que esta leyendo.
    version: int = Field(default=1, ge=1, le=1000)


class CreateCardRequest(StrictModel):
    filename: str = Field(min_length=4, max_length=180)
    function: str = Field(min_length=1, max_length=120)
    request: str = Field(min_length=1, max_length=20_000)
    inputs: dict[str, Any] | None = None
    destination: str | None = Field(default=None, max_length=500)
    priority: str | int = "normal"
    recipient: str | None = Field(default=None, max_length=120)
    max_attempts: int = Field(default=3, ge=1, le=100)
    body: str = Field(default="", max_length=100_000)
    external_reference: ExternalReference | None = None
    review_context: ReviewContext | None = None


class ClaimRequest(StrictModel):
    runner_id: str = Field(min_length=1, max_length=120)
    profile: str = Field(min_length=1, max_length=120)


class ProgressRequest(StrictModel):
    runner_id: str = Field(min_length=1, max_length=120)
    milestones: list[str] = Field(min_length=1, max_length=100)
    checkpoint: dict[str, str] | None = None
    review_gate_audit: dict[str, Any] | None = None


class RemoteArtifact(StrictModel):
    name: str = Field(min_length=1, max_length=180)
    content_base64: str = Field(min_length=1)


class CloseRequest(StrictModel):
    runner_id: str = Field(min_length=1, max_length=120)
    artifacts: list[RemoteArtifact] = Field(min_length=1, max_length=50)
    model: str | None = Field(default=None, max_length=240)
    execution_audit: dict[str, Any] | None = None


class YieldRequest(StrictModel):
    runner_id: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2_000)
    increment_attempts: bool = False


class UnblockRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=2_000)


class CancelRequest(StrictModel):
    reason: str = Field(min_length=1, max_length=2_000)


class InputResource(BaseModel):
    key: str
    filename: str
    size_bytes: int
    sha256: str
    download_url: str


class WorkItem(BaseModel):
    filename: str
    function: str
    request: str
    priority: str
    attempts: int
    profile: str
    card_document: str = ""
    inputs: list[InputResource] = Field(default_factory=list)
    # Identidad del tablero que emite la tarjeta. Entra en la clave de
    # idempotencia del broker para que dos tableros con una tarjeta del mismo
    # nombre no colisionen. Vacío = tablero anterior a esta versión.
    board_id: str = ""


class StateResponse(BaseModel):
    filename: str
    state: str
    replayed: bool = False


class RunnerOutcome(BaseModel):
    status: Literal[
        "completed",
        "offline",
        "idle",
        "lost_claim",
        "failed",
        "waiting_attachment",
        "broker_unavailable",
        "broker_running",
        # El broker en marcha no puede prometer lo que la tarjeta exige
        # (p. ej. exclusividad de contenido en un contrato anterior al 2.10).
        "contract_unsupported",
    ]
    card: str | None = None
    detail: str = ""
