"""De dónde saca el X-Admin-Token un proceso co-ubicado con AI_Broker.

Client_API.md, 3.1. El broker genera un token nuevo **en cada arranque** y, con
`server.publish_session_token: keyring`, lo deja en el almacén de credenciales
del sistema. Agora **lee** esa credencial; no la fabrica.

Esto sustituye a `BrokerSupervisor` como dueño del token en el despliegue real:
el broker lo arranca Windows (tarea programada, servicio o `.bat`), no Agora, y
después de un Wake-on-LAN el puerto ya está ocupado cuando el runner despierta.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from agora.broker.client import BrokerClient

# Los dos nombres son parte del contrato: Client_API.md, 3.1 promete que no
# cambian sin nota en la sección 12.
SESSION_KEYRING_SERVICE = "ai-broker"
SESSION_KEYRING_USERNAME = "session_admin_token"

# Entrada del operador para fijar un token estable. El broker la lee como
# reserva y nunca la escribe: pisarla convertiría un token de sesión en
# permanente, así que Agora no la toca.
OPERATOR_KEYRING_USERNAME = "dashboard_admin_token"


class SessionTokenUnavailable(RuntimeError):
    """No hay credencial local que leer."""


class SessionTokenSource(Protocol):
    def read(self) -> str: ...

    @property
    def origin(self) -> str: ...


def _keyring_reader(service: str, username: str) -> str | None:
    import keyring

    value: str | None = keyring.get_password(service, username)
    return value


@dataclass(slots=True)
class KeyringSessionToken:
    """Token de sesión publicado por el broker en el llavero del sistema."""

    service: str = SESSION_KEYRING_SERVICE
    username: str = SESSION_KEYRING_USERNAME
    reader: Callable[[str, str], str | None] = _keyring_reader

    def read(self) -> str:
        if self.username == OPERATOR_KEYRING_USERNAME:
            raise ValueError(
                "refusing to read the operator credential; "
                f"the session token lives in {SESSION_KEYRING_USERNAME}"
            )
        try:
            token = self.reader(self.service, self.username)
        except Exception as exc:  # el backend del llavero es opaco: keyring, WinCred…
            raise SessionTokenUnavailable(
                f"could not read {self.service}/{self.username}: {type(exc).__name__}: {exc}"
            ) from exc
        if not token:
            raise SessionTokenUnavailable(
                f"{self.service}/{self.username} is empty; is "
                "server.publish_session_token set to keyring in the broker?"
            )
        return token

    @property
    def origin(self) -> str:
        return f"keyring:{self.service}/{self.username}"


@dataclass(slots=True)
class EnvironmentSessionToken:
    """Token fijado por el operador en el entorno del propio runner."""

    variable: str = "AI_BROKER_ADMIN_TOKEN"

    def read(self) -> str:
        token = os.environ.get(self.variable)
        if not token:
            raise SessionTokenUnavailable(f"environment variable {self.variable} is empty")
        return token

    @property
    def origin(self) -> str:
        return f"env:{self.variable}"


@dataclass(slots=True)
class BrokerConnection:
    """Abre clientes contra un broker que ya está en marcha.

    Cada llamada relee la fuente: tras un reinicio del broker el token ha
    rotado, y ese es exactamente el caso que produce el 401/403 que dispara la
    renovación (Client_API.md, 3.1).
    """

    source: SessionTokenSource
    base_url: str = "http://127.0.0.1:8765"
    timeout: float = 30.0
    _factory: Callable[..., BrokerClient] = field(default=BrokerClient, repr=False)

    def connect(self) -> BrokerClient:
        return self._factory(self.base_url, self.source.read(), timeout=self.timeout)

    @property
    def origin(self) -> str:
        return self.source.origin
