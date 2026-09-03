"""Contrato 2.10 de AI_Broker: lo que Agora puede demostrar tras la respuesta del broker.

Cada prueba corresponde a un punto de `docs/RESPUESTA_A_AGORA.md` y está cotejada
contra el broker vivo (192.168.1.52:8765, contrato 2.10) el 2026-09-03. La
evidencia en vivo está en `docs/F3b_ACCEPTANCE_REPORT.md`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agora.board import BoardState
from agora.broker.contracts import (
    BrokerContractUnsupported,
    BrokerInvocation,
    BrokerPolicy,
    validate_capabilities,
)
from agora.broker.credentials import (
    BrokerConnection,
    KeyringSessionToken,
    SessionTokenUnavailable,
)
from agora.broker.executor import _contract_invocations
from agora.broker.request_builder import build_broker_request
from agora.cards import Card
from agora.profiles import Profile
from conftest import create_card
from test_ai_runner import _runner
from test_broker import DELIVERABLE, FakeBroker


def _invocation(role: str, *, contractual: bool | None, cost: float) -> BrokerInvocation:
    return BrokerInvocation(
        invocation_id=f"inv_{role}",
        role=role,
        contractual=contractual,
        model={"provider": "ollama", "deployment": "local", "model": "gemma4:12b"},
        status="completed",
        cost_usd=cost,
    )


# --- 1. `contractual` en vez de la lista negra de roles -----------------------


def test_contractual_flag_replaces_the_role_blacklist() -> None:
    """El broker decide qué es tuyo; un rol nuevo ya no rompe la separación."""
    invocations = (
        _invocation("single", contractual=True, cost=0.25),
        _invocation("shadow_probe", contractual=False, cost=0.75),
        _invocation("confidence_judge", contractual=True, cost=0.50),
        # Un rol que Agora no ha visto nunca: la lista negra lo habría contado mal.
        _invocation("refiner", contractual=True, cost=0.10),
    )

    assert [item.role for item in _contract_invocations(invocations)] == [
        "single",
        "confidence_judge",
        "refiner",
    ]


def test_confidence_judge_is_billed_to_the_card() -> None:
    """Corrección del broker: apartarlo infravaloraba el coste de la tarjeta."""
    invocations = (
        _invocation("single", contractual=True, cost=0.25),
        _invocation("confidence_judge", contractual=True, cost=0.50),
    )

    assert sum(item.cost_usd for item in _contract_invocations(invocations)) == pytest.approx(0.75)


def test_pre_210_broker_still_separates_the_shadow_probe_by_name() -> None:
    """Sin `contractual`, la reserva excluye el sondeo y sólo el sondeo."""
    invocations = (
        _invocation("single", contractual=None, cost=0.25),
        _invocation("shadow_probe", contractual=None, cost=0.75),
        _invocation("confidence_judge", contractual=None, cost=0.50),
    )

    assert [item.role for item in _contract_invocations(invocations)] == [
        "single",
        "confidence_judge",
    ]


# --- 2. Invocaciones auxiliares ----------------------------------------------


def _capabilities(version: str = "2.10", **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_version": version,
        "prompt_compression_override": True,
        "invocation_telemetry": True,
        "generation_determinism": True,
        "execution_fingerprint": True,
    }
    if version == "2.10":
        payload.update(
            {
                "invocation_contract": True,
                "prompt_compression_echo": True,
                "canonical_artifacts": True,
                "auxiliary_invocations": True,
                "auxiliary_invocations_optout": True,
            }
        )
    payload.update(extra)
    return payload


def _demo_request(policy: BrokerPolicy, contract: object) -> dict[str, object]:
    root = Path(__file__).resolve().parents[1] / "examples" / "f0-demo"
    profile = Profile.load(root / "AGENTS" / "summarizer" / "PROFILE.md")
    card = Card.load(root / "CARD.md")
    return build_broker_request(
        profile,
        (),
        card,
        policy=policy,
        idempotency_key="agora:test",
        contract=contract,  # type: ignore[arg-type]
    )


def test_confidential_card_asks_for_content_exclusivity() -> None:
    """Contenido confidencial no tolera que otro modelo lo vea, aunque sea local."""
    policy = BrokerPolicy(
        determinism="routed",
        data_classification="confidential",
        timeout_seconds=600,
        zombie_timeout_seconds=900,
    )
    contract = validate_capabilities(_capabilities())

    assert policy.auxiliary_invocations_allowed is False
    assert _demo_request(policy, contract)["auxiliary_invocations"] is False


def test_ordinary_card_does_not_send_the_optout() -> None:
    """El campo sólo viaja cuando hace falta: en 2.9 un campo desconocido es 422."""
    policy = BrokerPolicy(timeout_seconds=600, zombie_timeout_seconds=900)
    contract = validate_capabilities(_capabilities())

    assert "auxiliary_invocations" not in _demo_request(policy, contract)


def test_strict_card_relies_on_the_implicit_guarantee() -> None:
    """`target_model` + `fallback_allowed: false` ya apaga el sondeo: nada que enviar."""
    policy = BrokerPolicy(
        determinism="strict",
        target_model={"provider": "ollama", "deployment": "local", "model": "gemma4:12b"},
        timeout_seconds=600,
        zombie_timeout_seconds=900,
    )
    request = _demo_request(policy, validate_capabilities(_capabilities()))

    requirements = request["model_requirements"]
    assert "auxiliary_invocations" not in request
    assert isinstance(requirements, dict)
    assert requirements["fallback_allowed"] is False


def test_strict_card_is_rejected_before_reaching_a_29_broker() -> None:
    """Un broker anterior sondea bajo tu task_id: se rechaza aquí, no al leer telemetría."""
    policy = BrokerPolicy(
        determinism="strict",
        target_model={"provider": "ollama", "deployment": "local", "model": "gemma4:12b"},
        timeout_seconds=600,
        zombie_timeout_seconds=900,
    )

    with pytest.raises(BrokerContractUnsupported, match=r"2\.10"):
        validate_capabilities(_capabilities("2.9")).ensure_supports(policy)


def test_exclusive_card_is_rejected_when_the_broker_refuses_the_optout() -> None:
    policy = BrokerPolicy(
        determinism="routed",
        data_classification="local_only",
        timeout_seconds=600,
        zombie_timeout_seconds=900,
    )
    contract = validate_capabilities(_capabilities(auxiliary_invocations_optout=False))

    with pytest.raises(BrokerContractUnsupported, match="opt-out"):
        contract.ensure_supports(policy)


def test_nothing_to_switch_off_when_the_operator_disabled_probing() -> None:
    policy = BrokerPolicy(
        determinism="routed",
        data_classification="confidential",
        timeout_seconds=600,
        zombie_timeout_seconds=900,
    )
    contract = validate_capabilities(
        _capabilities(auxiliary_invocations=False, auxiliary_invocations_optout=False)
    )

    contract.ensure_supports(policy)


def test_card_that_cannot_be_honoured_is_returned_without_burning_an_attempt(
    tmp_path: Path,
) -> None:
    fake = FakeBroker(contract_version="2.9")
    strict = BrokerPolicy(
        determinism="strict",
        target_model=fake.served_by,
        timeout_seconds=2,
        zombie_timeout_seconds=3,
    )
    application, runner = _runner(tmp_path, fake, policy=strict)
    create_card(application.board)

    outcome = runner.run_once()

    assert outcome.status == "contract_unsupported"
    pending = Card.load(application.board.directory(BoardState.PENDING) / "task.md")
    assert pending.attempts == 0
    assert not fake.submissions


# --- 3. Eco de la compresión de prompt ---------------------------------------


def test_prompt_compression_is_proven_not_assumed(tmp_path: Path) -> None:
    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)

    assert runner.run_once().status == "completed"

    done = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    assert done.metadata["execution"]["prompt_compression"] == {
        "requested": "off",
        "verdict": "verified",
        "effective": ["off"],
    }


def test_compressed_prompt_fails_the_card(tmp_path: Path) -> None:
    """Pedir `off` y recibir `medium` invalida el trabajo atómico."""
    fake = FakeBroker()
    fake.invocations[0]["prompt_compression"] = {"requested": "off", "effective": "medium"}
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)

    outcome = runner.run_once()

    assert outcome.status == "failed"
    assert "compressed" in outcome.detail
    assert not (application.board.directory(BoardState.DONE) / "task.md").is_file()


def test_pre_210_broker_cannot_prove_compression(tmp_path: Path) -> None:
    """Sin eco no se afirma que se cumplió: se dice que no se puede verificar."""
    fake = FakeBroker(contract_version="2.9")
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)

    assert runner.run_once().status == "completed"

    done = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    assert done.metadata["execution"]["prompt_compression"]["verdict"] == "unverifiable"


# --- 4. `/artifacts` como vía canónica ---------------------------------------


def test_card_closes_with_the_final_artifact(tmp_path: Path) -> None:
    fake = FakeBroker()
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)

    assert runner.run_once().status == "completed"

    stored = tmp_path / "artifacts" / "remote" / "task"
    assert (stored / "respuesta.md").read_bytes() == DELIVERABLE
    # El nombre lo pone el broker, no un `broker-result.md` inventado por Agora.
    assert not (stored / "broker-result.md").exists()
    done = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    deliverable = done.metadata["execution"]["deliverable"]
    assert deliverable["source"] == "artifacts"
    assert deliverable["sha256"] == hashlib.sha256(DELIVERABLE).hexdigest()


def test_companion_artifacts_are_not_lost(tmp_path: Path) -> None:
    """Una imagen o la salida de `run_code` acompaña al entregable; antes se perdía."""
    fake = FakeBroker()
    image = b"\x89PNG\r\n\x1a\nfake"
    fake.artifact_bodies["art-image"] = image
    fake.artifacts.append(
        {
            "artifact_id": "art-image",
            "artifact_type": "image_output",
            "filename": "figura.png",
            "media_type": "image/png",
            "size_bytes": len(image),
            "sha256": hashlib.sha256(image).hexdigest(),
            "download_url": f"/api/v1/tasks/{fake.task_id}/artifacts/art-image",
            "available": True,
            "final": False,
        }
    )
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)

    assert runner.run_once().status == "completed"

    stored = tmp_path / "artifacts" / "remote" / "task"
    assert (stored / "respuesta.md").read_bytes() == DELIVERABLE
    assert (stored / "figura.png").read_bytes() == image


def test_first_artifact_is_not_mistaken_for_the_deliverable(tmp_path: Path) -> None:
    """Cerrar con «el primero de la lista» cerraría la tarjeta con el PNG."""
    fake = FakeBroker()
    image = b"\x89PNG\r\n\x1a\nfake"
    fake.artifact_bodies["art-image"] = image
    fake.artifacts.insert(
        0,
        {
            "artifact_id": "art-image",
            "artifact_type": "image_output",
            "filename": "figura.png",
            "media_type": "image/png",
            "sha256": hashlib.sha256(image).hexdigest(),
            "available": True,
            "final": False,
        },
    )
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)

    assert runner.run_once().status == "completed"

    done = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    assert done.metadata["execution"]["deliverable"]["name"] == "respuesta.md"


def test_tampered_artifact_is_refused(tmp_path: Path) -> None:
    """El sha256 cierra sobre los bytes del modelo: si no cuadra, no se cierra."""
    fake = FakeBroker()
    fake.artifact_bodies["art-final"] = b"otra cosa"
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)

    outcome = runner.run_once()

    assert outcome.status == "failed"
    assert "sha256 mismatch" in outcome.detail


def test_a_completed_task_must_expose_exactly_one_deliverable(tmp_path: Path) -> None:
    fake = FakeBroker()
    fake.artifacts = [item for item in fake.artifacts if not item["final"]]
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)

    outcome = runner.run_once()

    assert outcome.status == "failed"
    assert "exactly one final artifact" in outcome.detail


def test_pre_210_broker_still_closes_from_result(tmp_path: Path) -> None:
    """Sin `canonical_artifacts` no se sabe cuál es el entregable: se lee `result`."""
    fake = FakeBroker(contract_version="2.9")
    application, runner = _runner(tmp_path, fake)
    create_card(application.board)

    assert runner.run_once().status == "completed"

    assert (tmp_path / "artifacts" / "remote" / "task" / "broker-result.md").is_file()
    done = Card.load(application.board.directory(BoardState.DONE) / "task.md")
    assert done.metadata["execution"]["deliverable"]["source"] == "result"


# --- 5. El X-Admin-Token de un proceso co-ubicado ----------------------------


def test_session_token_is_read_from_the_contracted_keyring_entry() -> None:
    seen: list[tuple[str, str]] = []

    def reader(service: str, username: str) -> str:
        seen.append((service, username))
        return "session-token-from-keyring-0123"

    assert KeyringSessionToken(reader=reader).read() == "session-token-from-keyring-0123"
    assert seen == [("ai-broker", "session_admin_token")]


def test_operator_credential_is_never_read() -> None:
    """Pisar `dashboard_admin_token` convertiría un token de sesión en permanente."""
    source = KeyringSessionToken(username="dashboard_admin_token", reader=lambda *_: "x" * 32)

    with pytest.raises(ValueError, match="session_admin_token"):
        source.read()


def test_missing_session_token_says_what_to_configure() -> None:
    with pytest.raises(SessionTokenUnavailable, match="publish_session_token"):
        KeyringSessionToken(reader=lambda *_: None).read()


def test_keyring_backend_failure_is_not_silent() -> None:
    def broken(*_args: str) -> str:
        raise RuntimeError("credential store locked")

    with pytest.raises(SessionTokenUnavailable, match="credential store locked"):
        KeyringSessionToken(reader=broken).read()


def test_connection_rereads_the_token_after_a_broker_restart() -> None:
    """Tras un reinicio el token ha rotado: reconectar relee, no cachea."""
    tokens = iter(["first-session-token-01234567", "second-session-token-0123456"])
    connection = BrokerConnection(
        KeyringSessionToken(reader=lambda *_: next(tokens)),
        base_url="http://127.0.0.1:8765",
    )

    first = connection.connect()
    second = connection.connect()

    assert first.admin_token != second.admin_token
    assert second.admin_token == "second-session-token-0123456"


def test_runner_recovers_from_a_rotated_token_by_rereading_the_keyring(
    tmp_path: Path,
) -> None:
    fake = FakeBroker("rotated-session-token-01234")
    application, runner = _runner(tmp_path, fake)
    connection = BrokerConnection(
        KeyringSessionToken(reader=lambda *_: fake.token),
        base_url="http://127.0.0.1:8765",
    )
    # El cliente vivo lleva el token del arranque anterior.
    runner.broker = connection._factory(
        "http://127.0.0.1:8765",
        "stale-session-token-0123456",
        transport=runner.broker._client._transport,
    )
    runner.executor.client = runner.broker
    runner.renew_broker = lambda: connection._factory(
        "http://127.0.0.1:8765",
        connection.source.read(),
        transport=runner.broker._client._transport,
    )
    create_card(application.board)

    outcome = runner.run_once()

    assert outcome.status == "completed"
    assert runner.broker.admin_token == fake.token


def test_agora_never_manufactures_the_broker_credential() -> None:
    """El token lo genera el broker en cada arranque; Agora sólo lo lee."""
    import inspect

    from agora.broker import credentials

    source = inspect.getsource(credentials)
    assert "secrets" not in source
    assert "token_urlsafe" not in source


# --- Pasarela ----------------------------------------------------------------


def test_gateway_upload_keeps_the_original_filename(tmp_path: Path) -> None:
    """El broker registraba `agora-gateway-<aleatorio>-<nombre>` en vez del nombre."""
    from test_gateway import _gateway

    fake = FakeBroker()
    uploaded: list[str] = []
    original_handle = fake.handle

    def spy(request):  # type: ignore[no-untyped-def]
        if request.url.path == "/api/v1/files" and request.method == "POST":
            body = request.content.decode("utf-8", "replace")
            uploaded.append(body.split('filename="')[1].split('"')[0])
        return original_handle(request)

    fake.handle = spy  # type: ignore[method-assign]
    _broker, _session, client, headers, _token = _gateway(tmp_path, fake)

    response = client.post(
        "/api/v1/files",
        content=b"contenido",
        headers={**headers, "X-Filename": "informe trimestral.pdf"},
    )

    assert response.status_code == 202, response.text
    assert uploaded == ["informe trimestral.pdf"]
