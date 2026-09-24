"""Model-free heartbeat: census, gates, deterministic match, claim, launch."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from agora.board import Board, BoardState
from agora.cards import Card
from agora.errors import CardFormatError, ClaimConflict, ProfileFormatError
from agora.harnesses import WorkerLauncher
from agora.matching import MatchStatus, match_card
from agora.profiles import load_profiles


class DispatchStatus(StrEnum):
    DISPATCHED = "dispatched"
    WOULD_DISPATCH = "would_dispatch"
    WAITING_INPUT = "waiting_input"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CONFLICT = "conflict"
    ERROR = "error"
    #: Una tarjeta reclamada hace demasiado y devuelta a pendientes.
    RECOVERED = "recovered"


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    card: str
    status: DispatchStatus
    profile: str | None = None
    reason: str = ""
    final_path: Path | None = None


#: Quien puede depositar trabajo por el sistema de ficheros. La lista existe
#: para que una tarjeta dejada a mano en la carpeta no se ejecute sola; un
#: cliente que llega por el API autenticado se anade a esta lista al arrancar
#: el API, porque ya ha pasado una comprobacion mas fuerte que un nombre.
DEFAULT_TRUSTED_ORIGINS: tuple[str, ...] = ("human", "athena", "agora", "test")

#: Cuanto puede llevar una tarjeta reclamada sin que nadie la toque antes de
#: darla por abandonada y devolverla a pendientes.
#:
#: Media hora. El recorrido sano tarda minutos —claim y envio al broker ocurren
#: en el mismo minuto, y la ejecucion va de tres a diez—, asi que treinta no
#: molesta a nadie que este trabajando de verdad.
#:
#: Existe porque `Board.recover_zombies` estaba escrita, probada y **no la
#: llamaba nadie en produccion**: el unico uso en todo el repositorio era su
#: propia prueba. Una tarjeta reclamada por un runner que muere antes de enviar
#: al broker no tiene `task_id`, asi que la recuperacion del runner tampoco la
#: alcanza —su comprobacion de edad vive dentro de esa rama—, y se quedaba
#: `in-progress` con `attempts: 0` para siempre. Visto en vivo el 19-09-2026:
#: nueve horas y media. Ver `docs/HALLAZGO_20260919_TARJETA_HUERFANA.md`.
DEFAULT_ZOMBIE_TIMEOUT = timedelta(minutes=30)


class Dispatcher:
    def __init__(
        self,
        board: Board,
        profiles_root: Path,
        launcher: WorkerLauncher,
        *,
        trusted_origins: Iterable[str] = DEFAULT_TRUSTED_ORIGINS,
        max_dispatches_per_round: int = 3,
        zombie_timeout: timedelta | None = DEFAULT_ZOMBIE_TIMEOUT,
    ) -> None:
        self.board = board
        self.profiles_root = profiles_root
        self.launcher = launcher
        self.trusted_origins = frozenset(item.strip().lower() for item in trusted_origins)
        self.max_dispatches = max(1, max_dispatches_per_round)
        #: Cuanto puede estar una tarjeta reclamada sin que nadie la toque.
        #: `None` desactiva el barrido, para quien quiera gobernarlo por su
        #: cuenta; no es el valor por defecto a proposito.
        self.zombie_timeout = zombie_timeout

    def run_once(self, *, dry_run: bool = False) -> tuple[DispatchOutcome, ...]:
        try:
            profiles = load_profiles(self.profiles_root)
        except ProfileFormatError as exc:
            return (DispatchOutcome("PROFILE census", DispatchStatus.ERROR, reason=str(exc)),)
        # Primero se barre lo abandonado, y **despues** se reparte trabajo
        # nuevo: una tarjeta recuperada en este mismo latido vuelve a pendientes
        # y puede repartirse ya, sin esperar al siguiente. Al reves seria correcto
        # pero tardaria un latido de mas en cada rescate.
        outcomes: list[DispatchOutcome] = list(self.sweep_abandoned(dry_run=dry_run))
        candidates, errors = self._cards_by_priority()
        outcomes.extend(errors)
        dispatched = 0
        for path, card in candidates:
            gate = self._gate(path, card, dry_run=dry_run)
            if gate is not None:
                outcomes.append(gate)
                continue
            result = match_card(card, profiles)
            if result.status is not MatchStatus.MATCHED or result.profile is None:
                status = (
                    DispatchStatus.AMBIGUOUS
                    if result.status is MatchStatus.AMBIGUOUS
                    else DispatchStatus.UNMATCHED
                )
                outcomes.append(
                    DispatchOutcome(path.name, status, reason=result.reason or result.status.value)
                )
                continue
            harness_name = getattr(self.launcher, "harness_name", None)
            if result.profile.harness and harness_name and result.profile.harness != harness_name:
                outcomes.append(
                    DispatchOutcome(
                        path.name,
                        DispatchStatus.SKIPPED,
                        result.profile.name,
                        f"requires {result.profile.harness} harness",
                    )
                )
                continue
            availability = getattr(self.launcher, "is_available", None)
            if callable(availability) and not availability(result.profile):
                outcomes.append(
                    DispatchOutcome(
                        path.name,
                        DispatchStatus.SKIPPED,
                        result.profile.name,
                        "compatible runner is unavailable",
                    )
                )
                continue
            if dispatched >= self.max_dispatches:
                outcomes.append(
                    DispatchOutcome(
                        path.name, DispatchStatus.SKIPPED, reason="round capacity reached"
                    )
                )
                continue
            if dry_run:
                outcomes.append(
                    DispatchOutcome(
                        path.name, DispatchStatus.WOULD_DISPATCH, result.profile.name, result.reason
                    )
                )
                dispatched += 1
                continue
            try:
                claimed = self.board.claim(path.name, result.profile.name)
            except ClaimConflict as exc:
                outcomes.append(
                    DispatchOutcome(path.name, DispatchStatus.CONFLICT, reason=str(exc))
                )
                continue
            try:
                final = self.launcher.launch(self.board, claimed, result.profile)
            except (Exception, KeyboardInterrupt) as exc:
                if claimed.exists():
                    self.board.return_pending(
                        claimed,
                        actor="dispatcher",
                        reason=f"Harness failed visibly: {type(exc).__name__}: {exc}",
                    )
                outcomes.append(
                    DispatchOutcome(path.name, DispatchStatus.ERROR, result.profile.name, str(exc))
                )
                dispatched += 1
                continue
            outcomes.append(
                DispatchOutcome(
                    path.name, DispatchStatus.DISPATCHED, result.profile.name, result.reason, final
                )
            )
            dispatched += 1
        return tuple(outcomes)

    def sweep_abandoned(self, *, dry_run: bool = False) -> tuple[DispatchOutcome, ...]:
        """Contrata al barrendero: devuelve a pendientes lo reclamado y olvidado.

        Vive aqui, y no en el runner, porque el caso que de verdad ocurre es que
        el runner **deje de existir**. Un rescate que dependa de que el rescatado
        siga vivo no rescata nada.

        Es publica porque el reparto no es su unico llamante. En el despliegue
        real —API mas runner remoto— nadie pide un reparto con escritura: el
        runner pregunta por trabajo y `run_once` corre siempre en seco, asi que
        un barrido atado a ese `dry_run` habria vuelto a no ejecutarse nunca.
        Ese es el mismo fallo que esto corrige, un piso mas arriba.

        Un fallo barriendo no puede tumbar el reparto: se informa como cualquier
        otro error del latido y se sigue.
        """
        if self.zombie_timeout is None:
            return ()
        try:
            actions = self.board.recover_zombies(threshold=self.zombie_timeout, dry_run=dry_run)
        except OSError as exc:
            return (DispatchOutcome("ZOMBIE sweep", DispatchStatus.ERROR, reason=str(exc)),)
        suffix = "; dry-run left CARD unchanged" if dry_run else ""
        return tuple(
            DispatchOutcome(
                action.card,
                DispatchStatus.RECOVERED,
                reason=(
                    f"{action.reason}: returned to {action.destination.value} "
                    f"with attempt {action.attempts}{suffix}"
                ),
            )
            for action in actions
        )

    def _cards_by_priority(self) -> tuple[list[tuple[Path, Card]], tuple[DispatchOutcome, ...]]:
        cards: list[tuple[Path, Card]] = []
        errors: list[DispatchOutcome] = []
        for path in self.board.paths(BoardState.PENDING):
            try:
                cards.append((path, Card.load(path)))
            except (CardFormatError, OSError) as exc:
                errors.append(DispatchOutcome(path.name, DispatchStatus.ERROR, reason=str(exc)))
        cards.sort(
            key=lambda item: (-item[1].priority_score, item[0].stat().st_mtime_ns, item[0].name)
        )
        return cards, tuple(errors)

    def _gate(self, path: Path, card: Card, *, dry_run: bool = False) -> DispatchOutcome | None:
        if card.blocked:
            return DispatchOutcome(path.name, DispatchStatus.BLOCKED, reason="blocked field is set")
        if card.attempts >= card.max_attempts:
            return DispatchOutcome(
                path.name, DispatchStatus.BLOCKED, reason="attempt limit reached"
            )
        if card.metadata["origin"].strip().lower() not in self.trusted_origins:
            if dry_run:
                return DispatchOutcome(
                    path.name,
                    DispatchStatus.BLOCKED,
                    reason="would block untrusted origin; dry-run left CARD unchanged",
                )
            blocked = self.board.block_pending(
                path,
                actor="dispatcher",
                reason=f"Untrusted origin: {card.metadata['origin']}",
                code="untrusted_origin",
            )
            return DispatchOutcome(
                path.name, DispatchStatus.BLOCKED, reason="untrusted origin", final_path=blocked
            )
        if card.recipient and card.recipient.strip().lower() in {"human", "persona", "user"}:
            return DispatchOutcome(path.name, DispatchStatus.SKIPPED, reason="addressed to a human")
        missing = _missing_inputs(card.metadata.get("inputs"), self.board.root.parent)
        if missing:
            return DispatchOutcome(
                path.name,
                DispatchStatus.WAITING_INPUT,
                reason="missing input(s): " + ", ".join(str(item) for item in missing),
            )
        return None


def _missing_inputs(value: Any, base: Path) -> tuple[Path, ...]:
    referenced = tuple(_input_paths(value))
    missing: list[Path] = []
    for item in referenced:
        path = item if item.is_absolute() else base / item
        if not path.exists():
            missing.append(path.resolve())
    return tuple(missing)


def _input_paths(value: Any) -> Iterable[Path]:
    if isinstance(value, str) and value.strip():
        yield Path(value)
    elif isinstance(value, list):
        for item in value:
            yield from _input_paths(item)
    elif isinstance(value, dict):
        if isinstance(value.get("path"), str):
            yield Path(value["path"])
        else:
            for item in value.values():
                yield from _input_paths(item)
