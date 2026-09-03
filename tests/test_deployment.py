"""Lo que hace falta para desplegar el runner en el PC IA y que trabaje solo.

PKI interna, catálogo de capacidades, identidad del tablero y empaquetado. Casi
todas estas pruebas nacen de un fallo observado ejecutando la instalación real
contra el broker vivo el 2026-09-04, no de leer la especificación.
"""

from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any

import pytest
from cryptography import x509

from agora.api.contracts import WorkItem
from agora.application import AgoraApplication
from agora.broker.contracts import BrokerPolicy
from agora.broker.executor import broker_idempotency_key
from agora.models import ModelCatalog, ModelCatalogError, profile_capacity
from agora.remote.client import AgoraApiClient
from agora.security.certificates import (
    CA_PASSPHRASE_ENV,
    CertificateAuthority,
    CertificateError,
    describe,
    spki_sha256,
)
from test_tls import _memory_handshake

PASSPHRASE = "una-frase-de-paso-suficientemente-larga"


@pytest.fixture
def authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CertificateAuthority:
    monkeypatch.setenv(CA_PASSPHRASE_ENV, PASSPHRASE)
    ca = CertificateAuthority(tmp_path / "pki")
    ca.create()
    return ca


# --- PKI interna --------------------------------------------------------------


def test_ca_key_is_never_written_unencrypted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(CA_PASSPHRASE_ENV, PASSPHRASE)
    ca = CertificateAuthority(tmp_path / "pki")
    ca.create()

    assert b"ENCRYPTED PRIVATE KEY" in ca.key_path.read_bytes()
    monkeypatch.setenv(CA_PASSPHRASE_ENV, "otra-frase-distinta-larga")
    with pytest.raises(CertificateError, match="passphrase"):
        ca.load()


def test_a_weak_passphrase_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CA_PASSPHRASE_ENV, "corta")
    with pytest.raises(CertificateError, match=CA_PASSPHRASE_ENV):
        CertificateAuthority(tmp_path / "pki").create()


def test_a_second_ca_never_silently_splits_the_trust_anchor(
    authority: CertificateAuthority,
) -> None:
    with pytest.raises(CertificateError, match="already holds a CA"):
        authority.create()


def test_issued_certificate_completes_a_real_tls_handshake(
    authority: CertificateAuthority, tmp_path: Path
) -> None:
    """OpenSSL 3 rechazaba la cadena por falta de Authority Key Identifier.

    El fallo no lo vio ninguna prueba de estructura: apareció al arrancar el
    tablero de verdad («Missing Authority Key Identifier»). Esta prueba hace el
    handshake, que es lo único que lo habría cazado.
    """
    certificate, key = authority.issue(("localhost", "127.0.0.1"), tmp_path / "out")
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(certificate, key)
    client = ssl.create_default_context(cafile=str(authority.certificate_path))

    _memory_handshake(client, server)


def test_issued_certificate_carries_the_hosts_the_runner_will_use(
    authority: CertificateAuthority, tmp_path: Path
) -> None:
    certificate, _key = authority.issue(("192.168.1.50", "tablero.local"), tmp_path / "out")
    details = describe(certificate)

    assert set(details.hosts) == {"192.168.1.50", "tablero.local"}
    assert details.days_remaining > 300


def test_a_foreign_ca_cannot_impersonate_the_board(
    authority: CertificateAuthority, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    certificate, key = authority.issue(("localhost",), tmp_path / "out")
    intruder = CertificateAuthority(tmp_path / "intruder")
    intruder.create()
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(certificate, key)
    client = ssl.create_default_context(cafile=str(intruder.certificate_path))

    with pytest.raises(ssl.SSLCertVerificationError):
        _memory_handshake(client, server)


def test_renewal_can_keep_the_pin_and_by_default_does_not(
    authority: CertificateAuthority, tmp_path: Path
) -> None:
    destination = tmp_path / "out"
    first, _ = authority.issue(("localhost",), destination)
    pinned = describe(first).spki_sha256

    renewed, _ = authority.issue(("localhost",), destination, reuse_key=True)
    assert describe(renewed).spki_sha256 == pinned

    rotated, _ = authority.issue(("localhost",), destination)
    assert describe(rotated).spki_sha256 != pinned


def test_spki_is_computed_over_the_public_key_not_the_certificate(
    authority: CertificateAuthority, tmp_path: Path
) -> None:
    certificate, _ = authority.issue(("localhost",), tmp_path / "out")
    loaded = x509.load_pem_x509_certificate(certificate.read_bytes())

    assert spki_sha256(loaded) == describe(certificate).spki_sha256


# --- Anclaje en el cliente ----------------------------------------------------


def test_pinned_client_refuses_a_different_public_key() -> None:
    import httpx

    def answer(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    client = AgoraApiClient(
        "https://tablero.local",
        "token-de-pruebas-0123456789",
        transport=httpx.MockTransport(answer),
        pin_spki_sha256="anclaje-que-no-coincide",
    )

    # Sin capa TLS observable el anclaje no se puede afirmar, y callar sería
    # convertir la promesa en decorativa.
    with pytest.raises(Exception, match="pin"):
        client.health()


def test_unpinned_client_works_without_a_tls_layer() -> None:
    import httpx

    def answer(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    client = AgoraApiClient(
        "https://tablero.local",
        "token-de-pruebas-0123456789",
        transport=httpx.MockTransport(answer),
    )

    assert client.health() == {"status": "ok"}


# --- Catálogo de capacidades --------------------------------------------------


def _catalog(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "models.yml"
    path.write_text(body, encoding="utf-8")
    return path


def test_capacity_turns_a_profile_into_a_policy(tmp_path: Path) -> None:
    catalog = ModelCatalog.load(
        _catalog(
            tmp_path,
            """
default: standard
capacities:
  standard:
    determinism: routed
    max_cost_usd: 0.5
  maximum:
    determinism: strict
    data_classification: confidential
    target_model: {provider: ollama, deployment: local, model: qwen3.8:27b}
""",
        )
    )
    base = BrokerPolicy(timeout_seconds=600, zombie_timeout_seconds=900)

    strict = catalog.policy_for("maximum", base)
    assert strict.determinism == "strict"
    assert strict.target_model == {
        "provider": "ollama",
        "deployment": "local",
        "model": "qwen3.8:27b",
    }
    # Confidencial implica exclusividad de contenido sin declararla aparte.
    assert strict.auxiliary_invocations_allowed is False
    assert catalog.policy_for(None, base).determinism == "routed"


def test_a_capacity_the_board_cannot_serve_is_an_error_not_a_default(tmp_path: Path) -> None:
    catalog = ModelCatalog.load(
        _catalog(tmp_path, "default: standard\ncapacities:\n  standard: {determinism: routed}\n")
    )

    with pytest.raises(ModelCatalogError, match="inventada"):
        catalog.policy_for("inventada", BrokerPolicy())


def test_a_misspelled_field_fails_at_load_not_in_production(tmp_path: Path) -> None:
    """Un campo ignorado en silencio deja la tarjeta con una política que nadie pidió."""
    with pytest.raises(ModelCatalogError, match="determinsm"):
        ModelCatalog.load(
            _catalog(
                tmp_path,
                "default: standard\ncapacities:\n  standard: {determinsm: routed}\n",
            )
        )


def test_default_must_name_a_declared_capacity(tmp_path: Path) -> None:
    with pytest.raises(ModelCatalogError, match="default"):
        ModelCatalog.load(
            _catalog(tmp_path, "default: ausente\ncapacities:\n  standard: {}\n")
        )


def test_the_shipped_example_catalog_is_valid() -> None:
    root = Path(__file__).resolve().parents[1] / "examples" / "f0-demo" / "AGENTS"
    catalog = ModelCatalog.load(root / "models.yml")
    base = BrokerPolicy(timeout_seconds=600, zombie_timeout_seconds=900)

    assert set(catalog.capacities) == {"minimum", "standard", "maximum"}
    assert catalog.policy_for("maximum", base).determinism == "strict"


def test_profile_capacity_reads_the_declared_tier() -> None:
    assert profile_capacity({"model_capacity": " maximum "}) == "maximum"
    assert profile_capacity({}) is None
    with pytest.raises(ModelCatalogError):
        profile_capacity({"model_capacity": 3})


# --- Identidad del tablero ----------------------------------------------------


def test_two_boards_do_not_collide_on_the_brokers_idempotency_key(tmp_path: Path) -> None:
    """`IDEMPOTENCY_CONFLICT` real: dos tableros con una tarjeta `task.md`.

    Observado al ensayar la instalación contra el broker vivo el 2026-09-04.
    """
    common: dict[str, Any] = {
        "filename": "task.md",
        "function": "transform",
        "request": "summarize",
        "priority": "normal",
        "attempts": 0,
        "profile": "summarizer",
    }
    first = WorkItem(**common, board_id="1111")
    second = WorkItem(**common, board_id="2222")

    assert broker_idempotency_key(first) != broker_idempotency_key(second)
    repeated = WorkItem(**common, board_id="1111")
    assert broker_idempotency_key(first) == broker_idempotency_key(repeated)


def test_board_identity_survives_restarts(tmp_path: Path) -> None:
    application = AgoraApplication(tmp_path)
    application.initialize()
    first = application.instance_id

    assert first == AgoraApplication(tmp_path).instance_id
    assert (tmp_path / ".agora-instance").read_text(encoding="utf-8").strip() == first


def test_work_items_carry_the_board_identity(tmp_path: Path) -> None:
    from agora.api.service import RemoteWorkService
    from conftest import create_card, write_profile

    application = AgoraApplication(tmp_path)
    application.initialize()
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    create_card(application.board)
    service = RemoteWorkService(application)

    items = service.work(("summarizer",))
    assert items and items[0].board_id == application.instance_id


# --- Empaquetado del runner ---------------------------------------------------


def test_importing_the_wire_contracts_does_not_drag_in_the_board_server() -> None:
    """`pip install agora[runner]` no instala FastAPI, y el runner debe arrancar.

    `agora/api/__init__` importaba `create_api` de forma ansiosa, así que
    importar los contratos exigía el servidor entero: el runner instalado moría
    con `ModuleNotFoundError: fastapi`. Comprobado instalándolo.
    """
    import inspect

    from agora import api

    source = inspect.getsource(api)
    assert "def __getattr__" in source
    assert "from agora.api.app import" not in source.replace("if TYPE_CHECKING:", "").split(
        "def __getattr__"
    )[0].replace("    from agora.api.app import ApiSettings, create_api", "")


def test_the_runner_extra_declares_what_the_runner_imports() -> None:
    """El extra `runner` se quedó corto dos veces al instalarlo de verdad."""
    import tomllib

    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    extra = " ".join(project["project"]["optional-dependencies"]["runner"])

    for package in ("httpx", "pydantic", "keyring", "cryptography"):
        assert package in extra, f"el runner importa {package} y el extra no lo declara"
    assert "fastapi" not in extra, "el runner no sirve la API: no debe arrastrar FastAPI"


def test_every_console_script_points_at_something_importable() -> None:
    import importlib
    import tomllib

    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)

    for name, target in project["project"]["scripts"].items():
        module_name, _, attribute = target.partition(":")
        module = importlib.import_module(module_name)
        assert hasattr(module, attribute), f"{name} apunta a {target}, que no existe"
