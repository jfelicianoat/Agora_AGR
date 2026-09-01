"""Pydantic wire contracts for API v1."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class ClaimRequest(StrictModel):
    runner_id: str = Field(min_length=1, max_length=120)
    profile: str = Field(min_length=1, max_length=120)


class ProgressRequest(StrictModel):
    runner_id: str = Field(min_length=1, max_length=120)
    milestones: list[str] = Field(min_length=1, max_length=100)
    checkpoint: dict[str, str] | None = None


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
    ]
    card: str | None = None
    detail: str = ""
