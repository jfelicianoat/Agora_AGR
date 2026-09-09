"""A12: fijar `perfil@linea` y saber despues que version hizo un artefacto.

Dos cosas distintas, y las dos hacen falta:

- **Antes**: un cliente puede pedir una linea de version concreta, y si no esta,
  se le dice. Nunca se le da otra en silencio.
- **Despues**: la tarjeta guarda la version exacta que la ejecuto, para que
  dentro de seis meses se sepa que produjo el artefacto.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agora.api import ApiSettings, create_api
from agora.application import AgoraApplication
from agora.board import BoardState
from agora.cards import Card
from agora.matching import MatchStatus, match_card, parse_recipient
from agora.profiles import Profile, load_profiles
from conftest import create_card, write_profile

TOKEN = "a12-versioning-token-0123456789"

PROFILES_ROOT = Path(__file__).resolve().parents[1] / "profiles" / "task-planning"


def _card(recipient: str | None = None, **extra: Any) -> Card:
    return Card.create(
        function="transform",
        request="summarize document",
        origin="test",
        recipient=recipient,
        **extra,
    )


def _profiles(tmp_path: Path, version: str = "1.3.0") -> tuple[Profile, ...]:
    root = tmp_path / "AGENTS"
    write_profile(root, "resumidor", function="transform", handles=["summarize document"])
    path = root / "resumidor" / "PROFILE.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace('version: 1.0.0', f"version: {version}"),
        encoding="utf-8",
    )
    return load_profiles(root)


# --- La notacion --------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("task-intake", ("task-intake", None)),
        ("task-intake@1", ("task-intake", 1)),
        ("task-intake@2", ("task-intake", 2)),
        ("  task-intake @ 1 ".replace(" @ ", "@"), ("task-intake", 1)),
    ],
)
def test_the_notation_splits_name_from_line(valor: str, esperado: tuple) -> None:
    assert parse_recipient(valor) == esperado


def test_a_version_that_is_not_a_number_is_refused() -> None:
    with pytest.raises(ValueError, match="numero de linea"):
        parse_recipient("task-intake@uno")


def test_pinning_a_full_semver_is_refused() -> None:
    """Fijar `1.3.0` obligaria al cliente a perseguir cada correccion."""
    with pytest.raises(ValueError):
        parse_recipient("task-intake@1.3.0")


def test_every_real_profile_reports_its_line() -> None:
    for profile in load_profiles(PROFILES_ROOT):
        assert profile.major == int(profile.version.split(".")[0]), profile.name


# --- Resolucion explicita -----------------------------------------------------


def test_the_pinned_line_that_exists_is_selected(tmp_path: Path) -> None:
    profiles = _profiles(tmp_path, "1.3.0")

    resultado = match_card(_card("resumidor@1"), profiles)

    assert resultado.status is MatchStatus.MATCHED
    assert resultado.profile is not None and resultado.profile.name == "resumidor"
    assert "resumidor@1" in resultado.reason


def test_a_pinned_line_that_is_not_there_is_refused_not_substituted(
    tmp_path: Path,
) -> None:
    """Lo importante de la fase: nunca se le da al cliente otra linea en silencio."""
    profiles = _profiles(tmp_path, "1.3.0")

    resultado = match_card(_card("resumidor@2"), profiles)

    assert resultado.status is MatchStatus.INVALID_RECIPIENT
    assert resultado.profile is None
    assert "resumidor@2" in resultado.reason
    assert "1.3.0" in resultado.reason


def test_the_refusal_says_which_line_is_available(tmp_path: Path) -> None:
    """Un error que no dice la alternativa obliga a adivinar."""
    profiles = _profiles(tmp_path, "2.0.0")

    resultado = match_card(_card("resumidor@1"), profiles)

    assert resultado.candidates == ("resumidor@2",)


def test_a_patch_inside_the_same_line_still_matches(tmp_path: Path) -> None:
    """Semver promete que dentro de una linea la semantica no cambia."""
    for version in ("1.0.0", "1.4.9", "1.12.3"):
        profiles = _profiles(tmp_path / version, version)
        assert match_card(_card("resumidor@1"), profiles).status is MatchStatus.MATCHED, version


def test_naming_the_profile_without_a_line_works_as_always(tmp_path: Path) -> None:
    """Compatibilidad: quien no fija version se queda con la que haya."""
    profiles = _profiles(tmp_path, "3.1.4")

    resultado = match_card(_card("resumidor"), profiles)

    assert resultado.status is MatchStatus.MATCHED


def test_a_malformed_pin_does_not_pick_a_profile(tmp_path: Path) -> None:
    profiles = _profiles(tmp_path)

    resultado = match_card(_card("resumidor@ultima"), profiles)

    assert resultado.status is MatchStatus.INVALID_RECIPIENT
    assert resultado.profile is None


def test_pinning_the_wrong_function_is_still_refused(tmp_path: Path) -> None:
    """La linea no salta las comprobaciones que ya habia."""
    profiles = _profiles(tmp_path)
    card = Card.create(
        function="decompose", request="summarize document", origin="test",
        recipient="resumidor@1",
    )

    assert match_card(card, profiles).status is MatchStatus.INVALID_RECIPIENT


def test_matching_by_handle_is_untouched(tmp_path: Path) -> None:
    """Sin `recipient` no hay nada que resolver, y todo sigue igual."""
    profiles = _profiles(tmp_path)

    resultado = match_card(_card(), profiles)

    assert resultado.status is MatchStatus.MATCHED
    assert resultado.reason == "longest matching handle won"


# --- Trazabilidad del resultado ----------------------------------------------


def _api(tmp_path: Path) -> tuple[AgoraApplication, TestClient]:
    application = AgoraApplication(tmp_path)
    application.initialize()
    _profiles(tmp_path, "1.3.0")
    app: FastAPI = create_api(
        application, ApiSettings(token=TOKEN, principal="cliente", require_https=True)
    )
    return application, TestClient(
        app, base_url="https://testserver", headers={"Authorization": f"Bearer {TOKEN}"}
    )


def test_the_card_records_the_exact_version_that_ran_it(tmp_path: Path) -> None:
    """Sin esto, dentro de seis meses el artefacto no dice quien lo hizo."""
    application, client = _api(tmp_path)
    create_card(application.board)

    client.post(
        "/api/v1/cards/task.md/claim",
        json={"runner_id": "runner-1", "profile": "resumidor"},
        headers={"Idempotency-Key": "c"},
    )

    card = Card.load(application.board.directory(BoardState.IN_PROGRESS) / "task.md")
    assert card.metadata["profile"] == "resumidor"
    assert card.metadata["profile_version"] == "1.3.0"


def test_the_record_says_which_line_was_used(tmp_path: Path) -> None:
    application, client = _api(tmp_path)
    create_card(application.board)

    client.post(
        "/api/v1/cards/task.md/claim",
        json={"runner_id": "runner-1", "profile": "resumidor"},
        headers={"Idempotency-Key": "c"},
    )

    card = Card.load(application.board.directory(BoardState.IN_PROGRESS) / "task.md")
    assert "resumidor@1.3.0" in card.body


def test_the_client_sees_the_version_that_ran_its_work(tmp_path: Path) -> None:
    application, client = _api(tmp_path)
    create_card(application.board)
    client.post(
        "/api/v1/cards/task.md/claim",
        json={"runner_id": "runner-1", "profile": "resumidor"},
        headers={"Idempotency-Key": "c"},
    )

    estado = client.get("/api/v1/cards/task.md").json()

    assert estado["metadata"]["profile_version"] == "1.3.0"


def test_an_unknown_profile_records_nothing_instead_of_lying(tmp_path: Path) -> None:
    """Si no se puede resolver, no se inventa una version."""
    from agora.api.service import RemoteWorkService

    application, _ = _api(tmp_path)
    service = RemoteWorkService(application)

    assert service._profile_version("no-existe") is None


# --- Lo que el cliente puede descubrir ---------------------------------------


def test_the_catalogue_publishes_the_version_of_every_profile(
    tmp_path: Path,
) -> None:
    _, client = _api(tmp_path)

    perfiles = client.get("/api/v1/profiles").json()["profiles"]

    perfil = next(p for p in perfiles if p["name"] == "resumidor")
    assert perfil["version"] == "1.3.0"


def test_the_catalogue_publishes_the_line_so_it_can_be_pinned(
    tmp_path: Path,
) -> None:
    """Sin `major`, el cliente tendria que partir el semver por su cuenta."""
    _, client = _api(tmp_path)

    perfil = next(
        p for p in client.get("/api/v1/profiles").json()["profiles"]
        if p["name"] == "resumidor"
    )

    assert perfil["major"] == 1
    assert perfil["version"] == "1.3.0"


def test_the_catalogue_does_not_leak_the_board_filesystem(tmp_path: Path) -> None:
    """La ruta del PROFILE en disco no le sirve de nada a un cliente.

    Le revela la estructura del PC del tablero y no le permite hacer nada.
    Estaba saliendo por serializar el objeto entero; ahora la respuesta se
    compone campo a campo.
    """
    _, client = _api(tmp_path)

    perfiles = client.get("/api/v1/profiles").json()["profiles"]

    for perfil in perfiles:
        assert "source" not in perfil, perfil["name"]
        assert not any(
            isinstance(valor, str) and ("\\\\" in valor or valor.startswith("/"))
            for valor in perfil.values()
        ), perfil
