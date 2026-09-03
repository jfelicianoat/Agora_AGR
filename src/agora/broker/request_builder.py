"""Translate Atomic PROFILE + SKILL + CARD into an AI_Broker task request."""

from __future__ import annotations

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
        "max_output_tokens": 4000,
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


def _output_section(policy: BrokerPolicy) -> dict[str, Any]:
    """El broker exige json_schema cuando el formato es json; sin él responde 422."""
    section: dict[str, Any] = {
        "format": policy.output_format,
        "language": policy.output_language,
    }
    if policy.output_schema is not None:
        section["json_schema"] = policy.output_schema
    return section
