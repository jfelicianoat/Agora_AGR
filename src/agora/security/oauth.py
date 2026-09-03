"""Small OAuth 2.0 client-credentials authority using Authlib JOSE primitives.

The supported client authentication method is private_key_jwt (RFC 7523). Access
tokens follow the JWT access-token profile (RFC 9068) and are deliberately short-lived.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from authlib.jose import JoseError, JsonWebToken

from agora.documents import atomic_write_text
from agora.security.keys import public_jwk

PRIVATE_KEY_JWT = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
_JWT = JsonWebToken(["RS256"])


class OAuthProtocolError(RuntimeError):
    def __init__(self, error: str, description: str, *, status_code: int = 400) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class OAuthPrincipal:
    subject: str
    client_id: str
    scopes: frozenset[str]
    audience: str
    token_id: str

    def require(self, scopes: Iterable[str]) -> None:
        missing = sorted(set(scopes) - self.scopes)
        if missing:
            raise OAuthProtocolError(
                "insufficient_scope",
                "Required scope is missing: " + " ".join(missing),
                status_code=403,
            )


@dataclass(frozen=True, slots=True)
class ClientRecord:
    client_id: str
    public_jwk: dict[str, str]
    scopes: tuple[str, ...]
    audiences: tuple[str, ...]
    active: bool = True
    version: int = 1

    def __post_init__(self) -> None:
        if not self.client_id.strip():
            raise ValueError("client_id must not be empty")
        if self.public_jwk.get("kty") != "RSA":
            raise ValueError("private_key_jwt clients require an RSA public JWK")
        if not self.scopes or any(not item.strip() for item in self.scopes):
            raise ValueError("OAuth client scopes must not be empty")
        if not self.audiences or any(not item.strip() for item in self.audiences):
            raise ValueError("OAuth client audiences must not be empty")


@dataclass(slots=True)
class ClientRegistry:
    path: Path | None = None
    _records: dict[str, ClientRecord] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.path is not None and self.path.is_file():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            records = payload.get("clients", []) if isinstance(payload, dict) else []
            self._records = {
                item["client_id"]: ClientRecord(
                    client_id=item["client_id"],
                    public_jwk=dict(item["public_jwk"]),
                    scopes=tuple(item["scopes"]),
                    audiences=tuple(item["audiences"]),
                    active=bool(item.get("active", True)),
                    version=int(item.get("version", 1)),
                )
                for item in records
            }

    def get(self, client_id: str) -> ClientRecord | None:
        with self._lock:
            return self._records.get(client_id)

    def register(self, record: ClientRecord) -> None:
        with self._lock:
            if record.client_id in self._records:
                raise ValueError(f"OAuth client already exists: {record.client_id}")
            self._records[record.client_id] = record
            self._save()

    def revoke(self, client_id: str) -> ClientRecord:
        with self._lock:
            current = self._required(client_id)
            updated = ClientRecord(
                **{**asdict(current), "active": False, "version": current.version + 1}
            )
            self._records[client_id] = updated
            self._save()
            return updated

    def rotate(self, client_id: str, new_public_jwk: dict[str, str]) -> ClientRecord:
        with self._lock:
            current = self._required(client_id)
            updated = ClientRecord(
                **{
                    **asdict(current),
                    "public_jwk": new_public_jwk,
                    "active": True,
                    "version": current.version + 1,
                }
            )
            self._records[client_id] = updated
            self._save()
            return updated

    def _required(self, client_id: str) -> ClientRecord:
        record = self._records.get(client_id)
        if record is None:
            raise KeyError(client_id)
        return record

    def _save(self) -> None:
        if self.path is None:
            return
        payload = {
            "version": 1,
            "clients": [asdict(self._records[key]) for key in sorted(self._records)],
        }
        atomic_write_text(self.path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


@dataclass(slots=True)
class OAuthAuthority:
    issuer: str
    token_endpoint: str
    registry: ClientRegistry
    signing_private_key: bytes = field(repr=False)
    token_ttl_seconds: int = 300
    clock: Callable[[], float] = time.time
    identifier: Callable[[], str] = lambda: str(uuid4())
    _signing_keys: dict[str, dict[str, str]] = field(default_factory=dict, init=False, repr=False)
    _current_kid: str = field(default="", init=False, repr=False)
    _assertion_jti: OrderedDict[str, int] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    _revoked_jti: OrderedDict[str, int] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.issuer.startswith("https://") or not self.token_endpoint.startswith("https://"):
            raise ValueError("OAuth issuer and token endpoint must use HTTPS")
        if not 30 <= self.token_ttl_seconds <= 900:
            raise ValueError("OAuth access-token lifetime must be between 30 and 900 seconds")
        self.rotate_signing_key(self.signing_private_key)

    @property
    def jwks(self) -> dict[str, list[dict[str, str]]]:
        return {"keys": list(self._signing_keys.values())}

    def metadata(self) -> dict[str, Any]:
        base = self.issuer.rstrip("/")
        return {
            "issuer": self.issuer,
            "token_endpoint": self.token_endpoint,
            "revocation_endpoint": f"{base}/oauth/revoke",
            "jwks_uri": f"{base}/.well-known/jwks.json",
            "grant_types_supported": ["client_credentials"],
            "token_endpoint_auth_methods_supported": ["private_key_jwt"],
            "token_endpoint_auth_signing_alg_values_supported": ["RS256"],
        }

    def rotate_signing_key(self, private_key: bytes) -> str:
        key = public_jwk(private_key)
        kid = key["kid"]
        with self._lock:
            self.signing_private_key = private_key
            self._signing_keys[kid] = key
            self._current_kid = kid
        return kid

    def retire_signing_key(self, kid: str) -> None:
        with self._lock:
            if kid == self._current_kid:
                raise ValueError("cannot retire the active OAuth signing key")
            self._signing_keys.pop(kid, None)

    def issue(
        self,
        *,
        grant_type: str,
        client_id: str,
        client_assertion_type: str,
        client_assertion: str,
        scope: str,
        audience: str,
    ) -> dict[str, Any]:
        if grant_type != "client_credentials":
            raise OAuthProtocolError(
                "unsupported_grant_type", "Only client_credentials is supported"
            )
        record = self._authenticate_client(
            client_id, client_assertion_type, client_assertion
        )
        requested = frozenset(item for item in scope.split() if item)
        if not requested:
            raise OAuthProtocolError("invalid_scope", "At least one scope is required")
        if not requested.issubset(record.scopes):
            raise OAuthProtocolError("invalid_scope", "Requested scope is not granted to client")
        if audience not in record.audiences:
            raise OAuthProtocolError(
                "invalid_target", "Requested audience is not granted to client"
            )
        now = int(self.clock())
        token_id = self.identifier()
        claims = {
            "iss": self.issuer,
            "sub": record.client_id,
            "aud": audience,
            "exp": now + self.token_ttl_seconds,
            "iat": now,
            "jti": token_id,
            "client_id": record.client_id,
            "scope": " ".join(sorted(requested)),
            "client_version": record.version,
        }
        encoded = _JWT.encode(
            {"alg": "RS256", "typ": "at+jwt", "kid": self._current_kid},
            claims,
            self.signing_private_key,
        )
        return {
            "access_token": encoded.decode("ascii"),
            "token_type": "Bearer",
            "expires_in": self.token_ttl_seconds,
            "scope": claims["scope"],
        }

    def authenticate(self, token: str, *, audience: str) -> OAuthPrincipal:
        now = int(self.clock())
        self._prune(now)
        try:
            claims = _JWT.decode(
                token,
                self.jwks,
                claims_options={
                    "iss": {"essential": True, "value": self.issuer},
                    "sub": {"essential": True},
                    "aud": {"essential": True, "value": audience},
                    "exp": {"essential": True},
                    "iat": {"essential": True},
                    "jti": {"essential": True},
                    "client_id": {"essential": True},
                    "scope": {"essential": True},
                },
            )
            claims.validate(now=now, leeway=5)
        except JoseError as exc:
            raise OAuthProtocolError(
                "invalid_token", "Access token is invalid or expired", status_code=401
            ) from exc
        payload = dict(claims)
        client_id = str(payload.get("client_id", ""))
        if payload.get("sub") != client_id:
            raise OAuthProtocolError("invalid_token", "Token subject mismatch", status_code=401)
        record = self.registry.get(client_id)
        if record is None or not record.active or payload.get("client_version") != record.version:
            raise OAuthProtocolError("invalid_token", "OAuth client was revoked", status_code=401)
        token_id = str(payload["jti"])
        if token_id in self._revoked_jti:
            raise OAuthProtocolError("invalid_token", "Access token was revoked", status_code=401)
        scopes = frozenset(str(payload["scope"]).split())
        if not scopes.issubset(record.scopes):
            raise OAuthProtocolError(
                "invalid_token", "Token scope exceeds client grant", status_code=401
            )
        return OAuthPrincipal(
            subject=client_id,
            client_id=client_id,
            scopes=scopes,
            audience=audience,
            token_id=token_id,
        )

    def revoke_token(
        self,
        *,
        token: str,
        client_id: str,
        client_assertion_type: str,
        client_assertion: str,
    ) -> None:
        record = self._authenticate_client(client_id, client_assertion_type, client_assertion)
        try:
            claims = _JWT.decode(token, self.jwks)
            payload = dict(claims)
        except JoseError:
            return
        if payload.get("client_id") != record.client_id:
            return
        token_id = payload.get("jti")
        expiry = payload.get("exp")
        if isinstance(token_id, str) and isinstance(expiry, int):
            self._revoked_jti[token_id] = expiry

    def _authenticate_client(
        self, client_id: str, assertion_type: str, assertion: str
    ) -> ClientRecord:
        if assertion_type != PRIVATE_KEY_JWT or not assertion:
            raise OAuthProtocolError(
                "invalid_client",
                "private_key_jwt client authentication is required",
                status_code=401,
            )
        record = self.registry.get(client_id)
        if record is None or not record.active:
            raise OAuthProtocolError("invalid_client", "Unknown or revoked client", status_code=401)
        now = int(self.clock())
        self._prune(now)
        try:
            claims = _JWT.decode(
                assertion,
                record.public_jwk,
                claims_options={
                    "iss": {"essential": True, "value": client_id},
                    "sub": {"essential": True, "value": client_id},
                    "aud": {"essential": True, "value": self.token_endpoint},
                    "exp": {"essential": True},
                    "iat": {"essential": True},
                    "jti": {"essential": True},
                },
            )
            claims.validate(now=now, leeway=5)
        except JoseError as exc:
            raise OAuthProtocolError(
                "invalid_client", "Client assertion is invalid or expired", status_code=401
            ) from exc
        payload = dict(claims)
        issued = payload.get("iat")
        expiry = payload.get("exp")
        token_id = payload.get("jti")
        if (
            not isinstance(issued, int)
            or not isinstance(expiry, int)
            or expiry - issued > 300
            or issued > now + 5
            or not isinstance(token_id, str)
            or not token_id
        ):
            raise OAuthProtocolError(
                "invalid_client", "Client assertion lifetime is invalid", status_code=401
            )
        if token_id in self._assertion_jti:
            raise OAuthProtocolError(
                "invalid_client", "Client assertion was already used", status_code=401
            )
        self._assertion_jti[token_id] = expiry
        return record

    def _prune(self, now: int) -> None:
        for store in (self._assertion_jti, self._revoked_jti):
            expired = [key for key, expiry in store.items() if expiry < now]
            for key in expired:
                store.pop(key, None)


def sign_client_assertion(
    client_id: str,
    private_key: bytes,
    token_endpoint: str,
    *,
    now: int | None = None,
    token_id: str | None = None,
) -> str:
    """Client-side test/CLI helper; private keys never enter the server registry."""
    issued = int(time.time()) if now is None else now
    key = public_jwk(private_key)
    encoded = _JWT.encode(
        {"alg": "RS256", "typ": "JWT", "kid": key["kid"]},
        {
            "iss": client_id,
            "sub": client_id,
            "aud": token_endpoint,
            "iat": issued,
            "exp": issued + 60,
            "jti": token_id or str(uuid4()),
        },
        private_key,
    )
    ascii_token: str = encoded.decode("ascii")
    return ascii_token
