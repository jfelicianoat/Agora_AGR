"""Atomic SKILL loader and the seam for Athena SkillManifest adaptation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from agora.contracts import CONTRACTS_DIRNAME, ContractError, ContractRegistry
from agora.documents import parse_markdown_document
from agora.errors import DocumentFormatError, SkillFormatError


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    version: str
    description: str
    modes: tuple[str, ...]
    metadata: dict[str, Any]
    instructions: str
    source: Path
    #: Contrato de salida que la skill promete, si promete alguno.
    output_contract: str | None = None
    output_contract_version: int | None = None
    output_schema: dict[str, Any] | None = None
    #: Un documento de ejemplo que cumple el esquema. Es lo que se le ensena al
    #: modelo: con el esquema crudo delante, devuelve el esquema en vez de una
    #: instancia suya. Se comprobo contra el broker real.
    output_example: dict[str, Any] | None = None

    @property
    def declares_output_contract(self) -> bool:
        """Si promete un documento **y** se sabe que forma tiene.

        Una cita sin resolver no cuenta: nombra un contrato pero todavia no
        puede exigirlo ni validarlo.
        """
        return self.output_schema is not None

    @property
    def cites_unresolved_contract(self) -> bool:
        """Cita un contrato del registro que aun no se le ha dado."""
        return self.output_contract is not None and self.output_schema is None

    @classmethod
    def load(cls, path: Path) -> Skill:
        try:
            metadata, body = parse_markdown_document(
                path.read_text(encoding="utf-8"), source=str(path)
            )
        except DocumentFormatError as exc:
            raise SkillFormatError(str(exc)) from exc
        for field in ("name", "version", "description"):
            if not isinstance(metadata.get(field), str) or not metadata[field].strip():
                raise SkillFormatError(f"{path}: {field} must be a non-empty string")
        modes_value = metadata.get("modes", ["conversation", "card"])
        if not isinstance(modes_value, list) or any(
            not isinstance(item, str) for item in modes_value
        ):
            raise SkillFormatError(f"{path}: modes must be a list of strings")
        contract, contract_version, schema = _output_contract(metadata, path)
        example = metadata.get("output_example")
        if example is not None and not isinstance(example, dict):
            raise SkillFormatError(f"{path}: output_example must be a mapping")
        if example is not None and contract is None:
            raise SkillFormatError(
                f"{path}: output_example needs the contract it is an example of"
            )
        return cls(
            name=metadata["name"].strip(),
            version=metadata["version"].strip(),
            description=metadata["description"].strip(),
            modes=tuple(item.strip() for item in modes_value if item.strip()),
            metadata=metadata,
            instructions=body.strip(),
            source=path,
            output_contract=contract,
            output_contract_version=contract_version,
            output_schema=schema,
            output_example=example,
        )


def _output_contract(
    metadata: dict[str, Any],
    path: Path,
) -> tuple[str | None, int | None, dict[str, Any] | None]:
    """Lee el contrato de salida que declara la skill, si declara alguno.

    Hay dos formas legitimas de declararlo:

    - **con el esquema dentro** (`output_contract` + `output_contract_version` +
      `output_schema`), que es como nacio y sigue valiendo para un contrato que
      no comparte nadie;
    - **citandolo** (`output_contract` + `output_contract_version`, sin
      esquema), que es lo que hace falta cuando varias skills prometen el mismo
      documento. La cita la resuelve el registro al cargar el perfil.

    Lo que no vale es un nombre sin version, una version sin nombre, o un
    esquema suelto: nada de eso se puede citar despues.
    """
    contract = metadata.get("output_contract")
    version = metadata.get("output_contract_version")
    schema = metadata.get("output_schema")
    declared = [item for item in (contract, version, schema) if item is not None]
    if not declared:
        return None, None, None
    if contract is None or version is None:
        raise SkillFormatError(
            f"{path}: un contrato de salida se declara al menos con "
            "output_contract y output_contract_version"
        )
    if not isinstance(contract, str) or not contract.strip():
        raise SkillFormatError(f"{path}: output_contract must be a non-empty string")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise SkillFormatError(f"{path}: output_contract_version must be a positive integer")
    if schema is not None and (not isinstance(schema, dict) or not schema):
        raise SkillFormatError(f"{path}: output_schema must be a non-empty mapping")
    return contract.strip(), version, schema


def load_profile_skills(
    profile_root: Path,
    names: tuple[str, ...],
    *,
    registry: ContractRegistry | None = None,
) -> tuple[Skill, ...]:
    """Las skills que declara un PROFILE, con sus contratos ya resueltos.

    Sin `registry` se busca uno junto a los PROFILE, igual que se hace con
    `models.yml`. Una skill que **cite** un contrato recibe asi su esquema y su
    ejemplo. Una cita que no se puede resolver es un error y no un contrato
    ausente: quedarse callado ahi significaria escribir el artefacto sin
    comprobar nada, que es justo lo que el contrato existe para impedir.

    Los contratos, como los PROFILE, **no se cachean**: son promesas publicas y
    se leen del disco cada vez, para que corregir una no exija reiniciar nada.
    """
    if registry is None:
        registry = ContractRegistry.discover(profile_root.parent)
    loaded: list[Skill] = []
    for name in names:
        path = profile_root / "skills" / name / "SKILL.md"
        if not path.is_file():
            raise SkillFormatError(f"Declared skill not found: {path}")
        skill = Skill.load(path)
        if "card" not in skill.modes and "scheduled" not in skill.modes:
            raise SkillFormatError(f"{path}: skill does not support card mode")
        if skill.cites_unresolved_contract:
            skill = _resolve_citation(skill, registry)
        loaded.append(skill)
    return tuple(loaded)


def _resolve_citation(skill: Skill, registry: ContractRegistry | None) -> Skill:
    reference = f"{skill.output_contract}@{skill.output_contract_version}"
    if registry is None:
        raise SkillFormatError(
            f"{skill.source}: cita el contrato {reference} y aqui no hay "
            f"registro de contratos ({CONTRACTS_DIRNAME} junto a los PROFILE)"
        )
    try:
        contract = registry.get(skill.output_contract, skill.output_contract_version)
    except ContractError as exc:
        raise SkillFormatError(f"{skill.source}: {exc}") from exc
    # El ejemplo propio manda sobre el del contrato: una skill especializada
    # puede querer ensenar un caso mas cercano a lo suyo sin cambiar la forma.
    return replace(
        skill,
        output_schema=contract.schema,
        output_example=skill.output_example or contract.example,
    )
