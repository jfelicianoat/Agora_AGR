"""Application services shared by desktop, CLI, and future transport adapters.

This module is intentionally free of Qt and networking.  It is the only surface the
desktop UI needs in F1, keeping filesystem ownership and domain transitions in the core.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from agora.board import Board, BoardState
from agora.cards import Card, format_timestamp, utc_now
from agora.dispatcher import DEFAULT_TRUSTED_ORIGINS, Dispatcher, DispatchOutcome, DispatchStatus
from agora.documents import atomic_write_text
from agora.errors import AgoraError, CardFormatError, ProfileFormatError
from agora.harnesses import DeterministicHarness, WorkerLauncher
from agora.profiles import Profile
from agora.skills import load_profile_skills


@dataclass(frozen=True, slots=True)
class CardSummary:
    state: BoardState
    filename: str
    function: str
    request: str
    priority: str
    attempts: int
    agent: str | None
    corrupt: bool = False


@dataclass(frozen=True, slots=True)
class CardDetail:
    summary: CardSummary
    metadata: dict[str, Any]
    body: str
    record: str
    source: Path


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    name: str
    version: str
    function: str
    description: str
    handles: tuple[str, ...]
    refuses: tuple[str, ...]
    skills: tuple[str, ...]
    #: Los contratos de salida que prometen sus skills, como `nombre@version`.
    #: Es lo que un cliente necesita para saber que documento va a recibir antes
    #: de mandar la primera tarjeta.
    produces: tuple[str, ...]
    harness: str | None
    source: Path


@dataclass(frozen=True, slots=True)
class VisibleError:
    source: str
    message: str


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    timestamp: str
    level: str
    message: str


@dataclass(frozen=True, slots=True)
class AgoraSnapshot:
    captured_at: str
    cards: tuple[CardSummary, ...]
    profiles: tuple[ProfileSummary, ...]
    errors: tuple[VisibleError, ...]
    activity: tuple[ActivityEntry, ...]
    dispatcher_status: str

    def cards_in(self, state: BoardState) -> tuple[CardSummary, ...]:
        return tuple(card for card in self.cards if card.state is state)


class AgoraApplication:
    """Qt-free façade over Agora's domain services."""

    def __init__(
        self,
        workspace: Path,
        *,
        board_name: str = "KANBAN",
        profiles_name: str = "AGENTS",
        launcher: WorkerLauncher | None = None,
        activity_limit: int = 200,
        trusted_origins: Iterable[str] = DEFAULT_TRUSTED_ORIGINS,
    ) -> None:
        self.workspace = workspace.resolve()
        self.board = Board(self.workspace / board_name)
        self.profiles_root = self.workspace / profiles_name
        self.launcher = launcher or DeterministicHarness(self.workspace)
        self._activity: deque[ActivityEntry] = deque(maxlen=max(1, activity_limit))
        # Quien puede depositar trabajo. `trust_origin` lo amplia cuando el API
        # arranca con un principal propio: una tarjeta que llego autenticada por
        # HTTPS no puede quedarse fuera por no llamarse «human».
        self._configured_origins: tuple[str, ...] = tuple(
            dict.fromkeys(item.strip().lower() for item in trusted_origins if item.strip())
        )
        self._dispatcher_status = "idle"
        #: Lo que la ultima ronda bloqueo o no pudo hacer, para la pestaña Errores.
        self._dispatch_errors: tuple[VisibleError, ...] = ()

    @property
    def _trusted_origins_file(self) -> Path:
        return self.workspace / ".agora" / "trusted-origins.json"

    @property
    def trusted_origins(self) -> tuple[str, ...]:
        """Los configurados mas los que el API de este workspace ya autentico.

        Se lee del disco en cada consulta porque el escritorio y el API son
        **procesos distintos** sobre el mismo tablero. Cuando la confianza vivia
        solo en la memoria del API, la ventana bloqueaba por «Untrusted origin»
        las tarjetas que el propio API habia aceptado con 201 (AG-01, auditoria
        del 14-oct; `smoke-20260908.md` en produccion). El fichero no es mas
        falsificable que el campo `origin`: quien escribe en el workspace ya
        puede poner `origin: human`.
        """
        return tuple(dict.fromkeys((*self._configured_origins, *self._persisted_origins())))

    def trust_origin(self, origin: str) -> None:
        """Anade un origen de confianza. Idempotente y sin orden significativo."""
        value = origin.strip().lower()
        if not value or value in self.trusted_origins:
            return
        persisted = self._persisted_origins()
        target = self._trusted_origins_file
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            target, json.dumps(sorted({*persisted, value}), ensure_ascii=False, indent=2) + "\n"
        )

    def _persisted_origins(self) -> tuple[str, ...]:
        """Los origenes registrados por el API; vacio si no hay fichero o no se lee.

        Ilegible cierra en vez de abrir: se vuelve a los configurados, y
        `snapshot` lo cuenta como error visible.
        """
        try:
            raw = json.loads(self._trusted_origins_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ()
        if not isinstance(raw, list):
            return ()
        return tuple(item.strip().lower() for item in raw if isinstance(item, str) and item.strip())

    def _trust_file_errors(self) -> tuple[VisibleError, ...]:
        target = self._trusted_origins_file
        if not target.exists():
            return ()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return (VisibleError(str(target), f"No se pudo leer: {exc}"),)
        if not isinstance(raw, list):
            return (VisibleError(str(target), "Tiene que ser una lista de orígenes."),)
        return ()

    def initialize(self) -> None:
        self.board.initialize()
        self._log("info", f"Tablero listo en {self.board.root} (id {self.instance_id})")

    @property
    def instance_id(self) -> str:
        """Identidad estable de este tablero.

        Entra en la clave de idempotencia que Agora manda al broker. Sin ella,
        dos tableros con una tarjeta del mismo nombre —el de producción y un
        ensayo, por ejemplo— chocan en el broker con `IDEMPOTENCY_CONFLICT`, y
        el segundo no puede ejecutar nada. Observado ejecutándolo, no deducido.
        """
        marker = self.workspace / ".agora-instance"
        if marker.is_file():
            existing = marker.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        generated = uuid4().hex
        marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(marker, f"{generated}\n")
        return generated

    def snapshot(self) -> AgoraSnapshot:
        cards, card_errors = self._card_census()
        profiles, profile_errors = self._profile_census()
        return AgoraSnapshot(
            captured_at=format_timestamp(utc_now()),
            cards=cards,
            profiles=profiles,
            errors=card_errors + profile_errors + self._trust_file_errors() + self._dispatch_errors,
            activity=tuple(self._activity),
            dispatcher_status=self._dispatcher_status,
        )

    def card_detail(self, state: BoardState, filename: str) -> CardDetail:
        name = _plain_card_name(filename)
        path = self.board.directory(state) / name
        card = Card.load(path)
        return CardDetail(
            summary=_card_summary(state, path, card),
            metadata=dict(card.metadata),
            body=card.body,
            record=_record_section(card.body),
            source=path.resolve(),
        )

    def dispatch_once(self, *, dry_run: bool = False) -> tuple[DispatchOutcome, ...]:
        self._dispatcher_status = "simulating" if dry_run else "running"
        self._log("info", "Ronda simulada iniciada" if dry_run else "Ronda iniciada")
        try:
            outcomes = Dispatcher(
                self.board,
                self.profiles_root,
                self.launcher,
                trusted_origins=self.trusted_origins,
            ).run_once(dry_run=dry_run)
        except (AgoraError, OSError) as exc:
            self._dispatcher_status = "error"
            self._log("error", f"La ronda falló: {type(exc).__name__}: {exc}")
            raise
        for outcome in outcomes:
            profile = f" con {outcome.profile}" if outcome.profile else ""
            self._log(
                "error" if outcome.status == "error" else "info",
                f"{outcome.card}: {_OUTCOME_LABELS[outcome.status]}{profile} — {outcome.reason}",
            )
        # Lo que la ronda movio a Bloqueadas o no pudo hacer. Antes solo quedaba
        # en Actividad y en el Record, y la pestaña Errores decia «Sin errores»
        # justo despues de un bloqueo (AG-02). Se sustituye en cada ronda real.
        if not dry_run:
            self._dispatch_errors = tuple(
                VisibleError(
                    f"Ronda · {outcome.card}",
                    f"{_OUTCOME_LABELS[outcome.status]}: {outcome.reason}",
                )
                for outcome in outcomes
                if outcome.status is DispatchStatus.ERROR
                or (outcome.status is DispatchStatus.BLOCKED and outcome.final_path is not None)
            )
        self._dispatcher_status = "idle"
        return outcomes

    def unblock(self, filename: str, reason: str) -> Path:
        """Devuelve a `pending` una tarjeta bloqueada (U-01).

        Antes sólo se podía con `POST /admin/blocked/.../unblock` y el token de
        administración: desde la ventana, una tarjeta bloqueada era un callejón.
        """
        motivo = _required_reason(reason)
        destination = self.board.unblock(_plain_card_name(filename), actor="human", reason=motivo)
        self._log("info", f"{filename}: desbloqueada — {motivo}")
        return destination

    def cancel(self, filename: str, reason: str) -> Path:
        """Archiva como cancelada una tarjeta pendiente o en curso (U-01)."""
        motivo = _required_reason(reason)
        destination = self.board.cancel(_plain_card_name(filename), actor="human", reason=motivo)
        self._log("info", f"{filename}: cancelada — {motivo}")
        return destination

    def artifacts(self, state: BoardState, filename: str) -> tuple[Path, ...]:
        """Los resultados de la tarjeta que existen en disco **dentro del workspace** (U-02).

        `paths` lo escribe quien cierra la tarjeta; una ruta fuera del workspace
        no se ofrece nunca, como tampoco la sirve el API (A-3 del arbitraje).
        """
        paths = self.card_detail(state, filename).metadata.get("paths")
        if not isinstance(paths, list):
            return ()
        found: list[Path] = []
        for item in paths:
            path = Path(str(item)).resolve()
            if path.is_relative_to(self.workspace) and path.is_file():
                found.append(path)
        return tuple(found)

    @staticmethod
    def open_target(path: Path) -> Path:
        """Qué abrir para enseñar un resultado: el fichero si es un documento, si no su carpeta.

        Un runner puede subir artefactos con cualquier extensión; abrir un `.exe`
        o un `.bat` con la aplicación asociada sería ejecutarlo (A-3).
        """
        return path if path.suffix.lower() in _DOCUMENT_SUFFIXES else path.parent

    def explain(self, filename: str) -> str:
        """Por qué una tarjeta pendiente avanza o no, en español (U-04).

        Es la misma ronda simulada que hace el botón «Simular»: no escribe nada
        y no hay un segundo cálculo que pueda divergir del despachador.
        """
        name = _plain_card_name(filename)
        outcomes = Dispatcher(
            self.board,
            self.profiles_root,
            self.launcher,
            trusted_origins=self.trusted_origins,
            max_dispatches_per_round=1_000_000,
        ).run_once(dry_run=True)
        outcome = next((item for item in outcomes if item.card == name), None)
        if outcome is None:
            return "—"
        return _EXPLANATIONS.get(outcome.status, _OUTCOME_LABELS[outcome.status]) + (
            f" ({outcome.reason})" if outcome.reason else ""
        )

    def _card_census(self) -> tuple[tuple[CardSummary, ...], tuple[VisibleError, ...]]:
        cards: list[CardSummary] = []
        errors: list[VisibleError] = []
        for state in BoardState:
            for path in self.board.paths(state):
                try:
                    card = Card.load(path)
                except (CardFormatError, OSError) as exc:
                    cards.append(
                        CardSummary(state, path.name, "—", "CARD unreadable", "—", 0, None, True)
                    )
                    errors.append(VisibleError(str(path.resolve()), str(exc)))
                    continue
                cards.append(_card_summary(state, path, card))
        return tuple(cards), tuple(errors)

    def _profile_census(
        self,
    ) -> tuple[tuple[ProfileSummary, ...], tuple[VisibleError, ...]]:
        profiles: list[ProfileSummary] = []
        errors: list[VisibleError] = []
        if not self.profiles_root.exists():
            return (), ()
        for path in sorted(self.profiles_root.rglob("PROFILE.md")):
            try:
                profile = Profile.load(path)
            except (ProfileFormatError, OSError) as exc:
                errors.append(VisibleError(str(path.resolve()), str(exc)))
                continue
            profiles.append(
                ProfileSummary(
                    profile.name,
                    profile.version,
                    profile.function,
                    profile.description,
                    profile.handles,
                    profile.refuses,
                    profile.skills,
                    self._produces(profile, errors),
                    profile.harness,
                    path.resolve(),
                )
            )
        return tuple(profiles), tuple(errors)

    def _produces(
        self, profile: Profile, errors: list[VisibleError]
    ) -> tuple[str, ...]:
        """Los contratos que prometen las skills del perfil, sin repetir.

        Un perfil cuyas skills no cargan sigue apareciendo en el censo: el
        cliente tiene que poder ver que existe y que algo va mal en el, en vez
        de que desaparezca del listado sin explicacion.
        """
        try:
            skills = load_profile_skills(profile.source.parent, profile.skills)
        except (AgoraError, OSError) as exc:
            errors.append(VisibleError(str(profile.source), str(exc)))
            return ()
        seen: list[str] = []
        for skill in skills:
            if skill.output_contract is None:
                continue
            reference = f"{skill.output_contract}@{skill.output_contract_version}"
            if reference not in seen:
                seen.append(reference)
        return tuple(seen)

    def _log(self, level: str, message: str, *, when: datetime | None = None) -> None:
        self._activity.append(
            ActivityEntry(format_timestamp(when or utc_now()), level.lower(), message)
        )


#: Como se cuenta cada desenlace en la ventana. Los motivos que acompañan van en
#: inglés porque son los mismos que viajan por el API (idioma del contrato).
_OUTCOME_LABELS: dict[DispatchStatus, str] = {
    DispatchStatus.DISPATCHED: "despachada",
    DispatchStatus.WOULD_DISPATCH: "se despacharía",
    DispatchStatus.WAITING_INPUT: "esperando entradas",
    DispatchStatus.UNMATCHED: "sin perfil compatible",
    DispatchStatus.AMBIGUOUS: "ambigua",
    DispatchStatus.BLOCKED: "bloqueada",
    DispatchStatus.SKIPPED: "omitida",
    DispatchStatus.CONFLICT: "conflicto",
    DispatchStatus.ERROR: "error",
    DispatchStatus.RECOVERED: "rescatada",
    DispatchStatus.MATERIALIZED: "programada creada",
}


#: Extensiones que «Abrir resultado» abre directamente; el resto abre la carpeta.
_DOCUMENT_SUFFIXES = frozenset(
    {".md", ".txt", ".json", ".csv", ".pdf", ".html", ".htm", ".yml", ".yaml", ".log",
     ".png", ".jpg", ".jpeg", ".svg", ".xml", ".docx", ".xlsx"}
)

#: Lo que significa cada desenlace para quien mira una tarjeta pendiente.
_EXPLANATIONS: dict[DispatchStatus, str] = {
    DispatchStatus.WOULD_DISPATCH: "Lista: la ejecutará la próxima ronda de esta ventana",
    DispatchStatus.SKIPPED: (
        "En cola para otro ejecutor: si es de IA la recoge el runner del PC IA "
        "cuando el tablero (Agora Tablero) está en marcha"
    ),
    DispatchStatus.WAITING_INPUT: "Parada: faltan ficheros de entrada",
    DispatchStatus.UNMATCHED: "Parada: ningún perfil acepta esta petición",
    DispatchStatus.AMBIGUOUS: "Parada: dos perfiles la aceptan por igual",
    DispatchStatus.BLOCKED: "Parada: no se repartirá",
    DispatchStatus.ERROR: "Error al leerla",
}


def _required_reason(reason: str) -> str:
    motivo = reason.strip()
    if not motivo:
        raise ValueError("Hace falta un motivo: queda escrito en el Record de la tarjeta.")
    return motivo


def _card_summary(state: BoardState, path: Path, card: Card) -> CardSummary:
    priority = card.metadata.get("priority", "normal")
    agent = card.metadata.get("agent")
    return CardSummary(
        state=state,
        filename=path.name,
        function=card.function,
        request=card.request,
        priority=str(priority),
        attempts=card.attempts,
        agent=agent.strip() if isinstance(agent, str) and agent.strip() else None,
    )


def _record_section(body: str) -> str:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() == "## record":
            return "\n".join(lines[index + 1 :]).strip()
    return ""


def _plain_card_name(filename: str) -> str:
    candidate = Path(filename)
    if candidate.name != filename or candidate.suffix.lower() != ".md":
        raise ValueError("CARD filename must be a plain .md filename")
    return filename
