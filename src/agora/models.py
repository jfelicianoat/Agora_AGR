"""`models.yml`: traduce el `model_capacity` de un PROFILE a política de broker.

Hasta ahora `model_capacity` era metadato muerto —los PROFILE lo declaraban y
nadie lo leía—, así que **todos los perfiles compartían la política global del
runner**: el mismo presupuesto y el mismo determinismo para resumir una nota que
para redactar un informe. La especificación lo pide explícitamente.

El catálogo es un fichero del tablero, no código: cambiar qué modelo atiende a
los perfiles «maximum» no debería exigir tocar Python ni reiniciar nada más que
el runner.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from agora.broker.contracts import BrokerPolicy
from agora.errors import DocumentFormatError

CATALOG_FILENAME = "models.yml"

# Campos que una capacidad puede fijar. Se listan a propósito: un campo mal
# escrito en el YAML debe fallar al cargar, no ignorarse en silencio y dejar la
# tarjeta corriendo con una política que nadie pidió.
_TUNABLE = frozenset(
    {
        "determinism",
        "data_classification",
        "target_model",
        "max_cost_usd",
        "timeout_seconds",
        "zombie_timeout_seconds",
        "output_language",
        "auxiliary_invocations",
    }
)


class ModelCatalogError(DocumentFormatError):
    """El catálogo existe pero no dice algo que se pueda aplicar."""


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    """Política de ejecución por `model_capacity`, cargada de `models.yml`."""

    capacities: dict[str, dict[str, Any]]
    default: str
    source: Path | None = None

    @classmethod
    def load(cls, path: Path) -> ModelCatalog:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ModelCatalogError(f"{path}: invalid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ModelCatalogError(f"{path}: the catalog must be a mapping")
        capacities = raw.get("capacities")
        if not isinstance(capacities, dict) or not capacities:
            raise ModelCatalogError(f"{path}: 'capacities' must be a non-empty mapping")
        parsed: dict[str, dict[str, Any]] = {}
        for name, settings in capacities.items():
            if not isinstance(name, str) or not name.strip():
                raise ModelCatalogError(f"{path}: capacity names must be non-empty strings")
            if not isinstance(settings, dict):
                raise ModelCatalogError(f"{path}: capacity '{name}' must be a mapping")
            unknown = sorted(set(settings) - _TUNABLE)
            if unknown:
                raise ModelCatalogError(
                    f"{path}: capacity '{name}' sets unknown fields: {', '.join(unknown)}"
                )
            parsed[name.strip()] = dict(settings)
        default = raw.get("default")
        if not isinstance(default, str) or default not in parsed:
            raise ModelCatalogError(
                f"{path}: 'default' must name one of the declared capacities "
                f"({', '.join(sorted(parsed))})"
            )
        return cls(parsed, default, path)

    @classmethod
    def discover(cls, root: Path) -> ModelCatalog | None:
        """Carga `models.yml` de la raíz de PROFILEs si existe. Es opcional."""
        candidate = root / CATALOG_FILENAME
        return cls.load(candidate) if candidate.is_file() else None

    def policy_for(self, capacity: str | None, base: BrokerPolicy) -> BrokerPolicy:
        """Aplica la capacidad sobre la política base del runner.

        Una capacidad que el catálogo no declara es un error de contrato, no un
        motivo para caer al defecto: el PROFILE está pidiendo algo que este
        tablero no sabe servir.
        """
        name = (capacity or self.default).strip()
        if name not in self.capacities:
            raise ModelCatalogError(
                f"model_capacity '{name}' is not declared in "
                f"{self.source or CATALOG_FILENAME} ({', '.join(sorted(self.capacities))})"
            )
        settings = self.capacities[name]
        # `strict` fija modelo y `routed` prohíbe fijarlo: al cambiar de
        # determinismo hay que limpiar el modelo anterior o BrokerPolicy
        # rechazará la combinación con un error que no dice nada útil.
        if "determinism" in settings and settings["determinism"] != base.determinism:
            base = replace(base, target_model=None) if base.target_model else base
        try:
            return replace(base, **settings)
        except (TypeError, ValueError) as exc:
            raise ModelCatalogError(f"capacity '{name}' is not a valid policy: {exc}") from exc


def profile_capacity(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("model_capacity")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ModelCatalogError("model_capacity must be a non-empty string")
    return value.strip()
