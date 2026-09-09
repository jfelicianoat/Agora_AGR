"""FastAPI application factory for Agora API v1."""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, Security, status
from fastapi.responses import FileResponse, JSONResponse, Response

from agora.api.contracts import (
    CancelRequest,
    ClaimRequest,
    CloseRequest,
    CreateCardRequest,
    ProgressRequest,
    UnblockRequest,
    WorkItem,
    YieldRequest,
)
from agora.api.service import RemoteWorkService
from agora.api.projection import project
from agora.api.storage import EventStore, IdempotencyConflict, IdempotencyStore
from agora.application import AgoraApplication
from agora.contracts import ContractRegistry
from agora.board import BoardState
from agora.cards import Card
from agora.errors import AgoraError, CardFormatError, ClaimConflict, InvalidTransition
from agora.security.fastapi import ALL_AGORA_SCOPES, BearerAuthenticator, install_oauth_endpoints
from agora.security.oauth import OAuthAuthority, OAuthPrincipal


@dataclass(frozen=True, slots=True)
class ApiSettings:
    token: str | None = field(default=None, repr=False)
    principal: str = "provisional-client"
    require_https: bool = True
    oauth_authority: OAuthAuthority | None = field(default=None, repr=False)
    audience: str = "agora-api"

    def __post_init__(self) -> None:
        if self.token is not None and len(self.token) < 16:
            raise ValueError("provisional API token must contain at least 16 characters")
        if self.token is None and self.oauth_authority is None:
            raise ValueError("Agora API requires OAuth or a provisional token")
        if not self.principal.strip():
            raise ValueError("API principal must not be empty")


def create_api(application: AgoraApplication, settings: ApiSettings) -> FastAPI:
    application.initialize()
    # Una tarjeta creada por este API llega con `origin` puesto al principal
    # autenticado. Sin esto, el despachador la bloquearia por «untrusted
    # origin» despues de haber respondido 201: el cliente lo habria hecho todo
    # bien y su trabajo moriria igual.
    application.trust_origin(settings.principal)
    remote = RemoteWorkService(application)
    private_root = application.workspace / ".agora"
    idempotency = IdempotencyStore(private_root / "idempotency.json")
    events = EventStore(private_root / "events.json")
    operation_lock = Lock()
    app = FastAPI(title="Agora API", version="1.0.0", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def require_https(request: Request, call_next: Callable[..., Any]) -> Response:
        if settings.require_https and request.url.scheme != "https":
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "HTTPS is required"},
            )
        forwarded: Response = await call_next(request)
        return forwarded

    authenticate = BearerAuthenticator(
        settings.audience,
        settings.oauth_authority,
        settings.token,
        settings.principal,
        ALL_AGORA_SCOPES,
    )
    Principal = Annotated[OAuthPrincipal, Security(authenticate)]
    CardsRead = Annotated[OAuthPrincipal, Security(authenticate, scopes=["cards:read"])]
    CardsWrite = Annotated[OAuthPrincipal, Security(authenticate, scopes=["cards:write"])]
    BoardClaim = Annotated[OAuthPrincipal, Security(authenticate, scopes=["board:claim"])]
    BoardAdmin = Annotated[OAuthPrincipal, Security(authenticate, scopes=["board:admin"])]
    IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]

    if settings.oauth_authority is not None:
        install_oauth_endpoints(app, settings.oauth_authority)

    @app.exception_handler(IdempotencyConflict)
    async def idempotency_conflict(_request: Request, exc: IdempotencyConflict) -> Response:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ClaimConflict)
    @app.exception_handler(InvalidTransition)
    async def state_conflict(_request: Request, exc: Exception) -> Response:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(CardFormatError)
    async def invalid_card(_request: Request, exc: CardFormatError) -> Response:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(FileNotFoundError)
    async def missing_file(_request: Request, exc: FileNotFoundError) -> Response:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def invalid_value(_request: Request, exc: ValueError) -> Response:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/api/v1/health")
    def health(_principal: Principal) -> dict[str, str]:
        return {"status": "ok", "api": "v1", "board_owner": "agora"}

    @app.get("/api/v1/board")
    def board(_principal: CardsRead) -> dict[str, Any]:
        snapshot = application.snapshot()
        return {
            "captured_at": snapshot.captured_at,
            "cards": [asdict(card) for card in snapshot.cards],
            "errors": [asdict(error) for error in snapshot.errors],
        }

    @app.get("/api/v1/cards")
    def find_cards(
        _principal: CardsRead,
        system: Annotated[str, Query(min_length=1, max_length=120)],
        external_id: Annotated[str, Query(min_length=1, max_length=200)],
    ) -> dict[str, Any]:
        """Reconciliar: que tarjetas corresponden a este trabajo mio.

        Es lo que le permite a un cliente recuperarse de haber perdido el
        `filename` —un corte a mitad de la creacion, una base de datos que se
        restauro— sin tener que adivinar nada.

        Devuelve una **lista**: la referencia no es unica ni pretende serlo.
        Y exige `cards:read` como cualquier otra lectura: conocer una
        referencia no da acceso a nada por si solo.
        """
        return {"cards": list(remote.find_by_external_reference(system, external_id))}

    @app.get("/api/v1/cards/{state_name}/{filename}")
    def card(
        state_name: BoardState, filename: str, _principal: CardsRead
    ) -> dict[str, object]:
        return remote.card_payload(state_name, filename)

    @app.get("/api/v1/cards/{filename}")
    def card_status(filename: str, _principal: CardsRead) -> dict[str, object]:
        """El estado de una tarjeta, y si ya no va a cambiar.

        `terminal` le ahorra al cliente tener que llevar escrita la lista de
        estados finales: mientras sea `false`, sigue mirando.
        """
        state_name, payload = remote.locate(filename)
        metadata = payload.get("metadata")
        return {
            "state": state_name.value,
            **project(state_name, metadata if isinstance(metadata, dict) else {}),
            **payload,
        }

    @app.get("/api/v1/cards/{filename}/artifacts/{index}")
    def card_artifact(filename: str, index: int, _principal: CardsRead) -> FileResponse:
        path = remote.artifact_path(filename, index)
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @app.get("/api/v1/profiles")
    def profiles(_principal: CardsRead) -> dict[str, Any]:
        """Lo que un cliente necesita saber de cada capacidad.

        La respuesta se compone campo a campo a proposito. `ProfileSummary`
        lleva tambien la ruta del fichero en disco, que le sirve a la ventana de
        escritorio del propio tablero y **no** a un cliente: revela la
        estructura del PC ajeno y no le permite hacer nada. Serializar el objeto
        entero la publicaba sin querer.
        """
        snapshot = application.snapshot()
        return {
            "profiles": [
                {
                    "name": profile.name,
                    "version": profile.version,
                    "major": int(profile.version.split(".", 1)[0]),
                    "function": profile.function,
                    "description": profile.description,
                    "handles": list(profile.handles),
                    "refuses": list(profile.refuses),
                    "skills": list(profile.skills),
                    "produces": list(profile.produces),
                }
                for profile in snapshot.profiles
            ]
        }

    @app.get("/api/v1/contracts")
    def contracts(_principal: CardsRead) -> dict[str, Any]:
        """Los documentos que Agora sabe producir, con su esquema.

        Un cliente necesita esto antes de mandar la primera tarjeta: `produces`
        de cada perfil le dice **que** va a recibir, y esto le dice **con que
        forma**, para poder validarlo por su cuenta.

        Se lee del disco en cada peticion, como los PROFILE: un contrato es una
        promesa publica y corregir una no debe exigir reiniciar el tablero.
        """
        registry = ContractRegistry.discover(application.profiles_root)
        if registry is None:
            return {"contracts": []}
        return {
            "contracts": [
                {
                    "name": contract.name,
                    "version": contract.version,
                    "reference": contract.reference,
                    "description": contract.description,
                    "schema": contract.schema,
                    "example": contract.example,
                }
                for contract in registry.catalogue()
            ]
        }

    @app.get("/api/v1/events")
    def list_events(
        _principal: CardsRead,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ) -> dict[str, Any]:
        """Sondeo con cursor: la forma barata de seguir muchas tarjetas a la vez.

        Un cliente **no necesita ningun listener entrante**. Guarda `cursor`,
        vuelve cuando quiera y recibe solo lo que ha pasado desde entonces. Cada
        evento trae ya el estado y la referencia externa, asi que no hace falta
        un `GET` por tarjeta para saber que ha cambiado.

        `missed: true` significa que el cursor se quedo por detras de la ventana
        que guarda el tablero y **se perdieron eventos**. No es un error: es el
        aviso de que hay que reconciliar, por ejemplo con la busqueda por
        referencia externa. Callarlo dejaria al cliente creyendo que esta al dia.
        """
        page = events.page(after, limit)
        return {
            "events": list(page.events),
            "cursor": page.cursor,
            "oldest_available": page.oldest_available,
            "newest": page.newest,
            "missed": page.missed,
            "more": page.more,
        }

    @app.get("/api/v1/work", response_model=list[WorkItem])
    def work(
        _principal: BoardClaim,
        profiles: Annotated[list[str], Query(min_length=1)],
    ) -> tuple[WorkItem, ...]:
        return remote.work(tuple(profiles))

    @app.get("/api/v1/claims", response_model=list[WorkItem])
    def claims(
        _principal: BoardClaim,
        runner_id: Annotated[str, Query(min_length=1, max_length=120)],
    ) -> tuple[WorkItem, ...]:
        return remote.claimed(runner_id)

    @app.get("/api/v1/work/{filename}/inputs/{key}")
    def download_input(filename: str, key: str, _principal: BoardClaim) -> FileResponse:
        path = remote.input_path(filename, key)
        return FileResponse(path, filename=path.name, media_type="application/octet-stream")

    @app.post("/api/v1/cards", status_code=201)
    def create_card(
        request: CreateCardRequest,
        authenticated: CardsWrite,
        idempotency_key: IdempotencyKey = None,
    ) -> JSONResponse:
        key = _required_key(idempotency_key)
        body = request.model_dump(mode="json")
        digest = idempotency.digest(body)
        with operation_lock:
            cached = idempotency.lookup("create", key, digest)
            if cached:
                return _replay(cached.status_code, cached.payload)
            path = remote.create_card(request, principal=authenticated.subject)
            payload = {"filename": path.name, "state": BoardState.PENDING.value, "replayed": False}
            idempotency.save("create", key, digest, status_code=201, payload=payload)
            events.emit(
                "card.created",
                {
                    "filename": path.name,
                    "state": BoardState.PENDING.value,
                    "status": "queued",
                    "terminal": BoardState.PENDING.is_terminal,
                    "principal": authenticated.subject,
                    **_reference_of(path),
                },
            )
            return JSONResponse(status_code=201, content=payload)

    @app.post("/api/v1/cards/{filename}/claim")
    def claim(
        filename: str,
        request: ClaimRequest,
        _principal: BoardClaim,
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
        _principal: BoardClaim,
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
        _principal: BoardClaim,
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
        _principal: BoardClaim,
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

    @app.post("/api/v1/cards/{filename}/cancel")
    def cancel_card(
        filename: str,
        request: CancelRequest,
        authenticated: BoardAdmin,
        idempotency_key: IdempotencyKey = None,
    ) -> JSONResponse:
        return _mutation(
            "cancel",
            filename,
            request.model_dump(mode="json"),
            idempotency_key,
            operation_lock,
            idempotency,
            events,
            lambda: remote.cancel(
                filename, principal=authenticated.subject, reason=request.reason
            ),
            BoardState.ARCHIVE,
            "card.cancelled",
        )

    @app.post("/api/v1/admin/blocked/{filename}/unblock")
    def unblock(
        filename: str,
        request: UnblockRequest,
        authenticated: BoardAdmin,
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
            lambda: remote.unblock(
                filename, principal=authenticated.subject, reason=request.reason
            ),
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
        payload = {
            "filename": path.name,
            "state": _reached_state(path, destination),
            "replayed": False,
        }
        idempotency.save(scope, key, digest, status_code=200, payload=payload)
        reached = BoardState(payload["state"])
        events.emit(
            event_kind,
            {
                "filename": path.name,
                "state": reached.value,
                **project(reached, _metadata_of(path)),
                **_reference_of(path),
            },
        )
        return JSONResponse(status_code=200, content=payload)


def _metadata_of(path: Path) -> dict[str, Any]:
    """La metadata de la CARD, o nada si no se puede leer.

    Un fichero ilegible no puede tumbar el evento: lo que se estaba haciendo con
    la tarjeta ya ocurrio, y callar el evento seria perder el hecho.
    """
    try:
        return dict(Card.load(path).metadata)
    except (AgoraError, OSError):
        return {}


def _reference_of(path: Path) -> dict[str, Any]:
    """La referencia externa de la CARD, si la lleva.

    Va en el evento para que un cliente pueda atarlo con su propio trabajo sin
    tener que leer la tarjeta. Una CARD ilegible no puede tumbar el evento: lo
    que se estaba haciendo con ella ya ocurrio.
    """
    try:
        reference = Card.load(path).metadata.get("external_reference")
    except (AgoraError, OSError):
        return {}
    return {"external_reference": reference} if isinstance(reference, dict) else {}


def _reached_state(path: Path, expected: BoardState) -> str:
    """El estado real donde quedó la CARD.

    Una transición puede acabar en otro sitio del previsto: agotar `max_attempts`
    al ceder una tarjeta la manda a `blocked`, no a `pending`. Devolver el destino
    esperado haría que el runner (y el registro de idempotencia) guardaran una
    mentira sobre el tablero.
    """
    reached = path.parent.name
    if reached in {state.value for state in BoardState}:
        return reached
    return expected.value
