from __future__ import annotations

import inspect
from pathlib import Path
from typing import NoReturn

from fastapi.testclient import TestClient

from agora.broker.client import BrokerApiError, BrokerClient
from agora.broker.contracts import BrokerPolicy
from agora.broker.executor import BrokerExecutor
from agora.cards import Card
from agora.gateway import BrokerSession, GatewayClient, GatewaySettings, create_gateway
from agora.gateway import app as gateway_app_module
from agora.security.keys import generate_private_key, public_jwk
from agora.security.oauth import (
    PRIVATE_KEY_JWT,
    ClientRecord,
    ClientRegistry,
    OAuthAuthority,
    sign_client_assertion,
)
from conftest import write_profile
from test_broker import FakeBroker, _broker_client, _work

ISSUER = "https://testserver"


def _gateway(
    tmp_path: Path, fake: FakeBroker | None = None
) -> tuple[FakeBroker, BrokerSession, TestClient, dict[str, str], str]:
    now = 1_900_000_000
    key = generate_private_key()
    registry = ClientRegistry(tmp_path / "clients.json")
    registry.register(
        ClientRecord(
            "agora-worker",
            public_jwk(key),
            ("gateway:read", "gateway:submit", "gateway:cancel"),
            ("agora-gateway",),
        )
    )
    authority = OAuthAuthority(
        ISSUER,
        f"{ISSUER}/oauth/token",
        registry,
        generate_private_key(),
        clock=lambda: now,
    )
    assertion = sign_client_assertion(
        "agora-worker", key, f"{ISSUER}/oauth/token", now=now, token_id="gateway-assertion"
    )
    token = authority.issue(
        grant_type="client_credentials",
        client_id="agora-worker",
        client_assertion_type=PRIVATE_KEY_JWT,
        client_assertion=assertion,
        scope="gateway:read gateway:submit gateway:cancel",
        audience="agora-gateway",
    )["access_token"]
    broker = fake or FakeBroker()
    session = BrokerSession(_broker_client(broker))
    api = create_gateway(session, GatewaySettings(authority, require_https=True))
    client = TestClient(api, base_url=ISSUER)
    headers = {"Authorization": f"Bearer {token}"}
    return broker, session, client, headers, token


def _payload() -> dict[str, object]:
    return {
        "idempotency_key": "gateway-one",
        "content": {"prompt": "Do the work", "metadata": {"origin": "spoofed"}},
    }


def test_gateway_requires_oauth_scope_and_never_exposes_admin_token(tmp_path: Path) -> None:
    broker, _session, client, headers, token = _gateway(tmp_path)

    assert client.get("/api/v1/health").status_code == 401
    response = client.get("/api/v1/capabilities", headers=headers)

    assert response.status_code == 200
    assert broker.token not in response.text
    assert broker.token not in token
    assert "X-Admin-Token" not in inspect.getsource(GatewayClient)


def test_gateway_rejects_top_level_origin_and_overwrites_nested_spoof(tmp_path: Path) -> None:
    broker, _session, client, headers, _token = _gateway(tmp_path)

    top_level = {**_payload(), "origin": "administrator"}
    assert client.post("/api/v1/tasks", headers=headers, json=top_level).status_code == 422
    accepted = client.post("/api/v1/tasks", headers=headers, json=_payload())

    assert accepted.status_code == 202
    content = broker.submissions[0]["content"]
    assert isinstance(content, dict)
    assert content["metadata"]["origin"] == "agora-worker"


def test_gateway_renews_dynamic_broker_token_once_after_restart(tmp_path: Path) -> None:
    broker, session, client, headers, _token = _gateway(tmp_path)
    old = session.client
    broker.token = "rotated-broker-token-0123456789"
    renewals: list[str] = []

    def renew() -> BrokerClient:
        renewals.append("renewed")
        return _broker_client(broker)

    session.renew = renew
    response = client.post("/api/v1/tasks", headers=headers, json=_payload())

    assert response.status_code == 202
    assert renewals == ["renewed"]
    assert session.client is not old
    assert broker.token not in response.text


class _BrokenBroker:
    def __init__(self, code: int) -> None:
        self.code = code

    def capabilities(self) -> NoReturn:
        raise BrokerApiError(self.code, "safe broker detail")

    def close(self) -> None:
        pass


def test_gateway_maps_broker_errors_semantically(tmp_path: Path) -> None:
    _broker, session, client, headers, _token = _gateway(tmp_path)
    expectations = {
        422: (422, "broker_contract_error"),
        404: (404, "not_found"),
        409: (409, "broker_conflict"),
        429: (429, "broker_rate_limited"),
        403: (503, "broker_auth_unavailable"),
        500: (502, "broker_failure"),
    }
    for source, expected in expectations.items():
        session.client = _BrokenBroker(source)  # type: ignore[assignment]
        response = client.get("/api/v1/capabilities", headers=headers)
        assert (response.status_code, response.json()["error"]) == expected


def test_gateway_is_board_independent_and_has_no_second_queue(tmp_path: Path) -> None:
    broker, _session, client, headers, _token = _gateway(tmp_path)
    source = inspect.getsource(gateway_app_module)

    assert "AgoraApplication" not in source
    assert "from agora.board" not in source
    assert "from agora.cards" not in source
    assert "queue" not in source.lower().replace('"queue_owner"', "")
    response = client.post("/api/v1/tasks", headers=headers, json=_payload())
    assert response.status_code == 202
    assert len(broker.submissions) == 1


def test_broker_executor_can_use_gateway_adapter_without_recursive_submission(
    tmp_path: Path,
) -> None:
    broker, _session, api_client, _headers, token = _gateway(tmp_path)
    gateway = GatewayClient(
        ISSUER,
        access_token=lambda: token,
        transport=api_client._transport,
        sleep=lambda _seconds: None,
    )
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    card = Card.create(function="transform", request="summarize", origin="agora")
    executor = BrokerExecutor(
        gateway, tmp_path / "AGENTS", BrokerPolicy()  # type: ignore[arg-type]
    )

    result = executor.execute(_work(card), (), checkpoint=lambda _task, _key: None)

    assert result.task_id == broker.task_id
    assert len(broker.submissions) == 1
    forwarded = broker.submissions[0]["content"]
    assert isinstance(forwarded, dict)
    assert forwarded["metadata"]["origin"] == "agora-worker"
