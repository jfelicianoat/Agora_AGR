from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

from agora.api import ApiSettings, create_api
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.cards import Card
from agora.security.keys import generate_private_key, public_jwk
from agora.security.oauth import (
    PRIVATE_KEY_JWT,
    ClientRecord,
    ClientRegistry,
    OAuthAuthority,
    sign_client_assertion,
)

ISSUER = "https://testserver"
TOKEN_ENDPOINT = f"{ISSUER}/oauth/token"


@dataclass
class OAuthFixture:
    authority: OAuthAuthority
    registry: ClientRegistry
    keys: dict[str, bytes]
    now: list[int]

    def assertion(self, client_id: str, *, token_id: str) -> str:
        return sign_client_assertion(
            client_id,
            self.keys[client_id],
            TOKEN_ENDPOINT,
            now=self.now[0],
            token_id=token_id,
        )


def _oauth(tmp_path: Path) -> OAuthFixture:
    now = [1_900_000_000]
    registry = ClientRegistry(tmp_path / "oauth-clients.json")
    keys = {"athena": generate_private_key(), "runner": generate_private_key()}
    registry.register(
        ClientRecord(
            "athena",
            public_jwk(keys["athena"]),
            ("cards:write", "cards:read"),
            ("agora-api", "agora-gateway"),
        )
    )
    registry.register(
        ClientRecord(
            "runner",
            public_jwk(keys["runner"]),
            ("cards:read", "board:claim"),
            ("agora-api",),
        )
    )
    authority = OAuthAuthority(
        ISSUER,
        TOKEN_ENDPOINT,
        registry,
        generate_private_key(),
        token_ttl_seconds=60,
        clock=lambda: now[0],
        identifier=iter(f"access-{index}" for index in range(100)).__next__,
    )
    return OAuthFixture(authority, registry, keys, now)


def _client(tmp_path: Path, fixture: OAuthFixture) -> tuple[AgoraApplication, TestClient]:
    application = AgoraApplication(tmp_path / "workspace")
    app = create_api(
        application,
        ApiSettings(oauth_authority=fixture.authority, audience="agora-api"),
    )
    return application, TestClient(app, base_url=ISSUER)


def _token(
    client: TestClient,
    fixture: OAuthFixture,
    client_id: str,
    scope: str,
    *,
    audience: str = "agora-api",
    assertion_id: str,
) -> str:
    response = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_assertion_type": PRIVATE_KEY_JWT,
            "client_assertion": fixture.assertion(client_id, token_id=assertion_id),
            "scope": scope,
            "audience": audience,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["token_type"] == "Bearer"
    assert payload["expires_in"] == 60
    access_token: str = payload["access_token"]
    return access_token


def test_oauth_client_credentials_rejects_missing_and_expired_tokens(tmp_path: Path) -> None:
    fixture = _oauth(tmp_path)
    _application, client = _client(tmp_path, fixture)

    missing = client.get("/api/v1/health")
    assert missing.status_code == 401
    assert missing.json()["detail"]["error"] == "invalid_token"

    token = _token(
        client,
        fixture,
        "athena",
        "cards:read",
        assertion_id="assertion-expiry",
    )
    fixture.now[0] += 70
    expired = client.get("/api/v1/health", headers={"Authorization": f"Bearer {token}"})
    assert expired.status_code == 401
    assert expired.json()["detail"]["error"] == "invalid_token"


def test_oauth_rejects_wrong_audience_and_insufficient_scope(tmp_path: Path) -> None:
    fixture = _oauth(tmp_path)
    _application, client = _client(tmp_path, fixture)
    wrong_audience = _token(
        client,
        fixture,
        "athena",
        "cards:read",
        audience="agora-gateway",
        assertion_id="assertion-audience",
    )

    rejected = client.get(
        "/api/v1/health", headers={"Authorization": f"Bearer {wrong_audience}"}
    )
    assert rejected.status_code == 401
    assert rejected.json()["detail"]["error"] == "invalid_token"

    read_only = _token(
        client,
        fixture,
        "athena",
        "cards:read",
        assertion_id="assertion-read-only",
    )
    insufficient = client.post(
        "/api/v1/cards",
        headers={
            "Authorization": f"Bearer {read_only}",
            "Idempotency-Key": "read-only-create",
        },
        json={"filename": "forbidden.md", "function": "transform", "request": "work"},
    )
    assert insufficient.status_code == 403
    assert insufficient.json()["detail"]["error"] == "insufficient_scope"


def test_oauth_origin_is_authenticated_identity_and_spoof_is_rejected(tmp_path: Path) -> None:
    fixture = _oauth(tmp_path)
    application, client = _client(tmp_path, fixture)
    token = _token(
        client,
        fixture,
        "athena",
        "cards:write cards:read",
        assertion_id="assertion-origin",
    )
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "origin-1"}
    spoofed = client.post(
        "/api/v1/cards",
        headers=headers,
        json={
            "filename": "spoofed.md",
            "function": "transform",
            "request": "work",
            "origin": "admin",
        },
    )
    assert spoofed.status_code == 422

    headers["Idempotency-Key"] = "origin-2"
    created = client.post(
        "/api/v1/cards",
        headers=headers,
        json={"filename": "real.md", "function": "transform", "request": "work"},
    )
    assert created.status_code == 201
    card = Card.load(application.board.directory(BoardState.PENDING) / "real.md")
    assert card.metadata["origin"] == "athena"
    assert card.metadata["origin_identity"] == "athena"


def test_revoking_one_oauth_client_does_not_affect_another(tmp_path: Path) -> None:
    fixture = _oauth(tmp_path)
    _application, client = _client(tmp_path, fixture)
    athena_token = _token(
        client,
        fixture,
        "athena",
        "cards:read",
        assertion_id="assertion-athena-revoke",
    )
    runner_token = _token(
        client,
        fixture,
        "runner",
        "cards:read",
        assertion_id="assertion-runner-stays",
    )

    fixture.registry.revoke("athena")

    assert client.get(
        "/api/v1/health", headers={"Authorization": f"Bearer {athena_token}"}
    ).status_code == 401
    assert client.get(
        "/api/v1/health", headers={"Authorization": f"Bearer {runner_token}"}
    ).status_code == 200


def test_client_key_rotation_invalidates_old_key_without_shared_secret(tmp_path: Path) -> None:
    fixture = _oauth(tmp_path)
    _application, client = _client(tmp_path, fixture)
    old_assertion = fixture.assertion("athena", token_id="assertion-before-rotation")
    new_key = generate_private_key()
    fixture.registry.rotate("athena", public_jwk(new_key))

    old = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "athena",
            "client_assertion_type": PRIVATE_KEY_JWT,
            "client_assertion": old_assertion,
            "scope": "cards:read",
            "audience": "agora-api",
        },
    )
    assert old.status_code == 401
    assert old.json()["error"] == "invalid_client"

    fixture.keys["athena"] = new_key
    renewed = _token(
        client,
        fixture,
        "athena",
        "cards:read",
        assertion_id="assertion-after-rotation",
    )
    assert renewed.count(".") == 2


def test_token_endpoint_rejects_assertion_replay_and_revocation_is_immediate(
    tmp_path: Path,
) -> None:
    fixture = _oauth(tmp_path)
    _application, client = _client(tmp_path, fixture)
    assertion = fixture.assertion("athena", token_id="single-use-assertion")
    form = {
        "grant_type": "client_credentials",
        "client_id": "athena",
        "client_assertion_type": PRIVATE_KEY_JWT,
        "client_assertion": assertion,
        "scope": "cards:read",
        "audience": "agora-api",
    }
    first = client.post("/oauth/token", data=form)
    assert first.status_code == 200
    replay = client.post("/oauth/token", data=form)
    assert replay.status_code == 401
    assert replay.json()["error"] == "invalid_client"

    revoke_assertion = fixture.assertion("athena", token_id="revoke-assertion")
    revoked = client.post(
        "/oauth/revoke",
        data={
            "token": first.json()["access_token"],
            "client_id": "athena",
            "client_assertion_type": PRIVATE_KEY_JWT,
            "client_assertion": revoke_assertion,
        },
    )
    assert revoked.status_code == 200
    after = client.get(
        "/api/v1/health",
        headers={"Authorization": f"Bearer {first.json()['access_token']}"},
    )
    assert after.status_code == 401
