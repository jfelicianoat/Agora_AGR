"""La proyeccion de estado que ve un cliente, y por que no es la interna.

El tablero tiene seis estados que son carpetas: `pending`, `in-progress`,
`done`, `blocked`, `archive`, `scheduled`. Eso es la verdad de Agora y no se
toca. Pero obligar a un cliente a conocerlos significa dos cosas malas: que
tiene que aprenderse el sistema de ficheros de otro, y que el dia que Agora
anada un estado —o parta uno en dos— se rompe.

Aqui vive la traduccion, en un solo sitio, y **el estado interno viaja al lado**
para quien lo necesite.
"""

from __future__ import annotations

from typing import Any, Literal

from agora.board import BoardState

#: Lo que un cliente necesita distinguir para decidir que hacer.
ClientStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "blocked",
    "cancelled",
]

#: Codigos de error estables. Un cliente puede ramificar sobre ellos; el texto
#: que los acompana es para una persona y puede cambiar sin avisar.
ErrorCode = Literal[
    "attempts_exhausted",
    "untrusted_origin",
    "cancelled_by_client",
    "unknown",
]

_DIRECT: dict[BoardState, ClientStatus] = {
    BoardState.PENDING: "queued",
    BoardState.SCHEDULED: "queued",
    BoardState.IN_PROGRESS: "running",
    BoardState.DONE: "completed",
    BoardState.ARCHIVE: "cancelled",
}


def project(state: BoardState, metadata: dict[str, Any]) -> dict[str, Any]:
    """El estado de una CARD tal y como se le cuenta a un cliente.

    Dos decisiones que conviene tener escritas:

    **`scheduled` es `queued`.** Para el cliente no hay diferencia entre «aun no
    le toca» y «espera turno»: en los dos casos su trabajo no ha empezado y no
    hay nada que hacer. Que Agora distinga entre las dos es asunto de Agora.

    **`blocked` se parte en dos.** Una tarjeta que agoto sus intentos *fallo*, y
    eso es un desenlace del trabajo. Una parada por cualquier otro motivo —un
    origen que no es de confianza, un bloqueo administrativo— **no es un fallo
    del trabajo**: es que alguien tiene que mirarlo. Meter las dos cosas en la
    misma palabra le diria al cliente que su encargo salio mal cuando puede que
    ni siquiera se haya intentado.
    """
    if state is BoardState.BLOCKED:
        return _blocked(metadata)
    status = _DIRECT[state]
    projected: dict[str, Any] = {"status": status, "terminal": state.is_terminal}
    if status == "cancelled":
        projected["error"] = {
            "code": "cancelled_by_client",
            "message": _cancel_reason(metadata),
        }
    return projected


def _blocked(metadata: dict[str, Any]) -> dict[str, Any]:
    detail = metadata.get("blocked")
    detail = detail if isinstance(detail, dict) else {}
    code = detail.get("code")
    # Las CARD bloqueadas antes de que existiera el codigo no lo llevan. No se
    # adivina leyendo el texto: se dice que no se sabe, que es la verdad.
    if code not in {"attempts_exhausted", "untrusted_origin", "cancelled_by_client"}:
        code = "unknown"
    error: dict[str, Any] = {
        "code": code,
        "message": str(detail.get("reason") or "La tarjeta esta bloqueada."),
    }
    if code == "attempts_exhausted":
        error["attempts"] = metadata.get("attempts")
        error["max_attempts"] = metadata.get("max_attempts")
    return {
        "status": "failed" if code == "attempts_exhausted" else "blocked",
        # `blocked` no es terminal: una persona puede desbloquearla. Lo dice
        # `BoardState.is_terminal`, y aqui se respeta en vez de repetirse.
        "terminal": BoardState.BLOCKED.is_terminal,
        "error": error,
    }


def _cancel_reason(metadata: dict[str, Any]) -> str:
    cancelled = metadata.get("cancelled")
    if isinstance(cancelled, dict) and cancelled.get("reason"):
        return str(cancelled["reason"])
    return "La tarjeta se cancelo."
