"""A06 contra el broker real: el contrato compartido, y la deuda que cierra.

Hasta A06 las tres skills especializadas de descomposicion **no declaraban** el
contrato que A03 documentaba que producian. Cumplian de rebote, porque el
ejecutor manda todas las skills del perfil y la generica si lo declaraba.
Ninguna ejecucion real habia caido nunca en `course` ni en `study`, asi que la
promesa estaba sin comprobar.

Aqui se comprueba: dos encargos escritos para caer en esos procedimientos, y el
mismo documento `work-breakdown` v1 para los dos.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
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

HEAD = """---
function: decompose
request: 'break down work: {titulo}'
created: 2026-09-08T00:00:00Z
origin: verify-a06
paths: []
attempts: 0
---

"""

VARIANTS = {
    "curso": (
        "impartir un curso de introduccion a Python",
        "Tengo que impartir un curso de introduccion a Python de seis sesiones\n"
        "para companeros de trabajo que no han programado nunca. Hay que decidir\n"
        "que entra y que no, preparar el material y algo con lo que practiquen.\n"
        "Dime tambien en que semanas doy cada sesion.\n",
    ),
    "estudio": (
        "preparar el examen de estadistica del tema 6",
        "Tengo el examen de estadistica del tema 6 y voy justo. Son cuarenta\n"
        "ejercicios, un formulario que no me se, y dos demostraciones que no\n"
        "entiendo. Quiero llegar sabiendo hacerlos, no habiendolos leido.\n",
    ),
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


def run(name: str, titulo: str, cuerpo: str, profile, skills, pinned) -> bool:
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")
    card_path = TEMP / f"verify_a06_{name}.md"
    card_path.write_text(HEAD.format(titulo=titulo) + cuerpo, encoding="utf-8")

    policy = apply_skill_output_contract(BrokerPolicy(), skills)
    if pinned:
        provider, deployment, model = pinned.split("/", 2)
        policy = replace(
            policy,
            determinism="strict",
            target_model={"provider": provider, "deployment": deployment, "model": model},
        )
    request = build_broker_request(
        profile,
        skills,
        Card.load(card_path),
        policy=policy,
        idempotency_key=f"verify-a06-{name}-{int(time.time())}",
    )
    prompt = request["content"]["prompt"]
    print(f"skills en el prompt: {[s.name for s in skills]}")
    print(f"veces que aparece 'additionalProperties': {prompt.count('additionalProperties')}")
    print(f"vocabularios cerrados: {'si' if 'Estos campos solo admiten' in prompt else 'no'}")

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
    (TEMP / f"verify_a06_{name}_raw.txt").write_text(text, encoding="utf-8")
    print(f"modelo efectivo: {body.get('model_used')}")

    schema = next(s.output_schema for s in skills if s.declares_output_contract)
    try:
        payload = enforce(schema, text)
    except OutputContractError as error:
        print(f"CONTRATO INCUMPLIDO: {error}")
        return False

    print("CONTRATO CUMPLIDO: si")
    steps = payload["steps"]
    ordenes = [s["order"] for s in steps]
    total = payload.get("total_estimated_minutes")
    estimados = [s["estimated_minutes"] for s in steps if s["estimated_minutes"] is not None]
    sin_estimar = [s["order"] for s in steps if s["estimated_minutes"] is None]
    suma = sum(estimados)
    sin_criterio = [s["order"] for s in steps if not s.get("completion_criteria", "").strip()]
    dep_malas = [d for s in steps for d in s.get("depends_on", []) if d not in ordenes]
    fechas = re.findall(r"\b(20\d\d-\d\d-\d\d|lunes|martes|miercoles|jueves|viernes|\d{1,2}:\d{2})\b",
                        json.dumps(payload, ensure_ascii=False).lower())

    print(f"kind declarado: {payload.get('kind')!r}")
    print(f"contrato: {payload['contract']}@{payload['contract_version']}")
    print(f"pasos: {len(steps)} | orden: {ordenes}")
    print(f"pasos sin estimar: {sin_estimar or 'ninguno'}")
    print(f"pasos sin criterio de terminado: {sin_criterio or 'ninguno'}")
    print(f"dependencias a pasos inexistentes: {dep_malas or 'ninguna'}")
    print(f"total declarado {total} vs suma de los estimados {suma}: "
          f"{'coincide' if total == suma else 'NO COINCIDE'}")
    print(f"fechas u horas coladas: {fechas or 'ninguna'}")
    for w in payload.get("warnings", []):
        print(f"aviso: {w[:170]}")

    ok = (
        payload["contract"] == "work-breakdown"
        and payload["contract_version"] == 1
        and steps
        and not sin_criterio
        and not dep_malas
        and not fechas
        and total == suma
    )
    print(f"\nVEREDICTO {name}: {'CORRECTO' if ok else 'REVISAR'}")
    return ok


def main() -> int:
    root = ROOT / "profiles" / "task-planning" / "task-decomposer"
    profile = Profile.load(root / "PROFILE.md")
    skills = load_profile_skills(root, profile.skills)
    print(f"perfil {profile.name}@{profile.version}")
    for skill in skills:
        print(
            f"  {skill.name:22} cita {skill.output_contract}@"
            f"{skill.output_contract_version} resuelto={skill.declares_output_contract}"
        )
    # La forma es una sola: si hubiera dos, esto seria una copia distinta.
    esquemas = {id(s.output_schema) for s in skills if s.declares_output_contract}
    print(f"esquemas distintos entre las cuatro skills: {len(esquemas)}")

    pinned = os.environ.get("VERIFY_MODEL")
    only = os.environ.get("VERIFY_VARIANT")
    results = {
        name: run(name, titulo, cuerpo, profile, skills, pinned)
        for name, (titulo, cuerpo) in VARIANTS.items()
        if only in (None, name)
    }
    print(f"\n{'=' * 70}")
    for name, ok in results.items():
        print(f"{name}: {'CORRECTO' if ok else 'REVISAR'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
