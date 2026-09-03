"""Versioned HTTP API for Agora runners and clients.

`agora.api.contracts` es el contrato de cable **compartido** entre el tablero y
el runner del PC IA: modelos de pydantic, sin servidor. `agora.api.app` es sólo
del tablero y arrastra FastAPI y uvicorn.

Por eso la exportación es perezosa: importar los contratos desde el runner no
puede exigir instalar el servidor entero. Sin esto, `pip install agora[runner]`
produce un runner que no arranca — comprobado instalándolo, no leyéndolo.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agora.api.app import ApiSettings, create_api

__all__ = ["ApiSettings", "create_api"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from agora.api import app

        return getattr(app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
