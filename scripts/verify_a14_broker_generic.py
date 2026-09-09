"""A14 contra el AI_Broker real: las cuatro capacidades, una sola peticion.

No se prueba que el broker responda bien —eso ya esta probado fase por fase—.
Se prueba que **para hacerlas todas no ha hecho falta cambiarle nada**: mismo
`inference_kind`, misma forma de sobre, y el broker sin enterarse de que existen
las tareas personales.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
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

ENCARGOS = {
    "task-intake": (
        "understand",
        "understand request: tengo que dejar lista la memoria",
        "No se muy bien que me falta. Creo que el capitulo 4 y las conclusiones.\n",
    ),
    "task-decomposer": (
        "decompose",
        "break down work: preparar el poster del congreso",
        "Un poster A0 con los resultados del segundo experimento.\n",
    ),
    "review-analyzer": (
        "analyze",
        "analyze review: como fue la semana",
        "El capitulo 4 ya esta cerrado. Del poster no pude hacer nada.\n",
    ),
    "planning-advisor": (
        "advise",
        "advise on sequencing: tres encargos de la semana",
        "Cerrar el capitulo 4 (90 min), el poster (120 min) y ensayar (45 min).\n"
        "Esta semana solo tengo el viernes por la tarde.\n",
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


def main() -> int:
    fallos: list[str] = []

    def comprobar(nombre: str, ok: bool, detalle: str = "") -> None:
        print(f"  [{'ok' if ok else 'FALLA'}] {nombre}{f' — {detalle}' if detalle else ''}")
        if not ok:
            fallos.append(nombre)

    print("=== 1. La misma forma de peticion para las cuatro capacidades ===")
    peticiones: dict[str, dict] = {}
    for nombre, (function, request, body) in ENCARGOS.items():
        root = ROOT / "profiles" / "task-planning" / nombre
        profile = Profile.load(root / "PROFILE.md")
        skills = load_profile_skills(root, profile.skills)
        card_path = Path(os.environ.get("TEMP", ".")) / f"a14-{nombre}.md"
        card_path.write_text(
            "---\n"
            f"function: {function}\n"
            f"request: '{request}'\n"
            "created: 2026-09-09T00:00:00Z\n"
            "origin: verify-a14\npaths: []\nattempts: 0\n---\n\n" + body,
            encoding="utf-8",
        )
        policy = apply_skill_output_contract(BrokerPolicy(), skills)
        # Un modelo capaz para las cuatro, como en produccion (`maximum`).
        policy = replace(
            policy,
            determinism="strict",
            target_model={"provider": "ollama", "deployment": "local", "model": "qwen3.8:27b"},
        )
        peticiones[nombre] = (
            build_broker_request(
                profile,
                skills,
                Card.load(card_path),
                policy=policy,
                idempotency_key=f"a14-{nombre}-{int(time.time())}",
            ),
            skills,
        )

    kinds = {p["inference_kind"] for p, _ in peticiones.values()}
    comprobar("un solo tipo de operacion", kinds == {"chat"}, str(kinds))
    formas = {tuple(sorted(p)) for p, _ in peticiones.values()}
    comprobar("una sola forma de sobre", len(formas) == 1, str(len(formas)))
    salidas = {json.dumps(p["output"], sort_keys=True) for p, _ in peticiones.values()}
    comprobar("una sola seccion de salida", len(salidas) == 1, str(salidas))
    tamanos = {n: len(p["content"]["prompt"]) for n, (p, _) in peticiones.items()}
    print(f"    lo unico que cambia es el prompt: {tamanos}")

    print("\n=== 2. El broker acepta las cuatro sin nada especial ===")
    tareas: dict[str, str] = {}
    for nombre, (peticion, _) in peticiones.items():
        try:
            enviada = post("/v1/tasks", peticion)
            tareas[nombre] = enviada.get("task_id", "")
            comprobar(f"{nombre}: aceptada", bool(tareas[nombre]), tareas[nombre][:24])
        except urllib.error.HTTPError as error:
            comprobar(f"{nombre}: aceptada", False, f"HTTP {error.code}: {error.read()[:200]!r}")

    print("\n=== 3. Y las cuatro producen su contrato ===")
    deadline = time.time() + 2400
    pendientes = dict(tareas)
    resultados: dict[str, dict] = {}
    while pendientes and time.time() < deadline:
        time.sleep(15)
        for nombre, task_id in list(pendientes.items()):
            estado = post(f"/v1/tasks/{task_id}")
            if estado.get("status") in {"completed", "succeeded", "failed", "cancelled"}:
                resultados[nombre] = estado
                del pendientes[nombre]
                print(f"    {nombre}: {estado.get('status')}")

    for nombre, (_, skills) in peticiones.items():
        estado = resultados.get(nombre)
        if not estado or estado.get("status") not in {"completed", "succeeded"}:
            comprobar(f"{nombre}: cumple su contrato", False,
                      str((estado or {}).get("error") or "sin terminar"))
            continue
        body = estado.get("result") or {}
        texto = next(
            (body[k] for k in ("result_markdown", "assistant_content", "content", "text")
             if isinstance(body.get(k), str) and body[k].strip()),
            "",
        )
        esquema = next(s.output_schema for s in skills if s.declares_output_contract)
        try:
            documento = enforce(esquema, texto)
            comprobar(f"{nombre}: cumple su contrato", True,
                      f"{documento['contract']}@{documento['contract_version']}")
        except OutputContractError as error:
            comprobar(f"{nombre}: cumple su contrato", False, str(error)[:120])

    print("\n=== 4. El broker no ha aprendido nada del cliente ===")
    for nombre, (peticion, _) in peticiones.items():
        sobre = json.loads(json.dumps(peticion))
        sobre["content"]["prompt"] = ""
        sobre["content"]["metadata"]["profile"] = ""
        sobre["request_id"] = ""
        texto = json.dumps(sobre, ensure_ascii=False).lower()
        sucio = [
            palabra
            for palabra in ("calendar", "workblock", "pomodoro", "availability",
                            "work-breakdown", "review-analysis", "task-brief", "plan-advice")
            if palabra in texto
        ]
        comprobar(f"{nombre}: el sobre es generico", not sucio, str(sucio))

    print("\n=== 5. Lo que el broker anuncia, sin extensiones nuestras ===")
    contrato = post("/v1/meta/capabilities") if False else None
    print("    (no se consulta: Agora no pide ninguna extension)")

    print(f"\n{'=' * 62}")
    print(f"VEREDICTO A14: {'CORRECTO' if not fallos else 'REVISAR ' + str(fallos)}")
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
