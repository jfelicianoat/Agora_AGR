"""Hacer cumplir el contrato de salida que promete una SKILL.

El AI_Broker acepta `output.format: json` y un `json_schema`, pero **no los
impone**: se comprobó contra el broker real (contrato 2.10) y devolvió un bloque
Markdown con un JSON que no seguía el esquema. Si Agora se limitara a escribir
lo que llegue, una tarjeta terminaría en `done` con un artefacto que el cliente
no puede leer, y eso es peor que fallar.

Así que la promesa se comprueba aquí:

1. se **extrae** el JSON aunque venga envuelto en un bloque de código;
2. se **valida** contra el esquema declarado;
3. si no cumple, la tarjeta falla y se reintenta, que es el comportamiento
   durable de siempre.

El validador cubre el subconjunto de JSON Schema que usan estos contratos, no
JSON Schema entero. Es deliberado: una dependencia más para validar seis
palabras clave no se paga sola. Lo que no entiende, lo ignora en vez de
inventarse un veredicto.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCED = re.compile(r"```(?:json)?\s*(?P<payload>[\[{].*?[\]}])\s*```", re.DOTALL)

_TYPE_CHECKS: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}


class OutputContractError(ValueError):
    """La respuesta no cumple el contrato que la skill prometió."""


def extract_json(text: str) -> Any:
    """El documento JSON que haya en la respuesta, venga como venga.

    Los modelos envuelven el JSON en un bloque de código con muchísima
    frecuencia, y algunos lo preceden de una frase. Rechazar eso sería exigir
    una disciplina que no se cumple; extraerlo es trivial y honesto.
    """
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    match = _FENCED.search(stripped)
    if match:
        try:
            return json.loads(match.group("payload"))
        except json.JSONDecodeError as exc:
            raise OutputContractError(
                f"el bloque de codigo no contiene JSON valido: {exc}"
            ) from exc

    # Ultimo intento: el primer objeto o lista equilibrada del texto.
    for opening, closing in (("{", "}"), ("[", "]")):
        start = stripped.find(opening)
        end = stripped.rfind(closing)
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                continue

    raise OutputContractError("la respuesta no contiene ningun documento JSON")


def validate_payload(schema: dict[str, Any], payload: Any, *, path: str = "") -> list[str]:
    """Problemas del documento frente al esquema. Vacío significa que cumple."""
    problems: list[str] = []
    where = path or "raiz"

    expected = schema.get("type")
    if expected is not None and not _matches_type(expected, payload):
        return [f"{where}: se esperaba {expected} y llego {type(payload).__name__}"]

    if "const" in schema and payload != schema["const"]:
        problems.append(f"{where}: deberia ser {schema['const']!r} y es {payload!r}")
    if "enum" in schema and payload not in schema["enum"]:
        problems.append(f"{where}: {payload!r} no esta entre {schema['enum']}")

    if isinstance(payload, dict):
        problems.extend(_validate_object(schema, payload, where))
    elif isinstance(payload, list):
        problems.extend(_validate_array(schema, payload, where))
    elif isinstance(payload, str):
        minimum = schema.get("minLength")
        if isinstance(minimum, int) and len(payload) < minimum:
            problems.append(f"{where}: texto mas corto de {minimum} caracteres")
    elif isinstance(payload, (int, float)) and not isinstance(payload, bool):
        problems.extend(_validate_number(schema, payload, where))

    return problems


def enforce(schema: dict[str, Any], text: str) -> Any:
    """Extrae y valida. Lanza `OutputContractError` con todo lo que falle."""
    payload = extract_json(text)
    problems = validate_payload(schema, payload)
    if problems:
        raise OutputContractError("; ".join(problems))
    return payload


def _validate_object(schema: dict[str, Any], payload: dict, where: str) -> list[str]:
    problems: list[str] = []
    properties = schema.get("properties", {})
    for field in schema.get("required", ()):
        if field not in payload:
            problems.append(f"{where}: falta el campo requerido '{field}'")
    if schema.get("additionalProperties") is False and isinstance(properties, dict):
        extra = sorted(set(payload) - set(properties))
        if extra:
            problems.append(f"{where}: campos no declarados: {', '.join(extra)}")
    if isinstance(properties, dict):
        for field, rule in properties.items():
            if field in payload and isinstance(rule, dict):
                problems.extend(
                    validate_payload(rule, payload[field], path=_join(where, field))
                )
    return problems


def _validate_array(schema: dict[str, Any], payload: list, where: str) -> list[str]:
    problems: list[str] = []
    minimum = schema.get("minItems")
    if isinstance(minimum, int) and len(payload) < minimum:
        problems.append(f"{where}: hacen falta al menos {minimum} elementos")
    rule = schema.get("items")
    if isinstance(rule, dict):
        for index, item in enumerate(payload):
            problems.extend(validate_payload(rule, item, path=f"{where}[{index}]"))
    return problems


def _validate_number(schema: dict[str, Any], payload: float, where: str) -> list[str]:
    problems: list[str] = []
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, (int, float)) and payload < minimum:
        problems.append(f"{where}: {payload} es menor que {minimum}")
    if isinstance(maximum, (int, float)) and payload > maximum:
        problems.append(f"{where}: {payload} es mayor que {maximum}")
    return problems


def _matches_type(expected: Any, payload: Any) -> bool:
    """`type` admite un nombre o una lista de nombres, como en JSON Schema."""
    names = expected if isinstance(expected, list) else [expected]
    for name in names:
        if name == "null":
            if payload is None:
                return True
            continue
        checker = _TYPE_CHECKS.get(name)
        if checker is None:
            # Un tipo que este validador no conoce no se rechaza: se ignora.
            return True
        if name in ("number", "integer") and isinstance(payload, bool):
            continue
        if isinstance(payload, checker):
            return True
    return False


def _join(where: str, field: str) -> str:
    return field if where == "raiz" else f"{where}.{field}"
