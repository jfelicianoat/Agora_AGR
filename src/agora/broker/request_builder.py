"""Translate Atomic PROFILE + SKILL + CARD into an AI_Broker task request."""

from __future__ import annotations

import json
from typing import Any

from agora.broker.contracts import BrokerCapabilities, BrokerPolicy
from agora.cards import Card
from agora.documents import render_markdown_document
from agora.profiles import Profile
from agora.skills import Skill


def build_broker_request(
    profile: Profile,
    skills: tuple[Skill, ...],
    card: Card,
    *,
    policy: BrokerPolicy,
    idempotency_key: str,
    attachments: tuple[dict[str, Any], ...] = (),
    contract: BrokerCapabilities | None = None,
) -> dict[str, Any]:
    profile_contract = render_markdown_document(profile.metadata, profile.body).strip()
    skill_contracts = "\n\n".join(
        render_markdown_document(skill.metadata, skill.instructions).strip() for skill in skills
    )
    card_contract = card.serialize().strip()
    prompt = (
        "# Atomic PROFILE — capability contract\n\n"
        f"{profile_contract}\n\n"
        "# Declared SKILLS — procedural knowledge\n\n"
        f"{skill_contracts or 'No SKILL declared.'}\n\n"
        "# CARD — durable work order\n\n"
        f"{card_contract}\n\n"
        "Complete only this CARD. Return the requested deliverable; never invent missing inputs."
        f"{_output_instruction(policy, skills)}"
    )
    model_requirements: dict[str, Any] = {
        "fallback_allowed": policy.determinism == "routed",
        "max_cost_usd": policy.max_cost_usd,
    }
    if policy.target_model is not None:
        model_requirements["target_model"] = policy.target_model
        model_requirements["preferred_model"] = policy.target_model.get("model")
        model_requirements["allowed_providers"] = [policy.target_model.get("provider")]
    generation: dict[str, Any] = {
        "temperature": 0.0 if policy.determinism == "strict" else 0.3,
        "max_output_tokens": policy.max_output_tokens,
    }
    if policy.determinism == "strict":
        generation.update({"seed": policy.seed, "top_p": 1.0})
    request: dict[str, Any] = {
        "idempotency_key": idempotency_key,
        "request_id": f"agora:{card.source.name if card.source else idempotency_key}",
        "inference_kind": "chat",
        "content": {
            "prompt": prompt,
            "attachments": list(attachments),
            "metadata": {
                "source": "agora-atomic",
                "profile": f"{profile.name}@{profile.version}",
                "determinism": policy.determinism,
            },
        },
        "output": _output_section(policy),
        "generation": generation,
        "model_requirements": model_requirements,
        "execution": {
            "strategy": "single",
            "preset": "fast",
            "scheduling": "sequential",
            "timeout_seconds": policy.timeout_seconds,
        },
        "risk": {"data_classification": policy.data_classification},
        "priority": min(1000, max(0, card.priority_score)),
        "prompt_compression": "off",
        "exclude_from_model_learning": False,
    }
    # Opt-out explícito del sondeo en sombra (Client_API.md, 8.4). Sólo se envía
    # cuando hace falta y el broker lo anuncia: un campo desconocido en un broker
    # anterior es un 422, y una tarjeta `strict` ya está cubierta por la garantía
    # implícita (`target_model` + `fallback_allowed: false`).
    if (
        not policy.auxiliary_invocations_allowed
        and policy.determinism != "strict"
        and contract is not None
        and contract.auxiliary_invocations_optout
    ):
        request["auxiliary_invocations"] = False
    return request


def _output_instruction(policy: BrokerPolicy, skills: tuple[Skill, ...]) -> str:
    """El cierre del prompt cuando una SKILL promete una forma de salida.

    Aquí hay tres cosas aprendidas contra el AI_Broker real, no supuestas:

    1. el `json_schema` que viaja en `output` **no lo impone el broker**;
    2. con el esquema sólo dentro del contrato de la SKILL —perdido entre varios
       procedimientos largos— el modelo devolvía un CSV, o el documento anidado
       bajo una clave inventada;
    3. al poner el JSON Schema entero al final, devolvió **el esquema** en vez
       de una instancia suya.

    Lo que sí copia es un **ejemplo relleno**. El esquema sigue siendo lo que
    valida la respuesta antes de escribir el artefacto.
    """
    if policy.output_schema is None:
        return ""
    declaring = [skill for skill in skills if skill.declares_output_contract]
    named = (
        f" «{declaring[0].output_contract}» version "
        f"{declaring[0].output_contract_version}"
        if declaring
        else ""
    )
    heading = "\n\n# FORMATO OBLIGATORIO DE LA RESPUESTA\n\n"
    example = declaring[0].output_example if declaring else None
    if example is None:
        return (
            heading
            + "Responde EXCLUSIVAMENTE con un unico documento JSON del contrato"
            + f"{named}, tal y como lo describe `output_schema`. La raiz del "
            "documento es el objeto descrito: no lo envuelvas bajo ninguna "
            "clave, no devuelvas el esquema y no anadas texto alrededor."
        )
    rendered = json.dumps(example, ensure_ascii=False, indent=2)
    return (
        heading
        + "Responde EXCLUSIVAMENTE con un unico documento JSON del contrato"
        + f"{named}, con **esta misma forma** y tus propios datos:\n\n"
        + f"```\n{rendered}\n```\n\n"
        "Reglas, sin excepciones:\n\n"
        "- devuelve un documento **como ese**, no el esquema que lo describe;\n"
        "- la raiz es ese objeto: no lo envuelvas bajo ninguna clave;\n"
        "- usa exactamente esos nombres de campo, ni parecidos ni traducidos;\n"
        "- el JSON es la respuesta entera: sin texto antes ni despues, ni "
        "siquiera para atender una parte de la peticion que el contrato no "
        "recoja;\n"
        "- si un dato no se conoce, usa `null` donde el contrato lo permita en "
        "vez de inventarlo."
    )


def _output_section(policy: BrokerPolicy) -> dict[str, Any]:
    """El broker exige json_schema cuando el formato es json; sin él responde 422."""
    section: dict[str, Any] = {
        "format": policy.output_format,
        "language": policy.output_language,
    }
    # El esquema sólo viaja cuando el formato es json. Mandarlo con otro formato
    # no lo aplicaría nadie, y Agora lo usa por su cuenta para exigirlo en el
    # prompt y para validar la respuesta.
    if policy.output_format == "json" and policy.output_schema is not None:
        section["json_schema"] = policy.output_schema
    return section
