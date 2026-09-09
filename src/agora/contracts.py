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

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from agora.errors import AgoraError
from agora.output_contract import validate_payload

CONTRACTS_DIRNAME = "CONTRACTS"
PUBLISHED_LOCK = "PUBLISHED.lock"

#: Vocabulario que pertenece al planificador del cliente y no a Agora.
#:
#: La frontera del proyecto es una sola frase: **Agora ejecuta trabajo de IA; el
#: cliente es dueno de sus tareas, su calendario, su disponibilidad y su
#: planificador**. Un contrato que trajera un campo `start_date` o `work_block`
#: la habria cruzado, y lo malo de cruzarla es que se cruza poco a poco: un
#: campo util aqui, otro alli, y un dia Agora es medio calendario y nadie sabe
#: quien decide una fecha.
#:
#: Se comprueba al cargar, y sobre los **nombres de campo**: describir por que
#: no se agenda es correcto; tener donde escribirlo, no.
#:
#: Palabras que, siendo una palabra entera del nombre, delatan una agenda.
#: `today_matters` no lo es, y por eso esto se compara por palabras y no por
#: subcadenas: la primera version lo rechazaba por llevar «day_» dentro.
CALENDAR_TOKENS: frozenset[str] = frozenset(
    {
        "date", "dates",
        "day", "days",
        "hour", "hours",
        "week", "weeks",
        "slot", "slots",
        "due",
        "start", "end", "when",
    }
)

#: Palabras que no pueden ser un falso positivo ni partidas por la mitad.
CALENDAR_WORDS: tuple[str, ...] = (
    "availability",
    "calendar",
    "deadline",
    "holiday",
    "pomodoro",
    "schedul",
    "timeblock",
    "vacation",
    "workblock",
)

CALENDAR_EXCEPTIONS: frozenset[str] = frozenset(
    {"estimated_minutes", "total_estimated_minutes", "duration_minutes", "effort_minutes"}
)


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


def fingerprint(schema: dict[str, Any]) -> str:
    """La huella de un esquema, estable frente al orden de las claves.

    Sirve para una sola cosa: comprobar que un contrato ya publicado no ha
    cambiado de forma. Se ordenan las claves porque reordenar un YAML no es un
    cambio de contrato, y se compacta porque el espaciado tampoco lo es.
    """
    canonical = json.dumps(schema, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_published_lock(root: Path) -> dict[str, str]:
    """Las huellas de los contratos publicados, por `nombre@version`."""
    path = root / PUBLISHED_LOCK
    if not path.is_file():
        return {}
    published: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) != 2:
            raise ContractError(f"{path}:{number}: se esperaba «referencia huella»")
        published[parts[0]] = parts[1]
    return published


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

    for where, field in _walk_field_names(schema, "raiz"):
        offending = _calendar_word(field)
        if offending:
            raise ContractError(
                f"{path}: {where} es un campo del planificador del cliente "
                f"(«{offending}»). Agora ejecuta trabajo de IA; las fechas, las "
                "horas y la disponibilidad son del cliente. Si hace falta hablar "
                "de esfuerzo, usa minutos estimados, que no son una agenda."
            )


def _calendar_word(field: str) -> str | None:
    """La palabra de calendario que lleva el nombre de un campo, si lleva alguna.

    Se compara **por palabras**, partiendo el nombre por guiones bajos y por los
    cambios de caja. Comparar por subcadena rechazaba `today_matters` por llevar
    «day» dentro, que es exactamente el tipo de falso positivo que hace que una
    guardia acabe desactivada.
    """
    lowered = field.lower()
    if lowered in CALENDAR_EXCEPTIONS:
        return None
    # Sin separadores, para que `work_block` y `workBlock` sean lo mismo que
    # `workblock`: una guardia que se salta poniendo un guion no guarda nada.
    squashed = re.sub(r"[^a-z]", "", lowered)
    offending = next((word for word in CALENDAR_WORDS if word in squashed), None)
    if offending:
        return offending
    tokens = {token for token in re.split(r"[_\s-]+|(?<=[a-z])(?=[A-Z])", field) if token}
    return next(
        (token.lower() for token in tokens if token.lower() in CALENDAR_TOKENS), None
    )


def _walk_field_names(node: Any, where: str):
    """Todos los nombres de campo del esquema, con el sitio donde estan."""
    if not isinstance(node, dict):
        return
    for field, child in (node.get("properties") or {}).items():
        here = f"{where}.{field}"
        yield here, str(field)
        yield from _walk_field_names(child, here)
    items = node.get("items")
    if isinstance(items, dict):
        yield from _walk_field_names(items, f"{where}[]")


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
