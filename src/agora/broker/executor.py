"""Atomic contract execution through AI_Broker with auditable telemetry."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agora.api.contracts import WorkItem
from agora.broker.client import BrokerApiError, BrokerClient
from agora.broker.contracts import (
    BrokerArtifact,
    BrokerCapabilities,
    BrokerInvocation,
    BrokerPolicy,
    BrokerTaskState,
)
from agora.broker.request_builder import build_broker_request
from agora.broker.review_gate import ReviewGate, audit_milestone
from agora.cards import Card
from agora.models import ModelCatalog, profile_capacity
from agora.output_contract import OutputContractError, enforce
from agora.profiles import Profile, load_profiles
from agora.skills import Skill, load_profile_skills


class BrokerTaskFailed(RuntimeError):
    def __init__(self, state: BrokerTaskState) -> None:
        super().__init__(f"AI_Broker task {state.task_id} ended as {state.status}: {state.error}")
        self.state = state


# Margen para un documento con contrato. Medido, no elegido: el corte real
# ocurrio en 4000 con un documento que necesitaba algo mas de la mitad de esto.
CONTRACT_OUTPUT_TOKENS = 8000


def apply_skill_output_contract(
    policy: BrokerPolicy,
    skills: tuple[Skill, ...],
) -> BrokerPolicy:
    """Impone el contrato de salida que declare una skill.

    Si ninguna lo declara, la politica no cambia: el comportamiento anterior se
    conserva intacto. Si lo declaran dos, es un error de contrato y no una
    eleccion silenciosa: no hay forma de saber cual manda.
    """
    declaring = [skill for skill in skills if skill.declares_output_contract]
    if not declaring:
        return policy
    first = declaring[0]
    for other in declaring[1:]:
        # Varias skills del mismo perfil pueden compartir contrato: una generica
        # y sus especializaciones producen el mismo documento. Lo que no puede
        # haber son dos formas distintas, porque no habria manera de saber cual
        # manda y el consumidor recibiria algo que no espera.
        if (
            other.output_contract != first.output_contract
            or other.output_contract_version != first.output_contract_version
            or other.output_schema != first.output_schema
        ):
            raise ValueError(
                "skills declare conflicting output contracts: "
                f"{first.name} promises {first.output_contract}"
                f"@{first.output_contract_version} and {other.name} promises "
                f"{other.output_contract}@{other.output_contract_version}"
            )
    # Deliberadamente **no** se cambia `output_format`. Se comprobo contra el
    # broker real (2.10) y salieron dos cosas:
    #
    # 1. con `output.format: json` + `json_schema`, el broker **no impone** el
    #    esquema: devolvio un CSV y, otra vez, un JSON con otra forma;
    # 2. peor aun, al enrutar a lmstudio el proveedor rechaza la peticion con
    #    `'response_format.type' must be 'json_schema' or 'text'` y un error
    #    marcado como **no reintentable**, asi que la tarjeta muere.
    #
    # Pedir JSON por ahi no da garantia y si rompe segun a quien enrute. El
    # esquema se conserva en la politica para dos cosas que si funcionan:
    # exigirlo al final del prompt y validar la respuesta antes de escribirla.
    # Y se sube el tope de salida. Un documento con contrato tiene secciones
    # fijas y es estructuralmente mas largo que la prosa equivalente: con los
    # 4000 de siempre, un consejo sobre cuatro encargos salio cortado a media
    # cadena, JSON valido hasta el corte e invalido despues. Se respeta un tope
    # mayor si ya venia puesto; nadie baja lo que otro subio a proposito.
    return replace(
        policy,
        output_schema=first.output_schema,
        max_output_tokens=max(policy.max_output_tokens, CONTRACT_OUTPUT_TOKENS),
    )


def _enforce_output_contract(
    artifacts: dict[str, bytes],
    deliverable: dict[str, Any],
    policy: BrokerPolicy,
) -> dict[str, bytes]:
    """Comprueba que el entregable cumple el contrato que prometio la skill.

    El broker acepta el `json_schema` pero no lo impone: se verifico contra el
    broker real y devolvio un bloque Markdown con un JSON que no seguia el
    esquema. Escribir eso como artefacto dejaria la tarjeta en `done` con algo
    que el cliente no puede leer, asi que aqui se falla y la tarjeta se
    reintenta, que es el comportamiento durable de siempre.

    De paso, el artefacto se normaliza a JSON limpio: quien lo consuma no tiene
    que desenvolver bloques de codigo.
    """
    if policy.output_schema is None:
        return artifacts
    name = deliverable.get("name")
    if not isinstance(name, str) or name not in artifacts:
        return artifacts
    try:
        payload = enforce(policy.output_schema, artifacts[name].decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise BrokerPolicyViolation(
            f"el entregable prometia JSON y no es texto legible: {exc}"
        ) from exc
    except OutputContractError as exc:
        raise BrokerPolicyViolation(
            f"el entregable no cumple el contrato de salida declarado: {exc}"
        ) from exc
    normalized = dict(artifacts)
    normalized[name] = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    deliverable["sha256"] = hashlib.sha256(normalized[name]).hexdigest()
    return normalized


class BrokerPolicyViolation(RuntimeError):
    """The effective broker execution did not satisfy the requested policy."""


@dataclass(frozen=True, slots=True)
class BrokerExecution:
    task_id: str | None
    artifacts: dict[str, bytes]
    model: str | None
    audit: dict[str, Any]
    milestones: tuple[str, ...]


@dataclass(slots=True)
class BrokerExecutor:
    client: BrokerClient
    profiles_root: Path
    policy: BrokerPolicy
    # Traduce el `model_capacity` del PROFILE a política. Sin catálogo, todos
    # los perfiles comparten `policy`, que es como se comportaba antes.
    catalog: ModelCatalog | None = None
    review_gate: ReviewGate = field(default_factory=ReviewGate)

    def policy_for(self, profile: Profile) -> BrokerPolicy:
        if self.catalog is None:
            return self.policy
        return self.catalog.policy_for(profile_capacity(profile.metadata), self.policy)

    def policy_for_profile_name(self, name: str) -> BrokerPolicy:
        """Política de un perfil por nombre, para decisiones previas a ejecutar.

        Un perfil desconocido no puede decidir nada, así que manda la política
        base: el error real saldrá en `_contracts`, con su mensaje.
        """
        if self.catalog is None:
            return self.policy
        matches = [item for item in load_profiles(self.profiles_root) if item.name == name]
        return self.policy_for(matches[0]) if len(matches) == 1 else self.policy

    def prepare_attachments(
        self,
        paths: tuple[Path, ...],
        *,
        wait_seconds: float,
        uploaded: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Sube los adjuntos y espera a que el broker los convierta.

        Un adjunto que tarda en convertirse devuelve `waiting_attachment`, y el
        runner vuelve a sondear. `uploaded` memoriza el `file_id` por digest para
        no reenviar los bytes en cada vuelta. No hay `file_id` huérfanos que
        limpiar: el broker deduplica por SHA-256 y devuelve el mismo
        identificador con `created: false` (comprobado en vivo el 2026-09-04).
        """
        attachments: list[dict[str, Any]] = []
        for path in paths:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            known = uploaded.get(digest) if uploaded is not None else None
            file_id = known if known is not None else self.client.upload_file(path).file_id
            if uploaded is not None:
                uploaded[digest] = file_id
            ready = self.client.wait_file_ready(
                file_id,
                timeout_seconds=wait_seconds,
            )
            attachments.append(
                {
                    "type": "broker_file",
                    "name": ready.filename,
                    "uri": f"broker://files/{ready.file_id}",
                    "metadata": {"file_id": ready.file_id},
                }
            )
        return tuple(attachments)

    def execute(
        self,
        work: WorkItem,
        attachments: tuple[dict[str, Any], ...],
        *,
        checkpoint: Callable[[str, str], None],
        review_checkpoint: Callable[[dict[str, Any]], None] | None = None,
    ) -> BrokerExecution:
        card, profile = self._contracts(work)
        policy = self.policy_for(profile)
        contract = self.client.contract()
        # Rechazar aquí lo que el broker no puede prometer, no al leer la
        # telemetría cuando el contenido ya lo ha visto otro modelo.
        contract.ensure_supports(policy)
        key = broker_idempotency_key(work)
        skills = load_profile_skills(profile.source.parent, profile.skills)
        # Una skill que promete una forma de salida la impone de verdad: sin
        # esto el esquema seria solo texto en el prompt y el broker no lo
        # validaria. Se aplica despues de `ensure_supports` porque no cambia
        # nada que el contrato del broker tenga que prometer.
        policy = apply_skill_output_contract(policy, skills)
        gate_audit = self.review_gate.evaluate(
            self.client, card, profile, policy, contract,
            has_attachments=bool(attachments or work.inputs),
        )
        if gate_audit is not None:
            if review_checkpoint is not None:
                review_checkpoint(gate_audit)
            if gate_audit["reviewer_skipped"]:
                return _autoapproved_review(gate_audit, policy)
        payload = build_broker_request(
            profile,
            skills,
            card,
            policy=policy,
            idempotency_key=key,
            attachments=attachments,
            contract=contract,
        )
        task_id = self.client.submit(payload)
        checkpoint(task_id, key)
        if gate_audit is not None:
            self.review_gate.reviewer_submitted(gate_audit, task_id)
            if review_checkpoint is not None:
                review_checkpoint(gate_audit)
        state = self.client.wait_task(task_id, timeout_seconds=policy.timeout_seconds)
        return self.finalize(state, contract=contract, policy=policy, gate_audit=gate_audit)

    def resume(self, work: WorkItem, task_id: str) -> BrokerExecution:
        card, profile = self._contracts(work)
        policy = self.policy_for(profile)
        gate_audit = card.metadata.get("review_gate")
        if isinstance(gate_audit, dict) and gate_audit.get("enabled") is True:
            skills = load_profile_skills(profile.source.parent, profile.skills)
            policy = apply_skill_output_contract(policy, skills)
        state = self.client.wait_task(task_id, timeout_seconds=policy.timeout_seconds)
        return self.finalize(
            state, policy=policy,
            gate_audit=gate_audit if isinstance(gate_audit, dict) else None,
        )

    def finalize(
        self,
        state: BrokerTaskState,
        *,
        contract: BrokerCapabilities | None = None,
        policy: BrokerPolicy | None = None,
        gate_audit: dict[str, Any] | None = None,
    ) -> BrokerExecution:
        if state.status != "completed":
            raise BrokerTaskFailed(state)
        effective_policy = policy if policy is not None else self.policy
        effective = contract if contract is not None else self.client.contract()
        invocations = self.client.invocations(state.task_id)
        billable = _contract_invocations(invocations)
        determinism: dict[str, Any] | None = None
        if effective_policy.determinism == "strict":
            determinism = _validate_strict(state, invocations, effective_policy)
        compression = _validate_prompt_compression(billable, effective)
        artifacts, deliverable = self._collect_artifacts(state, effective, effective_policy)
        artifacts = _enforce_output_contract(artifacts, deliverable, effective_policy)
        audit = _audit(
            state, invocations, effective_policy, compression, deliverable, determinism
        )
        if gate_audit is not None:
            self.review_gate.reviewer_completed(gate_audit, state.task_id, invoked=bool(billable))
            self.review_gate.compare_shadow(
                gate_audit, artifacts[deliverable["name"]], state.task_id
            )
            audit["review_gate"] = gate_audit
        model = _model_label(audit.get("served_by"))
        milestones: tuple[str, ...] = (
            f"AI_Broker task completed: {state.task_id}.",
            f"Effective model: {model or 'not reported'}.",
            f"Broker invocations: {len(billable)}; cost USD: {audit['total_cost_usd']:.8f}.",
            f"Determinism policy: {effective_policy.determinism}; "
            f"prompt compression: {compression['verdict']}.",
            f"Deliverable: {deliverable['name']} via {deliverable['source']}"
            + (f"; sha256 {deliverable['sha256']}." if deliverable.get("sha256") else "."),
        )
        if determinism and determinism["deviations"]:
            # No invalida la tarjeta, pero el Record no puede callarlo.
            roles = ", ".join(item["role"] for item in determinism["deviations"])
            milestones += (
                f"Contractual invocations with their own generation parameters: {roles}.",
            )
        if gate_audit is not None:
            milestones += (audit_milestone(gate_audit),)
        return BrokerExecution(state.task_id, artifacts, model, audit, milestones)

    def _collect_artifacts(
        self,
        state: BrokerTaskState,
        contract: BrokerCapabilities,
        policy: BrokerPolicy,
    ) -> tuple[dict[str, bytes], dict[str, Any]]:
        """Recoge el entregable por la vía canónica y lo que lo acompaña.

        Client_API.md, 8.3: `/artifacts` viene tipado y con `sha256`; `result` no
        tiene esquema y no lo tendrá. Sin `canonical_artifacts` no hay forma de
        saber cuál de la lista es el entregable, así que se cae a `result`.
        """
        if not contract.canonical_artifacts:
            payload = _result_bytes(state.result)
            suffix = "json" if policy.output_format == "json" else "md"
            name = f"broker-result.{suffix}"
            return (
                {name: payload},
                {
                    "name": name,
                    "source": "result",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "reason": "broker does not announce canonical_artifacts",
                },
            )
        listed = self.client.artifacts(state.task_id)
        final = [item for item in listed if item.final and item.available]
        if len(final) != 1:
            raise BrokerApiError(
                500,
                f"a completed task must expose exactly one final artifact; found {len(final)}",
            )
        entregable = final[0]
        artifacts = {entregable.filename: self._download(state.task_id, entregable)}
        # Lo demás acompaña: imágenes y salidas de `run_code` que hoy se perdían.
        for item in listed:
            if item.final or not item.available or item.filename in artifacts:
                continue
            artifacts[item.filename] = self._download(state.task_id, item)
        return (
            artifacts,
            {
                "name": entregable.filename,
                "source": "artifacts",
                "artifact_id": entregable.artifact_id,
                "artifact_type": entregable.artifact_type,
                "media_type": entregable.media_type,
                "sha256": entregable.sha256,
                "companions": [name for name in artifacts if name != entregable.filename],
            },
        )

    def _download(self, task_id: str, artifact: BrokerArtifact) -> bytes:
        payload = self.client.download_artifact(task_id, artifact.artifact_id)
        if artifact.sha256:
            digest = hashlib.sha256(payload).hexdigest()
            if digest.lower() != artifact.sha256.lower():
                raise BrokerApiError(
                    500,
                    f"artifact {artifact.artifact_id} sha256 mismatch: "
                    f"declared {artifact.sha256}, downloaded {digest}",
                )
        return payload

    def _contracts(self, work: WorkItem) -> tuple[Card, Profile]:
        card = Card.parse(work.card_document, source=f"remote CARD {work.filename}")
        matches = [
            profile for profile in load_profiles(self.profiles_root) if profile.name == work.profile
        ]
        if len(matches) != 1:
            raise ValueError(f"exactly one local PROFILE is required for {work.profile}")
        profile = matches[0]
        if profile.function.strip().lower() != card.function.strip().lower():
            raise ValueError("local PROFILE function differs from CARD")
        return card, profile


def _autoapproved_review(gate_audit: dict[str, Any], policy: BrokerPolicy) -> BrokerExecution:
    """A recorded approval, with no invented Broker task or generative invocation."""
    document: dict[str, Any] = {"approved": True}
    if policy.output_schema is None:
        document["review_gate"] = gate_audit
    payload = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    name = "review-approval.json"
    audit: dict[str, Any] = {
        "task_id": None,
        "served_by": None,
        "total_cost_usd": None,  # System-1 does not report cost.
        "invocations": [],
        "review_gate": gate_audit,
        "deliverable": {
            "name": name, "source": "review_gate",
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    }
    artifacts = _enforce_output_contract({name: payload}, audit["deliverable"], policy)
    return BrokerExecution(None, artifacts, None, audit, (audit_milestone(gate_audit),))


def broker_idempotency_key(work: WorkItem) -> str:
    """Clave estable por (tablero, tarjeta, intento, perfil).

    El `board_id` es imprescindible: sin él, dos tableros con una tarjeta del
    mismo nombre producen la misma clave y el broker rechaza el segundo con
    `IDEMPOTENCY_CONFLICT`, sin poder ejecutar nada. Pasó al ensayar la
    instalación contra el broker real, con una tarjeta `task.md` que ya existía
    en otro tablero.
    """
    raw = f"agora\0{work.board_id}\0{work.filename}\0{work.attempts}\0{work.profile}"
    return "agora:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# AI_Broker 2.9 materializa el entregable bajo estas claves: app/coordinator.py
# escribe `result_markdown` y `assistant_content` en todas las estrategias. El resto
# se tolera sólo por compatibilidad futura; ninguna es la que el broker emite hoy.
_RESULT_KEYS = ("result_markdown", "assistant_content", "content", "text", "output", "answer")

# Reserva para brokers anteriores al 2.10, que no marcan `contractual`. El
# vocabulario real tiene catorce roles y crece, así que nombrar los propios del
# broker es frágil por definición: en 2.10 esta lista no se usa.
#
# `confidence_judge` NO está aquí: es trabajo de la tarjeta —hereda
# `model_requirements` y se factura— y apartarlo infravaloraba el coste
# (Client_API.md, 8.1). Hoy sólo `shadow_probe` es no contractual.
LEGACY_NON_CONTRACT_ROLES = frozenset({"shadow_probe"})


def _is_contractual(item: BrokerInvocation) -> bool:
    if item.contractual is not None:
        return item.contractual
    return item.role not in LEGACY_NON_CONTRACT_ROLES


def _contract_invocations(
    invocations: tuple[BrokerInvocation, ...],
) -> tuple[BrokerInvocation, ...]:
    return tuple(item for item in invocations if _is_contractual(item))


def _result_bytes(result: dict[str, Any] | None) -> bytes:
    if not result:
        raise BrokerApiError(500, "completed task has no result")
    for key in _RESULT_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value:
            return value.encode("utf-8")
    # Fallar de forma visible: cerrar la CARD con un volcado JSON del sobre del
    # broker disfrazaría de entregable algo que no lo es.
    raise BrokerApiError(
        500, "completed task has no textual deliverable in " + ", ".join(_RESULT_KEYS)
    )


def _validate_prompt_compression(
    billable: tuple[BrokerInvocation, ...],
    contract: BrokerCapabilities,
) -> dict[str, Any]:
    """El trabajo atómico exige prompt sin podar, y ahora se puede demostrar.

    Client_API.md, 8.5: la aserción es sobre las invocaciones contractuales.
    Sin `prompt_compression_echo` el broker no acusa recibo, así que se registra
    como no verificable en vez de afirmar que se cumplió.
    """
    if not contract.prompt_compression_echo:
        return {
            "requested": "off",
            "verdict": "unverifiable",
            "detail": "broker does not announce prompt_compression_echo",
        }
    observed: list[str] = []
    for item in billable:
        echo = item.prompt_compression
        if not isinstance(echo, dict):
            # `null` en llamadas que no envían prompt de usuario: nada que podar.
            continue
        effective = echo.get("effective")
        if effective is None:
            continue
        observed.append(str(effective))
        if effective != "off":
            raise BrokerPolicyViolation(
                f"invocation {item.invocation_id} ({item.role}) was compressed "
                f"with '{effective}' after requesting 'off'"
            )
    return {
        "requested": "off",
        "verdict": "verified" if observed else "not_reported",
        "effective": sorted(set(observed)),
    }


def _audit(
    state: BrokerTaskState,
    invocations: tuple[BrokerInvocation, ...],
    policy: BrokerPolicy,
    compression: dict[str, Any],
    deliverable: dict[str, Any],
    determinism: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = state.execution_summary or {}
    auxiliary = [item for item in invocations if not _is_contractual(item)]
    return {
        "system": "ai_broker",
        "task_id": state.task_id,
        "policy": policy.determinism,
        "determinism": determinism,
        "prompt_compression": compression,
        "auxiliary_invocations_allowed": policy.auxiliary_invocations_allowed,
        "served_by": summary.get("served_by"),
        "requested_model": summary.get("requested_model"),
        "models_used": summary.get("models_used", []),
        "fallback_used": summary.get("fallback_used"),
        "total_cost_usd": _contract_cost(state, invocations),
        "exploratory_cost_usd": sum(item.cost_usd for item in auxiliary),
        "auxiliary_roles": sorted({item.role for item in auxiliary}),
        "deliverable": deliverable,
        "invocations": [item.model_dump(mode="json") for item in invocations],
    }


def _same_model(candidate: object, target: dict[str, Any]) -> bool:
    """Identidad de modelo del broker: provider + deployment + model."""
    if not isinstance(candidate, dict):
        return False
    return all(
        candidate.get(key) == target.get(key)
        for key in ("provider", "deployment", "model")
    )


def _contract_cost(
    state: BrokerTaskState, invocations: tuple[BrokerInvocation, ...]
) -> float:
    """Coste de la CARD, no de lo que el broker explore por su cuenta.

    `result.usage` es la contabilidad contractual del propio broker (excluye los
    roles auxiliares). Si no viniera, se suman las invocaciones contractuales.
    """
    usage = (state.result or {}).get("usage")
    if isinstance(usage, dict):
        reported = usage.get("cost_usd")
        if isinstance(reported, (int, float)) and not isinstance(reported, bool):
            return float(reported)
    return sum(item.cost_usd for item in _contract_invocations(invocations))


def _model_label(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    components = [value.get("provider"), value.get("deployment"), value.get("model")]
    return "/".join(str(item) for item in components if item)


def _is_deterministic(item: BrokerInvocation, policy: BrokerPolicy) -> bool:
    generation = item.generation or {}
    return (
        generation.get("temperature") == 0.0
        and generation.get("seed") == policy.seed
        and generation.get("seed_status") == "sent"
        and generation.get("top_p") == 1.0
        and generation.get("top_p_status") == "sent"
    )


def _validate_strict(
    state: BrokerTaskState,
    invocations: tuple[BrokerInvocation, ...],
    policy: BrokerPolicy,
) -> dict[str, Any]:
    """Comprueba lo que la telemetría permite demostrar, y nombra lo que no.

    Los artefactos no traen `invocation_id` (comprobado en vivo sobre el contrato
    2.10), así que no hay forma contractual de atar el entregable a una llamada
    concreta. Lo demostrable es: ninguna invocación contractual salió del modelo
    aprobado, y al menos una se ejecutó con los parámetros exactos que se
    pidieron. Una llamada contractual con otros parámetros —el `confidence_judge`
    puntúa con los suyos— no invalida la tarjeta, pero se registra en el Record
    en vez de darse por buena en silencio.
    """
    summary = state.execution_summary or {}
    served = summary.get("served_by")
    target = policy.target_model or {}
    identity = ("provider", "deployment", "model")
    if not isinstance(served, dict) or any(served.get(key) != target.get(key) for key in identity):
        raise BrokerPolicyViolation("strict policy target_model was not the model served")
    billable = _contract_invocations(invocations)
    foreign = [item for item in billable if not _same_model(item.model, target)]
    if foreign:
        raise BrokerPolicyViolation(
            "strict policy content reached other models: "
            + ", ".join(f"{item.role}@{item.model.get('model')}" for item in foreign)
        )
    successful = [item for item in billable if item.status == "completed"]
    if not successful:
        raise BrokerPolicyViolation("strict policy has no successful invocation telemetry")
    verified = [item for item in successful if _is_deterministic(item, policy)]
    if not verified:
        raise BrokerPolicyViolation("effective generation does not satisfy strict policy")
    return {
        "verified_by": [item.invocation_id for item in verified],
        "deviations": [
            {
                "invocation_id": item.invocation_id,
                "role": item.role,
                "generation": item.generation,
            }
            for item in successful
            if item not in verified
        ],
    }
