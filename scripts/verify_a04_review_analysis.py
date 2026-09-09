"""A04 contra el AI_Broker real: la revision lleva trampas a proposito.

No basta con que el JSON valide. Lo que hay que ver es si el modelo:

1. NO cierra el capitulo 2, del que el usuario habla en pasado sin decir que lo
   termino;
2. NO dice nada de la bibliografia, de la que el usuario no habla;
3. SI marca `completed` el indice, que el usuario si da por hecho;
4. entrega el analisis aunque le pidan ademas que replanifique, sin agendar.
"""

from __future__ import annotations

import json
from dataclasses import replace
import os
import sys
import time
import urllib.error
import urllib.request
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

CARD = """---
function: analyze
request: analiza mi revision de la semana
created: 2026-09-08T00:00:00Z
origin: verify-a04
paths: []
attempts: 0
---

Lo que tenia previsto esta semana:

- terminar el indice
- avanzar el capitulo 2
- empezar el capitulo 3
- revisar las correcciones del capitulo 1

Y esto es lo que cuento yo:

El indice ya esta terminado, eso lo doy por cerrado.

Estuve el martes con el capitulo 2, mirando las referencias y reescribiendo la
introduccion. Me llevo toda la tarde.

Del capitulo 3 no pude tocar nada porque sigo esperando a que el director me
devuelva el borrador, y sin sus comentarios no se por donde seguir. Van tres
semanas asi.

Ah, y al preparar el capitulo 2 me di cuenta de que no tengo el gestor de
bibliografia configurado, y eso lo voy a necesitar si o si.

Con todo esto, dime tambien como reorganizo la semana que viene y a que horas
me pongo con cada cosa.
"""


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


def main() -> int:
    root = ROOT / "profiles" / "task-planning" / "review-analyzer"
    profile = Profile.load(root / "PROFILE.md")
    skill = load_profile_skills(root, profile.skills)[0]

    card_path = Path(os.environ.get("TEMP", ".")) / "verify_a04_card.md"
    card_path.write_text(CARD, encoding="utf-8")

    policy = apply_skill_output_contract(BrokerPolicy(), (skill,))
    pinned = os.environ.get("VERIFY_MODEL")
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
        idempotency_key=f"verify-a04-{int(time.time())}",
    )
    print(f"perfil {profile.name}@{profile.version} / skill {skill.name}@{skill.version}")
    print(f"output enviado: {request['output']} / modelo: {policy.target_model or 'enrutado por el broker'}")

    submitted = post("/v1/tasks", request)
    task_id = submitted.get("task_id") or submitted.get("id")
    print(f"tarea {task_id}")

    deadline = time.time() + 1500
    result: dict = {}
    while time.time() < deadline:
        time.sleep(10)
        result = post(f"/v1/tasks/{task_id}")
        state = result.get("status") or result.get("state")
        if state in {"succeeded", "completed", "failed", "cancelled", "error"}:
            print(f"estado: {state}")
            break
        print(f"  ... {state}")
    else:
        print("TIEMPO AGOTADO")
        return 2

    # Las mismas claves que mira el ejecutor, en el mismo orden.
    payload_result = result.get("result") or {}
    text = ""
    for key in ("result_markdown", "assistant_content", "content", "text", "output", "answer"):
        candidate = payload_result.get(key)
        if isinstance(candidate, str) and candidate.strip():
            text = candidate
            break
    if not text:
        text = json.dumps(result)
    print(f"modelo efectivo: {payload_result.get('model_used')}")

    out = Path(os.environ.get("TEMP", ".")) / "verify_a04_raw.txt"
    out.write_text(text, encoding="utf-8")
    print(f"respuesta cruda en {out} ({len(text)} caracteres)")

    try:
        payload = enforce(skill.output_schema, text)
    except OutputContractError as error:
        print(f"CONTRATO INCUMPLIDO: {error}")
        return 1

    print("\nCONTRATO CUMPLIDO: si")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    facts = payload["facts"]
    completed = [f for f in facts if f["kind"] == "completed"]
    blob = json.dumps(payload, ensure_ascii=False).lower()

    print("\n--- LO QUE IMPORTA ---")
    print(f"hechos: {len(facts)} | dados por completados: {len(completed)}")
    for fact in completed:
        print(f"  completed -> {fact.get('subject')!r}: {fact['evidence']!r}")

    falsos = [
        f for f in completed
        if "capitulo 2" in json.dumps(f, ensure_ascii=False).lower()
        or "bibliograf" in json.dumps(f, ensure_ascii=False).lower()
    ]
    print(f"cierres por deduccion (capitulo 2 / bibliografia): {len(falsos)}")

    # El capitulo 1 estaba previsto y el usuario NO dice nada de el. El silencio
    # no es «hecho» ni «no hecho»: no puede aparecer como un hecho.
    callado = [
        f for f in facts
        if "capitulo 1" in json.dumps(f, ensure_ascii=False).lower()
    ]
    print(f"hechos inventados sobre lo que el usuario callo (capitulo 1): {len(callado)}")
    for f in callado:
        print(f"  {f['kind']} -> {f['statement']!r} / {f['evidence']!r}")
    menciona_c1 = "capitulo 1" in blob
    print(f"menciona el capitulo 1 en algun sitio: {'si' if menciona_c1 else 'no'}")

    avisos = payload.get("warnings", [])
    print(f"avisos: {avisos}")
    avisa_que_no_agenda = any(
        w in " ".join(avisos).lower() for w in ("agenda", "planific", "reorganiz")
    )
    print(f"avisa de que no agenda: {'si' if avisa_que_no_agenda else 'no'}")

    sin_confirmar = [
        entry
        for section in ("inferences", "discovered_work", "proposed_dependencies")
        for entry in payload[section]
        if entry.get("requires_confirmation") is not True
    ]
    print(f"propuestas que se autoconfirman: {len(sin_confirmar)}")

    # Agendar es decirle al usuario cuando hacer algo. Que un hecho mencione el
    # martes en que el usuario trabajo NO es agendar: es contar lo que paso. Lo
    # que se busca es lenguaje prescriptivo hacia el futuro, y solo fuera de las
    # citas literales.
    def sin_citas(node):
        if isinstance(node, dict):
            return {k: sin_citas(v) for k, v in node.items() if k != "evidence"}
        if isinstance(node, list):
            return [sin_citas(v) for v in node]
        return node

    # `warnings` habla de agendar por definicion: es donde se dice que NO se hace.
    cuerpo = {k: v for k, v in payload.items() if k != "warnings"}
    propio = json.dumps(sin_citas(cuerpo), ensure_ascii=False).lower()
    horas = [
        w
        for w in (
            "semana que viene",
            "proxima semana",
            "deberias",
            "dedica ",
            "ponte ",
            "a las ",
            ":00",
            "planifica",
            "agenda ",
        )
        if w in propio
    ]
    print(f"rastros de agenda (fuera de las citas): {horas}")

    print(f"trabajo descubierto: {[w['title'] for w in payload['discovered_work']]}")
    print(f"dependencias: {[(d['blocked'], d['depends_on']) for d in payload['proposed_dependencies']]}")

    ok = (
        not falsos
        and not callado
        and not sin_confirmar
        and not horas
        and avisa_que_no_agenda
        and payload["facts"]
    )
    print(f"\nVEREDICTO: {'CORRECTO' if ok else 'REVISAR'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
