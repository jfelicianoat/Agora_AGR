"""Filesystem BOARD and its atomic state transitions."""

from __future__ import annotations

import functools
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

from agora.cards import Card, format_timestamp, utc_now
from agora.errors import BoardBusy, CardFormatError, ClaimConflict, InvalidTransition


class BoardState(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    DONE = "done"
    BLOCKED = "blocked"
    ARCHIVE = "archive"
    SCHEDULED = "scheduled"

    @property
    def is_terminal(self) -> bool:
        """El trabajo acabo y nada lo reabre.

        La distincion existe para que nadie tenga que deducirla de una lista
        escrita a mano en tres sitios distintos: un cliente que sondea necesita
        saber cuando dejar de mirar, y un runner que se reinicia necesita saber
        que no debe resucitar una tarjeta cerrada.

        `blocked` **no** es terminal: agoto sus intentos o tropezo con algo, y
        una persona puede desbloquearla. Se ha parado, que no es lo mismo que
        haber terminado.
        """
        return self in {BoardState.DONE, BoardState.ARCHIVE}

    @property
    def is_in_flight(self) -> bool:
        """El trabajo sigue su curso por si solo, sin que nadie intervenga."""
        return self in {BoardState.PENDING, BoardState.IN_PROGRESS, BoardState.SCHEDULED}

    @property
    def needs_intervention(self) -> bool:
        """Parado, y no se movera hasta que alguien decida algo."""
        return self is BoardState.BLOCKED


@dataclass(frozen=True, slots=True)
class RecoveryAction:
    card: str
    destination: BoardState
    attempts: int
    reason: str


#: Cuántas veces puede volver una tarjeta a `pending` sin gastar intento antes de
#: pararse. Sin tope, un runner que la suelta en cada vuelta (el broker no puede
#: cumplirla, Core la deniega) reescribía tarjeta, idempotencia y eventos cada
#: 15 s para siempre (A-2 del arbitraje de la auditoría del 19-11).
MAX_FREE_RELEASES = 5

#: Cuánto espera una mutación a que otro proceso suelte el tablero.
_LOCK_WAIT_SECONDS = 10.0

#: Profundidad del candado por tablero y por hilo (reentrante aunque haya varios
#: objetos `Board` sobre la misma carpeta en el mismo hilo).
_held = threading.local()

_F = TypeVar("_F", bound=Callable[..., Any])


def _locked(method: _F) -> _F:
    """Serializa la mutación con las de otros procesos sobre el mismo tablero."""

    @functools.wraps(method)
    def wrapper(self: Board, *args: Any, **kwargs: Any) -> Any:
        with self._mutation_lock():
            return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


class Board:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @contextmanager
    def _mutation_lock(self) -> Iterator[None]:
        """Candado del sistema operativo entre procesos, reentrante en el mismo hilo.

        La ventana y el API son procesos distintos sobre las mismas carpetas. Sin
        esto, desbloquear en la ventana a la vez que el API desbloqueaba dejaba la
        tarjeta en `blocked` y en `pending` a la vez (A-4 del arbitraje).

        Es un bloqueo de rango sobre un fichero que nunca se borra: si el proceso
        muere, Windows lo suelta solo, y un indexador que abra el fichero no
        impide soltarlo (B-2). Por eso no hay caducidad que adivinar.
        """
        key = str(self.root)
        depths: dict[str, int] = _held.__dict__.setdefault("depths", {})
        if depths.get(key):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        self.root.mkdir(parents=True, exist_ok=True)
        stream = open(self.root / ".board.lock", "a+b")  # noqa: SIM115 - vive en el with
        try:
            deadline = time.monotonic() + _LOCK_WAIT_SECONDS
            while not _try_lock(stream):
                if time.monotonic() > deadline:
                    raise BoardBusy("BOARD is busy: another process holds its lock")
                time.sleep(0.02)
            depths[key] = 1
            try:
                yield
            finally:
                depths[key] = 0
                _unlock(stream)
        finally:
            stream.close()

    def initialize(self) -> None:
        for state in BoardState:
            (self.root / state.value).mkdir(parents=True, exist_ok=True)

    def directory(self, state: BoardState) -> Path:
        return self.root / state.value

    def paths(self, state: BoardState) -> tuple[Path, ...]:
        directory = self.directory(state)
        if not directory.exists():
            return ()
        return tuple(sorted(path for path in directory.glob("*.md") if path.is_file()))

    @_locked
    def create(self, filename: str, card: Card, *, state: BoardState = BoardState.PENDING) -> Path:
        self.initialize()
        name = _card_filename(filename)
        target = self.directory(state) / name
        payload = card.serialize().encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(target, flags)
        except FileExistsError as exc:
            raise InvalidTransition(f"CARD already exists in {state.value}: {name}") from exc
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        card.source = target
        return target

    @_locked
    def claim(self, filename: str, agent: str, *, when: datetime | None = None) -> Path:
        name = _card_filename(filename)
        source = self.directory(BoardState.PENDING) / name
        destination = self.directory(BoardState.IN_PROGRESS) / name
        guard = self.directory(BoardState.IN_PROGRESS) / ".claims" / f"{name}.claim"
        guard.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(guard)
        except FileExistsError as exc:
            raise ClaimConflict(f"Atomic claim lost for {name}") from exc
        try:
            try:
                card = Card.load(source)
            except FileNotFoundError as exc:
                raise ClaimConflict(f"CARD is no longer pending: {name}") from exc
            _rename_claim(source, destination)
            moment = when or utc_now()
            try:
                card.metadata["agent"] = agent
                card.metadata["claimed"] = format_timestamp(moment)
                card.append_record(
                    agent, ["Claimed CARD atomically and began admission."], when=moment
                )
                card.save(destination)
            except BaseException:
                if destination.exists() and not source.exists():
                    os.rename(destination, source)
                raise
        finally:
            with suppress(FileNotFoundError):
                os.rmdir(guard)
        return destination

    @_locked
    def progress(
        self,
        claimed_path: Path,
        actor: str,
        milestones: list[str],
        *,
        when: datetime | None = None,
    ) -> None:
        _require_location(claimed_path, self.directory(BoardState.IN_PROGRESS))
        card = Card.load(claimed_path)
        card.append_record(actor, milestones, when=when)
        card.save(claimed_path)

    @_locked
    def close(
        self,
        claimed_path: Path,
        *,
        actor: str,
        paths: list[Path],
        model: str | None = None,
        when: datetime | None = None,
    ) -> Path:
        _require_location(claimed_path, self.directory(BoardState.IN_PROGRESS))
        if not paths:
            raise InvalidTransition("A CARD cannot close with empty paths")
        resolved = [str(path.resolve()) for path in paths]
        if any(not Path(path).is_file() for path in resolved):
            raise InvalidTransition("Every closing path must name an existing file")
        moment = when or utc_now()
        card = Card.load(claimed_path)
        card.metadata["paths"] = resolved
        card.metadata["closed"] = format_timestamp(moment)
        if model:
            card.metadata["model"] = model
        card.append_record(actor, [f"Verified artifact: {path}" for path in resolved], when=moment)
        card.append_record(actor, ["CARD closed."], when=moment)
        card.save(claimed_path)
        destination = self.directory(BoardState.DONE) / claimed_path.name
        _rename_transition(claimed_path, destination)
        return destination

    @_locked
    def return_pending(
        self,
        claimed_path: Path,
        *,
        actor: str,
        reason: str,
        increment_attempts: bool = True,
        when: datetime | None = None,
    ) -> Path:
        _require_location(claimed_path, self.directory(BoardState.IN_PROGRESS))
        card = Card.load(claimed_path)
        if increment_attempts:
            card.metadata["attempts"] = card.attempts + 1
            # El tope cuenta vueltas gratis *seguidas*: un intento real lo reinicia (B-4).
            card.metadata.pop("free_releases", None)
        else:
            card.metadata["free_releases"] = _int_field(card, "free_releases") + 1
        _count_release(card)
        card.metadata.pop("agent", None)
        card.metadata.pop("claimed", None)
        card.append_record(actor, [reason], when=when)
        if not increment_attempts and _int_field(card, "free_releases") >= MAX_FREE_RELEASES:
            return self._block_loaded(
                claimed_path,
                card,
                actor=actor,
                reason=(
                    f"Released {MAX_FREE_RELEASES} times without being attempted; "
                    f"last reason: {reason}"
                ),
                when=when,
                code="repeatedly_released",
            )
        if card.attempts >= card.max_attempts:
            return self._block_loaded(
                claimed_path,
                card,
                actor=actor,
                reason=reason,
                when=when,
                code="attempts_exhausted",
            )
        card.save(claimed_path)
        destination = self.directory(BoardState.PENDING) / claimed_path.name
        _rename_transition(claimed_path, destination)
        _remove_claim_guard(self, claimed_path.name)
        return destination

    @_locked
    def block_pending(
        self,
        pending_path: Path,
        *,
        actor: str,
        reason: str,
        when: datetime | None = None,
        code: str = "unknown",
    ) -> Path:
        _require_location(pending_path, self.directory(BoardState.PENDING))
        return self._block_loaded(
            pending_path,
            Card.load(pending_path),
            actor=actor,
            reason=reason,
            when=when,
            code=code,
        )

    @_locked
    def unblock(
        self,
        filename: str,
        *,
        actor: str,
        reason: str,
        when: datetime | None = None,
    ) -> Path:
        name = _card_filename(filename)
        source = self.directory(BoardState.BLOCKED) / name
        card = Card.load(source)
        card.metadata.pop("blocked", None)
        milestones = [f"CARD unblocked: {reason}"]
        if card.attempts >= card.max_attempts:
            # Sin esto la tarjeta volvia a `pending` con los intentos agotados:
            # el despachador no la repartia nunca y el cliente la veia `queued`
            # para siempre (D-02). No se ponen los intentos a cero: el intento
            # forma parte de la clave de idempotencia del broker, y repetirla le
            # devolveria la tarea que ya fallo. Se amplia el presupuesto.
            # Siempre el presupuesto original, no el ya ampliado: si no, cada
            # desbloqueo lo duplicaba (3→6→12…, A-5 del arbitraje).
            budget = _int_field(card, "attempt_budget") or card.max_attempts
            card.metadata["attempt_budget"] = budget
            extended = card.attempts + budget
            milestones.append(
                f"Attempt budget extended from {card.max_attempts} to {extended}."
            )
            card.metadata["max_attempts"] = extended
        card.metadata.pop("free_releases", None)
        _count_release(card)
        card.append_record(actor, milestones, when=when)
        card.save(source)
        destination = self.directory(BoardState.PENDING) / name
        _rename_transition(source, destination)
        return destination

    @_locked
    def cancel(self, filename: str, *, actor: str, reason: str) -> Path:
        """Cancel a pending or claimed CARD into the durable archive."""
        name = _card_filename(filename)
        source = next(
            (
                self.directory(state) / name
                for state in (BoardState.PENDING, BoardState.IN_PROGRESS)
                if (self.directory(state) / name).is_file()
            ),
            None,
        )
        if source is None:
            raise InvalidTransition(f"Cancellable CARD not found: {name}")
        card = Card.load(source)
        card.metadata.pop("agent", None)
        card.metadata.pop("claimed", None)
        # El motivo se guarda junto a la marca de tiempo, no solo en la bitacora:
        # un cliente que pregunta por que se cancelo su trabajo no deberia tener
        # que leer prosa para averiguarlo.
        card.metadata["cancelled"] = {
            "at": format_timestamp(utc_now()),
            "reason": reason,
        }
        card.append_record(actor, [f"CARD cancelled: {reason}"])
        card.save(source)
        destination = self.directory(BoardState.ARCHIVE) / name
        _rename_transition(source, destination)
        _remove_claim_guard(self, name)
        return destination

    def _block_loaded(
        self,
        source: Path,
        card: Card,
        *,
        actor: str,
        reason: str,
        when: datetime | None,
        code: str = "unknown",
    ) -> Path:
        """Aparta la CARD y deja escrito **por que**, con un codigo y con prosa.

        El `code` se escribe aqui, en el momento de bloquear, y no se deduce
        despues leyendo el texto: quien bloquea es quien sabe el motivo, y
        adivinarlo a base de buscar palabras en una frase se rompe en cuanto
        alguien reescribe la frase.
        """
        moment = when or utc_now()
        card.metadata.pop("agent", None)
        card.metadata.pop("claimed", None)
        card.metadata["blocked"] = {
            "at": format_timestamp(moment),
            "reason": reason,
            "code": code,
        }
        card.append_record(actor, [f"CARD blocked: {reason}"], when=moment)
        card.save(source)
        destination = self.directory(BoardState.BLOCKED) / source.name
        _rename_transition(source, destination)
        return destination

    def recover_zombies(
        self,
        *,
        now: datetime | None = None,
        threshold: timedelta,
        actor: str = "dispatcher",
        dry_run: bool = False,
    ) -> tuple[RecoveryAction, ...]:
        """Devuelve a pendientes lo reclamado hace mas de `threshold`.

        Con `dry_run` informa de lo que rescataria y **no escribe nada**. Existe
        para que quien la llame no tenga que reimplementar el calculo de edad
        para su modo de prueba: esa copia acabaria divergiendo de esta, que es
        la que decide de verdad.
        """
        current = (now or utc_now()).astimezone(UTC)
        actions: list[RecoveryAction] = []
        for path in self.paths(BoardState.IN_PROGRESS):
            try:
                card = Card.load(path)
                claimed = _parse_timestamp(card.metadata.get("claimed"))
            except CardFormatError:
                continue
            if claimed is None:
                claimed = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if claimed is None or current - claimed <= threshold:
                continue
            attempts = card.attempts + 1
            if dry_run:
                # El mismo criterio que aplica `return_pending` al volver:
                # agotados los intentos, la CARD no vuelve a pendientes.
                actions.append(
                    RecoveryAction(
                        path.name,
                        (
                            BoardState.BLOCKED
                            if attempts >= card.max_attempts
                            else BoardState.PENDING
                        ),
                        attempts,
                        "zombie timeout",
                    )
                )
                continue
            destination = self.return_pending(
                path,
                actor=actor,
                reason=f"Zombie reclaimed after exceeding {threshold.total_seconds():g} seconds.",
                increment_attempts=True,
                when=current,
            )
            state = (
                BoardState.BLOCKED
                if destination.parent == self.directory(BoardState.BLOCKED)
                else BoardState.PENDING
            )
            actions.append(RecoveryAction(path.name, state, attempts, "zombie timeout"))
        return tuple(actions)


def _try_lock(stream: Any) -> bool:
    stream.seek(0)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(stream: Any) -> None:
    stream.seek(0)
    with suppress(OSError):
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _int_field(card: Card, field: str) -> int:
    value = card.metadata.get(field, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _count_release(card: Card) -> None:
    """Cuenta cada vuelta a `pending`; entra en la clave de idempotencia del runner.

    Un runner que suelta una tarjeta sin gastar intento (Core la deniega, el
    broker no puede cumplirla) y la vuelve a reclamar repetia la misma clave, y
    el API le devolvia la respuesta guardada del primer `claim` sin reclamar
    nada (D-03). Con este contador la clave cambia en cada ciclo.
    """
    previous = card.metadata.get("releases", 0)
    count = previous if isinstance(previous, int) and not isinstance(previous, bool) else 0
    card.metadata["releases"] = max(0, count) + 1


def _card_filename(filename: str) -> str:
    candidate = Path(filename)
    if candidate.name != filename or candidate.suffix.lower() != ".md" or filename in {".", ".."}:
        raise ValueError("CARD filename must be a plain .md filename")
    return filename


def _rename_claim(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ClaimConflict(f"CARD is already claimed: {source.name}")
    try:
        os.rename(source, destination)
    except (FileNotFoundError, FileExistsError, PermissionError, OSError) as exc:
        raise ClaimConflict(f"Atomic claim lost for {source.name}") from exc


def _rename_transition(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise InvalidTransition(f"Destination CARD already exists: {destination}")
    try:
        os.rename(source, destination)
    except OSError as exc:
        raise InvalidTransition(f"Cannot move CARD from {source} to {destination}") from exc


def _require_location(path: Path, directory: Path) -> None:
    if path.resolve().parent != directory.resolve():
        raise InvalidTransition(f"CARD is not in {directory.name}: {path}")


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _remove_claim_guard(board: Board, filename: str) -> None:
    guard = board.directory(BoardState.IN_PROGRESS) / ".claims" / f"{filename}.claim"
    with suppress(FileNotFoundError, OSError):
        os.rmdir(guard)
