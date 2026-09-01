"""FastAPI transport bindings for the OAuth authority and resource servers."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import SecurityScopes

from agora.security.oauth import OAuthAuthority, OAuthPrincipal, OAuthProtocolError

ALL_AGORA_SCOPES = frozenset({"cards:write", "cards:read", "board:claim", "board:admin"})
ALL_GATEWAY_SCOPES = frozenset({"gateway:read", "gateway:submit", "gateway:cancel"})


@dataclass(slots=True, eq=False)
class BearerAuthenticator:
    audience: str
    authority: OAuthAuthority | None = None
    legacy_token: str | None = None
    legacy_principal: str = "provisional-client"
    legacy_scopes: frozenset[str] = ALL_AGORA_SCOPES

    def __call__(
        self,
        security_scopes: SecurityScopes,
        authorization: Annotated[str | None, Header()] = None,
    ) -> OAuthPrincipal:
        scheme, _, candidate = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not candidate:
            _auth_error("invalid_token", "Bearer access token is required", 401)
        if self.legacy_token is not None and hmac.compare_digest(candidate, self.legacy_token):
            principal = OAuthPrincipal(
                subject=self.legacy_principal,
                client_id=self.legacy_principal,
                scopes=self.legacy_scopes,
                audience=self.audience,
                token_id="legacy",
            )
        elif self.authority is not None:
            try:
                principal = self.authority.authenticate(candidate, audience=self.audience)
            except OAuthProtocolError as exc:
                _auth_error(exc.error, exc.description, exc.status_code)
        else:
            _auth_error("invalid_token", "Bearer access token is invalid", 401)
        try:
            principal.require(security_scopes.scopes)
        except OAuthProtocolError as exc:
            _auth_error(exc.error, exc.description, exc.status_code)
        return principal


def install_oauth_endpoints(app: FastAPI, authority: OAuthAuthority) -> None:
    @app.get("/.well-known/oauth-authorization-server")
    def oauth_metadata() -> dict[str, Any]:
        return authority.metadata()

    @app.get("/.well-known/jwks.json")
    def oauth_jwks() -> dict[str, Any]:
        return authority.jwks

    @app.post("/oauth/token")
    async def oauth_token(request: Request) -> JSONResponse:
        fields = await _form(request)
        try:
            response = authority.issue(
                grant_type=fields.get("grant_type", ""),
                client_id=fields.get("client_id", ""),
                client_assertion_type=fields.get("client_assertion_type", ""),
                client_assertion=fields.get("client_assertion", ""),
                scope=fields.get("scope", ""),
                audience=fields.get("audience") or fields.get("resource", ""),
            )
        except OAuthProtocolError as exc:
            return _oauth_response(exc)
        return JSONResponse(response, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    @app.post("/oauth/revoke")
    async def oauth_revoke(request: Request) -> JSONResponse:
        fields = await _form(request)
        try:
            authority.revoke_token(
                token=fields.get("token", ""),
                client_id=fields.get("client_id", ""),
                client_assertion_type=fields.get("client_assertion_type", ""),
                client_assertion=fields.get("client_assertion", ""),
            )
        except OAuthProtocolError as exc:
            return _oauth_response(exc)
        return JSONResponse({}, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


async def _form(request: Request) -> dict[str, str]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=415, detail={"error": "invalid_request"})
    body = await request.body()
    if len(body) > 64_000:
        raise HTTPException(status_code=413, detail={"error": "invalid_request"})
    try:
        values = parse_qs(body.decode("ascii"), strict_parsing=True)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_request"}) from exc
    if any(len(items) != 1 for items in values.values()):
        raise HTTPException(status_code=400, detail={"error": "invalid_request"})
    return {key: items[0] for key, items in values.items()}


def _oauth_response(exc: OAuthProtocolError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "error_description": exc.description},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _auth_error(error: str, description: str, status_code: int) -> None:
    headers = {"WWW-Authenticate": f'Bearer error="{error}"'}
    raise HTTPException(
        status_code=status_code,
        detail={"error": error, "error_description": description},
        headers=headers,
    )
