"""El registro de contratos: un esquema, en un sitio, con su version.

Lo que se protege aqui son dos cosas. Que las reglas de la casa se comprueben
**una sola vez** para todos los contratos, en vez de repetirse a mano en las
pruebas de cada skill. Y que un contrato mal escrito se rechace al cargarlo,
con un mensaje que diga que pasa, en lugar de reventar mas tarde delante de una
respuesta del modelo.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from agora.contracts import CONTRACTS_DIRNAME, ContractError, ContractRegistry
from agora.errors import SkillFormatError
from agora.output_contract import validate_payload
from agora.profiles import Profile
from agora.skills import Skill, load_profile_skills

PROFILES_ROOT = Path(__file__).resolve().parents[1] / "profiles" / "task-planning"

BUENO: dict[str, Any] = {
    "name": "ejemplo",
    "version": 1,
    "description": "Un contrato de prueba.",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["contract", "contract_version", "items"],
        "properties": {
            "contract": {"const": "ejemplo"},
            "contract_version": {"const": 1},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["titulo"],
                    "properties": {"titulo": {"type": "string", "minLength": 1}},
                },
            },
        },
    },
    "example": {
        "contract": "ejemplo",
        "contract_version": 1,
        "items": [{"titulo": "algo"}],
    },
}


def escribir(root: Path, doc: dict[str, Any], name: str = "ejemplo.v1.yml") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def doc() -> dict[str, Any]:
    return copy.deepcopy(BUENO)


@pytest.fixture(scope="module")
def registry() -> ContractRegistry:
    found = ContractRegistry.discover(PROFILES_ROOT)
    assert found is not None
    return found


# --- Lo que Agora ofrece hoy -------------------------------------------------


def test_the_catalogue_answers_what_agora_produces(registry: ContractRegistry) -> None:
    """La pregunta que hara el cliente en A07: que documentos hay y en que version."""
    assert [c.reference for c in registry.catalogue()] == [
        "plan-advice@1",
        "review-analysis@1",
        "task-brief@1",
        "work-breakdown@1",
    ]


def test_every_contract_explains_itself(registry: ContractRegistry) -> None:
    for contract in registry.catalogue():
        assert contract.description.strip(), contract.reference


def test_the_three_named_by_the_phase_are_there(registry: ContractRegistry) -> None:
    """TaskBrief, TaskDecomposition y ReviewAnalysis, que es lo que pedia A06."""
    for name in ("task-brief", "work-breakdown", "review-analysis"):
        assert registry.get(name, 1).name == name


def test_asking_for_a_version_that_does_not_exist_says_which_do(
    registry: ContractRegistry,
) -> None:
    with pytest.raises(ContractError) as error:
        registry.get("task-brief", 99)
    assert "task-brief@1" in str(error.value)


# --- Las reglas de la casa, comprobadas una sola vez -------------------------


def test_every_contract_closes_its_root(registry: ContractRegistry) -> None:
    for contract in registry.catalogue():
        assert contract.schema["additionalProperties"] is False, contract.reference


def test_every_contract_says_inside_what_it_is(registry: ContractRegistry) -> None:
    for contract in registry.catalogue():
        properties = contract.schema["properties"]
        assert properties["contract"]["const"] == contract.name
        assert properties["contract_version"]["const"] == contract.version
        assert "contract" in contract.schema["required"]
        assert "contract_version" in contract.schema["required"]


def test_every_contract_example_is_valid(registry: ContractRegistry) -> None:
    for contract in registry.catalogue():
        assert validate_payload(contract.schema, contract.example) == [], contract.reference


# --- Payloads validos --------------------------------------------------------


def test_a_well_formed_contract_loads(tmp_path: Path, doc: dict[str, Any]) -> None:
    escribir(tmp_path, doc)
    loaded = ContractRegistry.load(tmp_path)
    assert loaded.get("ejemplo", 1).reference == "ejemplo@1"


def test_the_description_is_optional(tmp_path: Path, doc: dict[str, Any]) -> None:
    del doc["description"]
    escribir(tmp_path, doc)
    assert ContractRegistry.load(tmp_path).get("ejemplo", 1).description == ""


def test_two_versions_of_the_same_contract_live_together(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    """Versionar sirve justo para esto: la v1 sigue viva mientras nace la v2."""
    escribir(tmp_path, doc)
    dos = copy.deepcopy(doc)
    dos["version"] = 2
    dos["schema"]["properties"]["contract_version"]["const"] = 2
    dos["example"]["contract_version"] = 2
    escribir(tmp_path, dos, "ejemplo.v2.yml")

    loaded = ContractRegistry.load(tmp_path)
    assert [c.reference for c in loaded.catalogue()] == ["ejemplo@1", "ejemplo@2"]


# --- Payloads invalidos, con el error delante --------------------------------


@pytest.mark.parametrize(
    ("cambio", "mensaje"),
    [
        pytest.param({"name": ""}, "name", id="sin-nombre"),
        pytest.param({"version": 0}, "positive integer", id="version-cero"),
        pytest.param({"version": "uno"}, "positive integer", id="version-texto"),
        pytest.param({"schema": {}}, "schema", id="esquema-vacio"),
        pytest.param({"example": {}}, "example", id="ejemplo-vacio"),
        pytest.param({"descripcion": "x"}, "unknown fields", id="campo-inventado"),
    ],
)
def test_a_malformed_contract_file_is_rejected(
    tmp_path: Path, doc: dict[str, Any], cambio: dict[str, Any], mensaje: str
) -> None:
    doc.update(cambio)
    escribir(tmp_path, doc)
    with pytest.raises(ContractError, match=mensaje):
        ContractRegistry.load(tmp_path)


def test_an_open_root_is_rejected(tmp_path: Path, doc: dict[str, Any]) -> None:
    """Sin cerrar la raiz, el modelo cuela un campo y nadie se entera."""
    del doc["schema"]["additionalProperties"]
    escribir(tmp_path, doc)
    with pytest.raises(ContractError, match="additionalProperties"):
        ContractRegistry.load(tmp_path)


def test_an_open_nested_object_is_rejected(tmp_path: Path, doc: dict[str, Any]) -> None:
    del doc["schema"]["properties"]["items"]["items"]["additionalProperties"]
    escribir(tmp_path, doc)
    with pytest.raises(ContractError, match=r"items\[\]"):
        ContractRegistry.load(tmp_path)


def test_a_contract_that_lies_about_its_own_name_is_rejected(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    doc["schema"]["properties"]["contract"]["const"] = "otro"
    escribir(tmp_path, doc)
    with pytest.raises(ContractError, match="'contract' vale"):
        ContractRegistry.load(tmp_path)


def test_a_contract_that_lies_about_its_version_is_rejected(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    doc["schema"]["properties"]["contract_version"]["const"] = 7
    escribir(tmp_path, doc)
    with pytest.raises(ContractError, match="'contract_version' vale"):
        ContractRegistry.load(tmp_path)


def test_a_contract_without_a_version_inside_is_rejected(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    del doc["schema"]["properties"]["contract_version"]
    escribir(tmp_path, doc)
    with pytest.raises(ContractError, match="contract_version"):
        ContractRegistry.load(tmp_path)


def test_a_version_not_pinned_with_const_is_rejected(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    doc["schema"]["properties"]["contract_version"] = {"type": "integer"}
    escribir(tmp_path, doc)
    with pytest.raises(ContractError, match="const"):
        ContractRegistry.load(tmp_path)


def test_an_example_that_breaks_its_own_schema_is_rejected(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    """El modelo copia el ejemplo. Un ejemplo malo enseña a incumplir."""
    doc["example"]["items"] = [{"titulo": "algo", "colado": 1}]
    escribir(tmp_path, doc)
    with pytest.raises(ContractError, match="el ejemplo no cumple"):
        ContractRegistry.load(tmp_path)


def test_the_same_contract_declared_twice_is_rejected(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    escribir(tmp_path, doc, "uno.yml")
    escribir(tmp_path, doc, "dos.yml")
    with pytest.raises(ContractError, match="ya está declarado"):
        ContractRegistry.load(tmp_path)


def test_broken_yaml_says_so(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "roto.yml").write_text("name: [sin cerrar\n", encoding="utf-8")
    with pytest.raises(ContractError, match="invalid YAML"):
        ContractRegistry.load(tmp_path)


def test_an_empty_directory_is_rejected(tmp_path: Path) -> None:
    (tmp_path / CONTRACTS_DIRNAME).mkdir()
    with pytest.raises(ContractError, match="no contract files"):
        ContractRegistry.load(tmp_path / CONTRACTS_DIRNAME)


# --- Las citas de las skills -------------------------------------------------


def test_a_citation_gets_the_schema_and_the_example() -> None:
    root = PROFILES_ROOT / "review-analyzer"
    skill = load_profile_skills(root, ("analyze-review",))[0]
    assert skill.output_schema is not None
    assert skill.output_example is not None
    assert skill.declares_output_contract is True


def test_a_skill_that_only_cites_is_not_a_declared_contract_yet(tmp_path: Path) -> None:
    """Cargada suelta, la cita esta sin resolver: promete pero no puede exigir."""
    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\nname: cita\nversion: 1.0.0\ndescription: cita algo\n"
        "output_contract: ejemplo\noutput_contract_version: 1\n---\n\ncuerpo\n",
        encoding="utf-8",
    )
    skill = Skill.load(path)
    assert skill.cites_unresolved_contract is True
    assert skill.declares_output_contract is False


def test_a_citation_with_no_registry_is_an_error_not_a_silence(tmp_path: Path) -> None:
    """Callarse aqui seria escribir el artefacto sin comprobar nada."""
    skills = tmp_path / "perfil" / "skills" / "cita"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: cita\nversion: 1.0.0\ndescription: cita algo\nmodes:\n  - card\n"
        "output_contract: ejemplo\noutput_contract_version: 1\n---\n\ncuerpo\n",
        encoding="utf-8",
    )
    with pytest.raises(SkillFormatError, match="no hay registro"):
        load_profile_skills(tmp_path / "perfil", ("cita",))


def test_citing_a_contract_nobody_declared_is_an_error(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    escribir(tmp_path / CONTRACTS_DIRNAME, doc)
    skills = tmp_path / "perfil" / "skills" / "cita"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: cita\nversion: 1.0.0\ndescription: cita algo\nmodes:\n  - card\n"
        "output_contract: inexistente\noutput_contract_version: 1\n---\n\ncuerpo\n",
        encoding="utf-8",
    )
    with pytest.raises(SkillFormatError, match="contrato desconocido"):
        load_profile_skills(tmp_path / "perfil", ("cita",))


def test_a_skill_may_show_its_own_example_of_a_shared_contract(
    tmp_path: Path, doc: dict[str, Any]
) -> None:
    """La forma es del contrato; el ejemplo puede acercarse a lo suyo."""
    escribir(tmp_path / CONTRACTS_DIRNAME, doc)
    skills = tmp_path / "perfil" / "skills" / "cita"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(
        "---\nname: cita\nversion: 1.0.0\ndescription: cita algo\nmodes:\n  - card\n"
        "output_contract: ejemplo\noutput_contract_version: 1\n"
        "output_example:\n"
        "  contract: ejemplo\n"
        "  contract_version: 1\n"
        "  items:\n"
        "    - titulo: uno propio\n"
        "---\n\ncuerpo\n",
        encoding="utf-8",
    )
    skill = load_profile_skills(tmp_path / "perfil", ("cita",))[0]
    assert skill.output_example["items"][0]["titulo"] == "uno propio"
    assert skill.output_schema == doc["schema"]


def test_no_skill_keeps_a_schema_of_its_own_any_more() -> None:
    """Un esquema duplicado en un front matter es una segunda fuente de verdad."""
    for path in sorted(PROFILES_ROOT.rglob("SKILL.md")):
        metadata = Skill.load(path).metadata
        assert "output_schema" not in metadata, path


def test_the_contracts_directory_is_not_mistaken_for_a_profile() -> None:
    from agora.profiles import load_profiles

    names = {profile.name for profile in load_profiles(PROFILES_ROOT)}
    assert CONTRACTS_DIRNAME not in names
    assert "review-analyzer" in names
