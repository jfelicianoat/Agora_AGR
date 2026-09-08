"""Registro de contratos de salida: un esquema, en un sitio, con su versión.

Hasta ahora cada SKILL llevaba su `output_schema` dentro del front matter. Eso
funcionó mientras hubo un contrato por perfil, y se rompió en cuanto hubo
varios que prometían el mismo documento: las tres skills especializadas de
descomposición decían producir `work-breakdown` v1 y **no declaraban nada**.
Funcionaban de milagro, porque el ejecutor manda todas las skills del perfil y
una hermana sí lo declaraba. Separadas, el contrato habría desaparecido sin que
nadie se enterase.

Un contrato es una promesa pública: hay que poder enumerarla, citarla por
nombre y versión, y comprobarla una sola vez. Por eso vive en su propio fichero.

Los esquemas siguen dentro de las SKILL cuando no se comparten. Esto no obliga
a nadie a migrar: una skill con `output_schema` propio se comporta igual que
antes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agora.errors import AgoraError
from agora.output_contract import validate_payload

CONTRACTS_DIRNAME = "CONTRACTS"


class ContractError(AgoraError):
    """Un contrato de salida no cumple las reglas de la casa."""


@dataclass(frozen=True, slots=True)
class OutputContract:
    """Un documento que Agora promete producir, con su forma y su versión."""

    name: str
    version: int
    schema: dict[str, Any]
    example: dict[str, Any]
    description: str = ""
    source: Path | None = None

    @property
    def reference(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True, slots=True)
class ContractRegistry:
    """Lo que Agora sabe producir, indexado por nombre y versión."""

    contracts: dict[tuple[str, int], OutputContract]
    source: Path | None = None

    @classmethod
    def load(cls, root: Path) -> ContractRegistry:
        """Carga `<root>/*.yml`. Cada fichero declara **un** contrato."""
        if not root.is_dir():
            raise ContractError(f"{root}: contract directory not found")
        contracts: dict[tuple[str, int], OutputContract] = {}
        for path in sorted(root.glob("*.yml")) + sorted(root.glob("*.yaml")):
            contract = _load_one(path)
            key = (contract.name, contract.version)
            if key in contracts:
                raise ContractError(
                    f"{path}: {contract.reference} ya está declarado en "
                    f"{contracts[key].source}"
                )
            contracts[key] = contract
        if not contracts:
            raise ContractError(f"{root}: no contract files found")
        return cls(contracts=contracts, source=root)

    @classmethod
    def discover(cls, profiles_root: Path) -> ContractRegistry | None:
        """El registro que haya junto a los PROFILE, o ninguno.

        Que no haya es legítimo: un despliegue cuyas skills llevan su esquema
        dentro no necesita registro, y se comporta como siempre.
        """
        candidate = profiles_root / CONTRACTS_DIRNAME
        return cls.load(candidate) if candidate.is_dir() else None

    def get(self, name: str, version: int) -> OutputContract:
        try:
            return self.contracts[(name, version)]
        except KeyError:
            known = ", ".join(sorted(c.reference for c in self.contracts.values()))
            raise ContractError(
                f"contrato desconocido {name}@{version}; declarados: {known or 'ninguno'}"
            ) from None

    def catalogue(self) -> tuple[OutputContract, ...]:
        """Todo lo declarado, en orden estable. Es la respuesta a «qué ofreces»."""
        return tuple(
            sorted(self.contracts.values(), key=lambda c: (c.name, c.version))
        )


def _load_one(path: Path) -> OutputContract:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ContractError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ContractError(f"{path}: a contract file must be a mapping")

    unknown = set(raw) - {"name", "version", "description", "schema", "example"}
    if unknown:
        raise ContractError(f"{path}: unknown fields: {', '.join(sorted(unknown))}")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ContractError(f"{path}: name must be a non-empty string")
    name = name.strip()

    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ContractError(f"{path}: version must be a positive integer")

    schema = raw.get("schema")
    if not isinstance(schema, dict) or not schema:
        raise ContractError(f"{path}: schema must be a non-empty mapping")

    example = raw.get("example")
    if not isinstance(example, dict) or not example:
        raise ContractError(f"{path}: example must be a non-empty mapping")

    description = raw.get("description", "")
    if not isinstance(description, str):
        raise ContractError(f"{path}: description must be a string")

    _check_house_rules(name, version, schema, path)

    problems = validate_payload(schema, example)
    if problems:
        raise ContractError(f"{path}: el ejemplo no cumple su propio esquema: " + "; ".join(problems))

    return OutputContract(
        name=name,
        version=version,
        schema=schema,
        example=example,
        description=description.strip(),
        source=path,
    )


def _check_house_rules(
    name: str,
    version: int,
    schema: dict[str, Any],
    path: Path,
) -> None:
    """Lo que todo contrato de Agora cumple, comprobado una sola vez.

    No son gustos. Cada regla evita un fallo que ya ha ocurrido:

    - sin `additionalProperties: false`, un modelo cuela un campo que nadie
      espera y el consumidor lo ignora en silencio;
    - sin `contract` y `contract_version` dentro del propio documento, quien lo
      recibe no puede saber qué está leyendo;
    - si esos valores no coinciden con el nombre del fichero, el documento
      miente sobre sí mismo.
    """
    if schema.get("type") != "object":
        raise ContractError(f"{path}: la raiz del contrato tiene que ser un objeto")
    if schema.get("additionalProperties") is not False:
        raise ContractError(
            f"{path}: la raiz necesita additionalProperties: false; sin eso el "
            "documento acepta campos que nadie ha declarado"
        )

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        raise ContractError(f"{path}: el contrato necesita properties")

    required = schema.get("required")
    if not isinstance(required, list):
        raise ContractError(f"{path}: el contrato necesita required")

    for field, expected in (("contract", name), ("contract_version", version)):
        if field not in required:
            raise ContractError(f"{path}: '{field}' tiene que estar en required")
        declared = properties.get(field)
        if not isinstance(declared, dict) or "const" not in declared:
            raise ContractError(
                f"{path}: '{field}' tiene que fijarse con const para que el "
                "documento diga lo que es"
            )
        if declared["const"] != expected:
            raise ContractError(
                f"{path}: '{field}' vale {declared['const']!r} y el contrato "
                f"declara {expected!r}"
            )

    for where, node in _walk_objects(schema, "raiz"):
        if node.get("additionalProperties") is not False:
            raise ContractError(
                f"{path}: {where} necesita additionalProperties: false"
            )


def _walk_objects(schema: dict[str, Any], where: str):
    """Los objetos anidados del esquema, con el sitio donde están."""
    for field, node in (schema.get("properties") or {}).items():
        if not isinstance(node, dict):
            continue
        here = f"{where}.{field}"
        if node.get("type") == "object":
            yield here, node
            yield from _walk_objects(node, here)
        items = node.get("items")
        if node.get("type") == "array" and isinstance(items, dict):
            if items.get("type") == "object":
                yield f"{here}[]", items
                yield from _walk_objects(items, f"{here}[]")
