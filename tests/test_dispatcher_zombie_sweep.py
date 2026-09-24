"""El latido barre lo reclamado y olvidado antes de repartir trabajo nuevo.

Nace de `docs/HALLAZGO_20260919_TARJETA_HUERFANA.md`: `recover_zombies` estaba
escrita y probada, y **no la llamaba nadie en produccion**. Estas pruebas miran
al llamante, no al barrendero: que el dispatcher lo invoque, que informe de lo
que ha rescatado y que en seco no escriba nada.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agora.board import Board, BoardState
from agora.cards import Card
from agora.dispatcher import Dispatcher, DispatchStatus
from agora.harnesses import DeterministicHarness
from conftest import create_card, write_profile


def _dispatcher(tmp_path: Path, board: Board, **kwargs: object) -> Dispatcher:
    profiles_root = tmp_path / "AGENTS"
    write_profile(
        profiles_root,
        "summarizer",
        handles=["summarize"],
        skills=["summarize-text"],
    )
    return Dispatcher(board, profiles_root, DeterministicHarness(tmp_path), **kwargs)  # type: ignore[arg-type]


def _claimed_long_ago(board: Board, name: str, **overrides: object) -> Path:
    """Una tarjeta reclamada hace mucho y **sin `task_id`**: el caso real.

    Es el runner que muere entre el `claim` y el `submit`, que es cuando aun no
    hay nada que apuntar. La vista en vivo llevaba nueve horas y media asi.
    """
    create_card(board, name, **overrides)
    return board.claim(name, "ai-1", when=datetime(2026, 9, 1, 6, 43, tzinfo=UTC))


def test_heartbeat_recovers_a_card_nobody_touched(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    # Un encargo que ningun perfil sabe atender: asi el rescate se mira solo,
    # sin que el reparto del mismo latido se la lleve enseguida. Que se la lleve
    # es correcto, y lo prueba el caso siguiente.
    claimed = _claimed_long_ago(board, "huerfana.md", request="carve a sculpture")
    assert Card.load(claimed).attempts == 0

    outcomes = _dispatcher(tmp_path, board).run_once()

    recovered = [item for item in outcomes if item.status is DispatchStatus.RECOVERED]
    assert [item.card for item in recovered] == ["huerfana.md"]
    assert "attempt 1" in recovered[0].reason
    assert not board.paths(BoardState.IN_PROGRESS)
    # Gasta intento: a los tres acaba en `blocked`, que es donde decide una
    # persona. Un rescate que no cuenta convierte un cuelgue en un bucle.
    assert Card.load(board.directory(BoardState.PENDING) / "huerfana.md").attempts == 1


def test_recovered_card_is_dispatched_in_the_same_heartbeat(tmp_path: Path) -> None:
    """Rescatar y repartir en el mismo latido, no en el siguiente."""
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    _claimed_long_ago(board, "huerfana.md")

    outcomes = _dispatcher(tmp_path, board).run_once()

    estados = [item.status for item in outcomes]
    assert DispatchStatus.RECOVERED in estados
    assert DispatchStatus.DISPATCHED in estados


def test_dry_run_reports_the_sweep_without_touching_the_board(tmp_path: Path) -> None:
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    claimed = _claimed_long_ago(board, "huerfana.md")

    outcomes = _dispatcher(tmp_path, board).run_once(dry_run=True)

    recovered = [item for item in outcomes if item.status is DispatchStatus.RECOVERED]
    assert [item.card for item in recovered] == ["huerfana.md"]
    assert "dry-run left CARD unchanged" in recovered[0].reason
    assert claimed.exists()
    assert Card.load(claimed).attempts == 0


def test_sweep_can_be_switched_off(tmp_path: Path) -> None:
    """`None` desactiva el barrido para quien quiera gobernarlo por su cuenta."""
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    claimed = _claimed_long_ago(board, "huerfana.md")

    outcomes = _dispatcher(tmp_path, board, zombie_timeout=None).run_once()

    assert not [item for item in outcomes if item.status is DispatchStatus.RECOVERED]
    assert claimed.exists()


def test_the_runner_poll_rescues_for_real_even_though_it_simulates(tmp_path: Path) -> None:
    """El unico latido continuo del despliegue real tiene que barrer de verdad.

    En el despliegue API mas runner remoto nadie pide nunca un reparto con
    escritura: el runner pregunta por trabajo y el tablero responde en seco,
    porque quien reclama es el runner. Un barrido atado a ese `dry_run` habria
    quedado sin ejecutarse jamas —el mismo fallo que esto corrige, un piso mas
    arriba—.
    """
    from agora.api.service import RemoteWorkService
    from agora.application import AgoraApplication

    application = AgoraApplication(tmp_path)
    application.initialize()
    write_profile(tmp_path / "AGENTS", "summarizer", handles=["summarize"])
    claimed = _claimed_long_ago(application.board, "huerfana.md")

    items = RemoteWorkService(application).work(("summarizer",))

    assert not claimed.exists()
    pendiente = application.board.directory(BoardState.PENDING) / "huerfana.md"
    assert Card.load(pendiente).attempts == 1
    # Y ademas vuelve a ofrecerse: rescatarla sin repartirla la dejaria parada.
    assert [item.filename for item in items] == ["huerfana.md"]


def test_a_card_claimed_just_now_is_left_alone(tmp_path: Path) -> None:
    """El umbral protege a quien esta trabajando de verdad."""
    board = Board(tmp_path / "KANBAN")
    board.initialize()
    create_card(board, "en-marcha.md")
    claimed = board.claim("en-marcha.md", "ai-1")

    outcomes = _dispatcher(tmp_path, board, zombie_timeout=timedelta(minutes=30)).run_once()

    assert not [item for item in outcomes if item.status is DispatchStatus.RECOVERED]
    assert claimed.exists()
