"""FastAPI application factory for Agora API v1."""

import hmac
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse

from agora.api.contracts import (
    ClaimRequest,
    CloseRequest,
    CreateCardRequest,
    ProgressRequest,
    UnblockRequest,
    WorkItem,
    YieldRequest,
)
from agora.api.service import RemoteWorkService
from agora.api.storage import EventStore, IdempotencyConflict, IdempotencyStore
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.errors import CardFormatError, ClaimConflict, InvalidTransition


@dataclass(frozen=True, slots=True)
class ApiSettings:
    token: str = field(repr=False)
    principal: str = "provisional-client"
    require_https: bool = True

    def __post_init__(self) -> None:
        if len(self.token) < 16:
            raise ValueError("provisional API token must contain at least 16 characters")
        if not self.principal.strip():
            raise ValueError("API principal must not be empty")


def create_api(application: AgoraApplication, settings: ApiSettings) -> FastAPI:
    application.initialize()
    remote = RemoteWorkService(application)
    private_root = application.workspace / ".agora"
    idempotency = IdempotencyStore(private_root / "idempotency.json")
    events = EventStore(private_root / "events.json")
    operation_lock = Lock()
    app = FastAPI(title="Agora API", version="1.0.0", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def require_https(request: Request, call_next: Callable[..., Any]):
        if settings.require_https and request.url.scheme != "https":
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "HTTPS is required"},
            )
        return await call_next(request)

    def principal(authorization: Annotated[str | None, Header()] = None) -> str:
        scheme, _, candidate = (authorization or "").partition(" ")
        valid = scheme.lower() == "bearer" and hmac.compare_digest(candidate, settings.token)
        if not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer credential",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return settings.principal

    Principal = Annotated[str, Depends(principal)]
    IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]

    @app.exception_handler(IdempotencyConflict)
    async def idempotency_conflict(_request: Request, exc: IdempotencyConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ClaimConflict)
    @app.exception_handler(InvalidTransition)
    async def state_conflict(_request: Request, exc: Exception):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(CardFormatError)
    async def invalid_card(_request: Request, exc: CardFormatError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(FileNotFoundError)
    async def missing_file(_request: Request, exc: FileNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid_value(_request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/api/v1/health")
    def health(_principal: Principal) -> dict[str, str]:
        return {"status": "ok", "api": "v1", "board_owner": "agora"}

    @app.get("/api/v1/board")
    def board(_principal: Principal) -> dict[str, Any]:
        snapshot = application.snapshot()
        return {
            "captured_at": snapshot.captured_at,
            "cards": [asdict(card) for card in snapshot.cards],
            "errors": [asdict(error) for error in snapshot.errors],
        }

    @app.get("/api/v1/cards/{state_name}/{filename}")
    def card(state_name: BoardState, filename: str, _principal: Principal) -> dict[str, object]:
        return remote.card_payload(state_name, filename)

    @app.get("/api/v1/profiles")
    def profiles(_principal: Principal) -> dict[str, Any]:
        snapshot = application.snapshot()
        return {"profiles": [asdict(profile) for profile in snapshot.profiles]}

    @app.get("/api/v1/events")
    def list_events(
        _principal: Principal,
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        return {"events": events.since(after)}

    @app.get("/api/v1/work", response_model=list[WorkItem])
    def work(
        _principal: Principal,
        profiles: Annotated[list[str], Query(min_length=1)],
    ) -> tuple[WorkItem, ...]:
        return remote.work(tuple(profiles))

    @app.get("/api/v1/claims", response_model=list[WorkItem])
    def claims(
        _principal: Principal,
        runner_id: Annotated[str, Query(min_length=1, max_length=120)],
    ) -> tuple[WorkItem, ...]:
        return remote.claimed(runner_id)

    @app.get("/api/v1/work/{filename}/inputs/{key}")
    def download_input(filename: str, key: str, _principal: Principal) -> FileResponse:
        path = remote.input_path(filename, key)
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @app.post("/api/v1/cards", status_code=201)
    def create_card(
        request: CreateCardRequest,
        authenticated: Principal,
        idempotency_key: IdempotencyKey = None,
    ) -> JSONResponse:
        key = _required_key(idempotency_key)
        body = request.model_dump(mode="json")
        digest = idempotency.digest(body)
        with operation_lock:
            cached = idempotency.lookup("create", key, digest)
            if cached:
                return _replay(cached.status_code, cached.payload)
            path = remote.create_card(request, principal=authenticated)
            payload = {"filename": path.name, "state": BoardState.PENDING.value, "replayed": False}
            idempotency.save("create", key, digest, status_code=201, payload=payload)
            events.emit("card.created", {"filename": path.name, "principal": authenticated})
            return JSONResponse(status_code=201, content=payload)

    @app.post("/api/v1/cards/{filename}/claim")
    def claim(
        filename: str,
        request: ClaimRequest,
        _principal: Principal,
        idempotency_key: IdempotencyKey = None,
    ) -> JSONResponse:
        return _mutation(
            "claim",
            filename,
            request.model_dump(mode="json"),
            idempotency_key,
            operation_lock,
            idempotency,
            events,
            lambda: remote.claim(filename, request.runner_id, request.profile),
            BoardState.IN_PROGRESS,
            "card.claimed",
        )

    @app.post("/api/v1/cards/{filename}/progress")
    def progress(
        filename: str,
        request: ProgressRequest,
        _principal: Principal,
        idempotency_key: IdempotencyKey = None,
    ) -> JSONResponse:
        return _mutation(
            "progress",
            filename,
            request.model_dump(mode="json"),
            idempotency_key,
            operation_lock,
            idempotency,
            events,
            lambda: remote.progress(
                filename,
                request.runner_id,
                request.milestones,
                request.checkpoint,
            ),
            BoardState.IN_PROGRESS,
            "card.progressed",
        )

    @app.post("/api/v1/cards/{filename}/close")
    def close(
        filename: str,
        request: CloseRequest,
        _principal: Principal,
        idempotency_key: IdempotencyKey = None,
    ) -> JSONResponse:
        return _mutation(
            "close",
            filename,
            request.model_dump(mode="json"),
            idempotency_key,
            operation_lock,
            idempotency,
            events,
            lambda: remote.close(filename, request),
            BoardState.DONE,
            "card.closed",
        )

    @app.post("/api/v1/cards/{filename}/yield")
    def yield_card(
        filename: str,
        request: YieldRequest,
        _principal: Principal,
        idempotency_key: IdempotencyKey = None,
    ) -> JSONResponse:
        return _mutation(
            "yield",
            filename,
            request.model_dump(mode="json"),
            idempotency_key,
            operation_lock,
            idempotency,
            events,
            lambda: remote.yield_card(
                filename,
                request.runner_id,
                request.reason,
                increment_attempts=request.increment_attempts,
            ),
            BoardState.PENDING,
            "card.yielded",
        )

    @app.post("/api/v1/admin/blocked/{filename}/unblock")
    def unblock(
        filename: str,
        request: UnblockRequest,
        authenticated: Principal,
        idempotency_key: IdempotencyKey = None,
    ) -> JSONResponse:
        return _mutation(
            "unblock",
            filename,
            request.model_dump(mode="json"),
            idempotency_key,
            operation_lock,
            idempotency,
            events,
            lambda: remote.unblock(filename, principal=authenticated, reason=request.reason),
            BoardState.PENDING,
            "card.unblocked",
        )

    return app


def _required_key(value: str | None) -> str:
    key = (value or "").strip()
    if not key or len(key) > 200:
        raise HTTPException(status_code=400, detail="valid Idempotency-Key is required")
    return key


def _replay(status_code: int, payload: dict[str, Any]) -> JSONResponse:
    replay = dict(payload)
    replay["replayed"] = True
    return JSONResponse(status_code=status_code, content=replay)


def _mutation(
    scope: str,
    filename: str,
    body: dict[str, Any],
    key_value: str | None,
    lock: Lock,
    idempotency: IdempotencyStore,
    events: EventStore,
    operation: Callable[[], Path],
    destination: BoardState,
    event_kind: str,
) -> JSONResponse:
    key = _required_key(key_value)
    digest = idempotency.digest({"filename": filename, "body": body})
    with lock:
        cached = idempotency.lookup(scope, key, digest)
        if cached:
            return _replay(cached.status_code, cached.payload)
        path = operation()
        payload = {"filename": path.name, "state": destination.value, "replayed": False}
        idempotency.save(scope, key, digest, status_code=200, payload=payload)
        events.emit(event_kind, {"filename": path.name})
        return JSONResponse(status_code=200, content=payload)
