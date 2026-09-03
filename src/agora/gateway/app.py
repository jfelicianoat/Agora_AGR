"""FastAPI application exposing AI_Broker through OAuth without owning a Board."""

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse, Response

from agora.broker.client import BrokerApiError, BrokerTimeout
from agora.gateway.session import BrokerSession
from agora.security.fastapi import ALL_GATEWAY_SCOPES, BearerAuthenticator, install_oauth_endpoints
from agora.security.oauth import OAuthAuthority, OAuthPrincipal


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    oauth_authority: OAuthAuthority
    audience: str = "agora-gateway"
    require_https: bool = True
    max_upload_bytes: int = 50 * 1024 * 1024


def create_gateway(session: BrokerSession, settings: GatewaySettings) -> FastAPI:
    app = FastAPI(title="Agora AI Gateway", version="1.0.0", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def require_https(request: Request, call_next: Callable[..., Any]) -> Response:
        if settings.require_https and request.url.scheme != "https":
            return JSONResponse(status_code=400, content={"detail": "HTTPS is required"})
        forwarded: Response = await call_next(request)
        return forwarded

    authenticate = BearerAuthenticator(
        audience=settings.audience,
        authority=settings.oauth_authority,
        legacy_scopes=ALL_GATEWAY_SCOPES,
    )
    AnyPrincipal = Annotated[OAuthPrincipal, Security(authenticate)]
    ReadPrincipal = Annotated[
        OAuthPrincipal, Security(authenticate, scopes=["gateway:read"])
    ]
    SubmitPrincipal = Annotated[
        OAuthPrincipal, Security(authenticate, scopes=["gateway:submit"])
    ]
    CancelPrincipal = Annotated[
        OAuthPrincipal, Security(authenticate, scopes=["gateway:cancel"])
    ]
    Filename = Annotated[str, Header(alias="X-Filename")]

    install_oauth_endpoints(app, settings.oauth_authority)

    @app.exception_handler(BrokerApiError)
    async def broker_error(_request: Request, exc: BrokerApiError) -> Response:
        code, error = _broker_error(exc.status_code)
        return JSONResponse(
            status_code=code,
            content={"error": error, "error_description": exc.detail},
        )

    @app.exception_handler(BrokerTimeout)
    async def broker_timeout(_request: Request, exc: BrokerTimeout) -> Response:
        return JSONResponse(
            status_code=504,
            content={"error": "broker_timeout", "error_description": str(exc)},
        )

    @app.exception_handler(httpx.RequestError)
    async def broker_unavailable(_request: Request, _exc: httpx.RequestError) -> Response:
        return JSONResponse(
            status_code=503,
            content={"error": "broker_unavailable", "error_description": "AI service unavailable"},
        )

    @app.get("/api/v1/health")
    def health(_principal: AnyPrincipal) -> dict[str, str]:
        return {"status": "ok", "gateway": "v1", "queue_owner": "ai_broker"}

    @app.get("/api/v1/capabilities")
    def capabilities(_principal: ReadPrincipal) -> dict[str, Any]:
        payload: dict[str, Any] = session.call("capabilities")
        return payload

    @app.post("/api/v1/files", status_code=status.HTTP_202_ACCEPTED)
    async def upload(
        request: Request, filename: Filename, _principal: SubmitPrincipal
    ) -> dict[str, Any]:
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise HTTPException(status_code=422, detail="X-Filename is invalid")
        body = await request.body()
        if not body or len(body) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="upload size is invalid")
        # El broker registra el nombre del fichero que sube la pasarela: un
        # temporal `agora-gateway-<aleatorio>-<nombre>` le robaba al cliente el
        # nombre original. El aislamiento lo da el directorio, no el nombre.
        with tempfile.TemporaryDirectory(prefix="agora-gateway-") as directory:
            temporary = Path(directory) / safe_name
            temporary.write_bytes(body)
            accepted: dict[str, Any] = session.call("upload_file", temporary).model_dump(
                mode="json"
            )
            return accepted

    @app.get("/api/v1/files/{file_id}")
    def file_state(file_id: str, _principal: ReadPrincipal) -> dict[str, Any]:
        payload: dict[str, Any] = session.call("file_state", file_id).model_dump(mode="json")
        return payload

    @app.post("/api/v1/tasks", status_code=status.HTTP_202_ACCEPTED)
    def submit(payload: dict[str, Any], principal: SubmitPrincipal) -> dict[str, str]:
        if "origin" in payload:
            raise HTTPException(status_code=422, detail="origin is derived from OAuth identity")
        content = payload.get("content")
        if not isinstance(content, dict):
            raise HTTPException(status_code=422, detail="content must be an object")
        metadata = content.get("metadata", {})
        if not isinstance(metadata, dict):
            raise HTTPException(status_code=422, detail="content.metadata must be an object")
        forwarded = {
            **payload,
            "content": {
                **content,
                "metadata": {**metadata, "origin": principal.subject},
            },
        }
        return {"task_id": session.call("submit", forwarded)}

    @app.get("/api/v1/tasks/{task_id}")
    def task(task_id: str, _principal: ReadPrincipal) -> dict[str, Any]:
        payload: dict[str, Any] = session.call("task", task_id).model_dump(mode="json")
        return payload

    @app.get("/api/v1/tasks/{task_id}/invocations")
    def invocations(task_id: str, _principal: ReadPrincipal) -> dict[str, Any]:
        items = session.call("invocations", task_id)
        return {"task_id": task_id, "items": [item.model_dump(mode="json") for item in items]}

    @app.get("/api/v1/tasks/{task_id}/artifacts")
    def artifacts(task_id: str, _principal: ReadPrincipal) -> dict[str, Any]:
        items = session.call("artifacts", task_id)
        return {"task_id": task_id, "items": [item.model_dump(mode="json") for item in items]}

    @app.get("/api/v1/tasks/{task_id}/artifacts/{artifact_id}")
    def artifact_download(task_id: str, artifact_id: str, _principal: ReadPrincipal) -> Response:
        listed = {item.artifact_id: item for item in session.call("artifacts", task_id)}
        artifact = listed.get(artifact_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="artifact not found for this task")
        payload = session.call("download_artifact", task_id, artifact_id)
        return Response(
            content=payload,
            media_type=artifact.media_type or "application/octet-stream",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{Path(artifact.filename).name}"'
                )
            },
        )

    @app.delete("/api/v1/tasks/{task_id}")
    def cancel(task_id: str, _principal: CancelPrincipal) -> dict[str, Any]:
        payload: dict[str, Any] = session.call("cancel", task_id).model_dump(mode="json")
        return payload

    return app


def _broker_error(status_code: int) -> tuple[int, str]:
    if status_code in {400, 422}:
        return 422, "broker_contract_error"
    if status_code in {404, 409, 429}:
        names = {404: "not_found", 409: "broker_conflict", 429: "broker_rate_limited"}
        return status_code, names[status_code]
    if status_code in {401, 403}:
        return 503, "broker_auth_unavailable"
    return 502, "broker_failure"
