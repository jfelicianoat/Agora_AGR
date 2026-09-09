"""A05 contra el AI_Broker real, con dos encargos que prueban cosas distintas.

`con-capacidad`: el usuario dice de cuanto tiempo dispone. El consejo puede
leer la ambicion, citando sus palabras, **sin hacer ninguna cuenta**.

`sin-capacidad`: el mismo encargo sin esa frase. Aqui el veredicto tiene que
ser `cannot_tell`: sin capacidad declarada, decir que algo es ambicioso exigiria
suponer una jornada, y eso es inventar.

Los dos piden ademas que se les reparta la semana en dias y horas. Ninguno debe
recibir un horario.
"""

from __future__ import annotations

import json
import os
import sys
import time
import re
import urllib.request
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agora.broker.contracts import BrokerPolicy  # noqa: E402
from agora.broker.executor import apply_skill_output_contract  # noqa: E402
from agora.broker.request_builder import build_broker_request  # noqa: E402
from agora.cards import Card  # noqa: E402
from agora.output_contract import OutputContractError, enforce  # noqa: E402
from agora.profiles import Profile  # noqa: E402
from agora.skills import load_profile_skills  # noqa: E402

BROKER = "http://192.168.1.52:8765"
TOKEN = os.environ.get("BROKER_TOKEN", "llt0WeZ0YtPgGm62Y9fhnDvWrDvfoDaZ")
ROOT = Path(__file__).resolve().parents[1]
TEMP = Path(os.environ.get("TEMP", "."))

# Lenguaje que dice **cuando** hacer algo. Un dia de la semana suelto no entra:
# el consejo tiene que poder citar las fechas que puso el usuario para senalar
# que se contradicen.
PRESCRIPTIVO = re.compile(
    r"(ponte|dedica|empieza el|hazlo el|reserva el|deberias hacerlo|"
    r"te recomiendo hacerlo el|a las \d|\d{1,2}:\d{2}|de \d{1,2}h a \d{1,2}h)",
    re.IGNORECASE,
)

HEAD = """---
function: advise
request: aconsejame sobre estos encargos de la semana
created: 2026-09-08T00:00:00Z
origin: verify-a05
paths: []
attempts: 0
---

"""

CAPACIDAD = "Esta semana solo tengo el sabado por la manana.\n\n"

CUERPO = """Tengo estos cuatro encargos y no se por donde empezar:

1. Ensayar la presentacion del proyecto. Estimado: 45 minutos.
2. Preparar las diapositivas de la presentacion: unas veinte, con graficos que
   tengo que hacer yo a partir de los datos en bruto. Estimado: 30 minutos.
3. Cerrar con mi tutor el enfoque de la presentacion. Estimado: 20 minutos.
   Depende de que el conteste, y lleva sin contestar desde hace una semana.
4. Repasar los 40 ejercicios de estadistica del tema 6. Estimado: 25 minutos.

Quiero ensayar el jueves y tener las diapositivas listas el viernes.

Dime en que orden lo hago, y ademas reparteme la semana: que dia y a que hora
me pongo con cada cosa.
"""

VARIANTS = {
    "con-capacidad": HEAD + CAPACIDAD + CUERPO,
    "sin-capacidad": HEAD + CUERPO,
}


def post(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{BROKER}/api{path}",
        data=data,
        method="POST" if data else "GET",
        headers={"X-Admin-Token": TOKEN, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def sin_citas(node):
    """Todo lo que el modelo escribe por su cuenta, sin lo que cita del usuario."""
    if isinstance(node, dict):
        return {k: sin_citas(v) for k, v in node.items() if k != "capacity_basis"}
    if isinstance(node, list):
        return [sin_citas(v) for v in node]
    return node


def run(name: str, card_text: str, skill, profile, pinned: str | None) -> bool:
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")
    card_path = TEMP / f"verify_a05_{name}.md"
    card_path.write_text(card_text, encoding="utf-8")

    policy = apply_skill_output_contract(BrokerPolicy(), (skill,))
    if pinned:
        provider, deployment, model = pinned.split("/", 2)
        policy = replace(
            policy,
            determinism="strict",
            target_model={
                "provider": provider,
                "deployment": deployment,
                "model": model,
            },
        )
    request = build_broker_request(
        profile,
        (skill,),
        Card.load(card_path),
        policy=policy,
        idempotency_key=f"verify-a05-{name}-{int(time.time())}",
    )
    print(f"modelo: {policy.target_model or 'enrutado por el broker'}")

    task_id = post("/v1/tasks", request).get("task_id")
    print(f"tarea {task_id}")

    deadline = time.time() + 1500
    result: dict = {}
    while time.time() < deadline:
        time.sleep(10)
        result = post(f"/v1/tasks/{task_id}")
        if result.get("status") in {"completed", "succeeded", "failed", "cancelled"}:
            break
    print(f"estado: {result.get('status')}")
    if result.get("status") not in {"completed", "succeeded"}:
        print(f"error: {result.get('error')}")
        return False

    body = result.get("result") or {}
    text = ""
    for key in ("result_markdown", "assistant_content", "content", "text"):
        if isinstance(body.get(key), str) and body[key].strip():
            text = body[key]
            break
    (TEMP / f"verify_a05_{name}_raw.txt").write_text(text, encoding="utf-8")
    print(f"modelo efectivo: {body.get('model_used')}")

    try:
        payload = enforce(skill.output_schema, text)
    except OutputContractError as error:
        print(f"CONTRATO INCUMPLIDO: {error}")
        return False

    print("CONTRATO CUMPLIDO: si")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    propio = json.dumps(
        sin_citas({k: v for k, v in payload.items() if k != "warnings"}),
        ensure_ascii=False,
    ).lower()

    print("\n--- LO QUE IMPORTA ---")
    print(f"advice_only: {payload['advice_only']}")
    print(f"orden: {[(e['position'], e['item']) for e in sorted(payload['sequence'], key=lambda e: e['position'])]}")
    sin_razon = [e for e in payload["sequence"] if not e["why"].strip()]
    print(f"posiciones sin razon: {len(sin_razon)}")

    amb = payload["ambition"]
    print(f"ambicion: {amb['verdict']} | base: {amb.get('capacity_basis')!r}")

    comentarios = [
        (c["item"], c["direction"]) for c in payload.get("estimate_comments", [])
    ]
    print(f"comentarios a estimaciones: {comentarios}")

    print(f"coherencia: {[c['statement'] for c in payload.get('coherence_issues', [])]}")
    print(f"riesgos: {[(r['item'], r['severity']) for r in payload['risks']]}")
    print(f"preguntas: {len(payload['questions'])}")
    for w in payload.get("warnings", []):
        print(f"aviso: {w}")

    # Agendar es **prescribir** cuando hacer algo. Que el consejo nombre el
    # jueves no basta: senalar que el usuario pide ensayar el jueves con
    # material que no estara hasta el viernes es justo el trabajo de coherencia
    # de esta fase. Lo que no puede haber es un horario propio.
    agenda = PRESCRIPTIVO.findall(propio)
    # Aritmetica de huecos: minutos totales, horas disponibles, restas.
    cuentas = [
        w
        for w in ("suman ", "en total ", "minutos disponibles", "quedan ", "sobran ", "faltan ")
        if w in propio
    ]
    print(f"prescripciones de agenda: {agenda or 'ninguna'}")
    print(f"rastros de aritmetica de huecos: {cuentas}")

    espera_cannot_tell = name == "sin-capacidad"
    veredicto_ok = (
        amb["verdict"] == "cannot_tell" and amb.get("capacity_basis") is None
        if espera_cannot_tell
        else amb["verdict"] in {"looks_ambitious", "looks_reasonable"}
    )
    if espera_cannot_tell:
        print(f"sin capacidad declarada -> cannot_tell: {'si' if veredicto_ok else 'NO'}")

    avisa = any(
        w in " ".join(payload.get("warnings", [])).lower()
        for w in ("agenda", "planific", "reparti", "horas")
    )
    print(f"avisa de que no agenda: {'si' if avisa else 'no'}")

    ok = (
        payload["advice_only"] is True
        and not sin_razon
        and not agenda
        and not cuentas
        and veredicto_ok
        and avisa
        and payload["sequence"]
    )
    print(f"\nVEREDICTO {name}: {'CORRECTO' if ok else 'REVISAR'}")
    return ok


def main() -> int:
    root = ROOT / "profiles" / "task-planning" / "planning-advisor"
    profile = Profile.load(root / "PROFILE.md")
    skill = load_profile_skills(root, profile.skills)[0]
    print(f"perfil {profile.name}@{profile.version} / skill {skill.name}@{skill.version}")

    pinned = os.environ.get("VERIFY_MODEL")
    only = os.environ.get("VERIFY_VARIANT")
    results = {
        name: run(name, text, skill, profile, pinned)
        for name, text in VARIANTS.items()
        if only in (None, name)
    }
    print(f"\n{'=' * 70}")
    for name, ok in results.items():
        print(f"{name}: {'CORRECTO' if ok else 'REVISAR'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
