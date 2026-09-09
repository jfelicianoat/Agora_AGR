"""A07 contra el tablero real: el flujo entero visto por un cliente externo.

Descubrir que pedir y que se va a recibir, crear la tarjeta por el API, seguir
su estado hasta que un runner de verdad la ejecuta, recoger el artefacto y
validarlo contra el esquema que el propio tablero publico.

Lo que se prueba aqui y no se puede probar con `TestClient` es el arreglo del
origen: la tarjeta la crea un **cliente autenticado**, no una mano dejando un
fichero en la carpeta.
"""

from __future__ import annotations

import json
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BOARD = "https://127.0.0.1:8741"
CA = r"C:\Agora\pki\ca.crt"
BOARD_PYTHON = r"C:\Agora\venv\Scripts\python.exe"


def board_token() -> str:
    result = subprocess.run(
        [
            BOARD_PYTHON,
            "-c",
            "import keyring; print(keyring.get_password('agora-board','api_token') or '')",
        ],
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if not token:
        raise SystemExit("no hay token del tablero en el llavero (agora-board/api_token)")
    return token


TOKEN = board_token()
CTX = ssl.create_default_context(cafile=CA)
CTX.check_hostname = False


def call(method: str, path: str, payload: dict | None = None, key: str | None = None):
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if key:
        headers["Idempotency-Key"] = key
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(f"{BOARD}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, context=CTX, timeout=30) as response:
            body = response.read()
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, body
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, raw.decode("utf-8", "replace")


def main() -> int:
    fallos: list[str] = []

    def comprobar(nombre: str, condicion: bool, detalle: str = "") -> None:
        print(f"  [{'ok' if condicion else 'FALLA'}] {nombre}{f' — {detalle}' if detalle else ''}")
        if not condicion:
            fallos.append(nombre)

    print("=== 1. Descubrir que pedir y que se va a recibir ===")
    _, perfiles = call("GET", "/api/v1/profiles")
    perfil = next(p for p in perfiles["profiles"] if p["name"] == "task-decomposer")
    comprobar("el perfil publica su vocabulario", bool(perfil["handles"]), str(perfil["handles"]))
    comprobar("el perfil dice que produce", perfil.get("produces") == ["work-breakdown@1"])

    _, catalogo = call("GET", "/api/v1/contracts")
    contrato = next(
        c for c in catalogo["contracts"] if c["reference"] == perfil["produces"][0]
    )
    comprobar("el catalogo da el esquema", "properties" in contrato["schema"])
    comprobar("y un ejemplo", bool(contrato["example"]))

    print("\n=== 2. Crear la tarjeta como cliente autenticado ===")
    filename = f"a07-cliente-{uuid.uuid4().hex[:8]}.md"
    handle = perfil["handles"][1]
    peticion = {
        "filename": filename,
        "function": perfil["function"],
        "request": f"{handle}: preparar la defensa del TFM",
        "body": (
            "Tengo que preparar la defensa del TFM: presentacion, ensayo y las\n"
            "preguntas previsibles del tribunal. Dime tambien que dias me pongo.\n"
        ),
        "max_attempts": 3,
    }
    clave = str(uuid.uuid4())
    status, creada = call("POST", "/api/v1/cards", peticion, key=clave)
    comprobar("crear devuelve 201", status == 201, str(creada))
    comprobar("el nombre lo eligio el cliente", creada.get("filename") == filename)
    comprobar("nace en pending", creada.get("state") == "pending")
    comprobar("no es una repeticion", creada.get("replayed") is False)

    print("\n=== 3. Idempotencia ===")
    status, otra = call("POST", "/api/v1/cards", peticion, key=clave)
    comprobar("repetir no duplica", otra.get("filename") == filename and status == 201)
    comprobar("y se marca como repetida", otra.get("replayed") is True)

    status, conflicto = call(
        "POST", "/api/v1/cards", dict(peticion, body="otro cuerpo"), key=clave
    )
    comprobar("misma clave con otro cuerpo es 409", status == 409, str(conflicto)[:90])

    status, sin_clave = call("POST", "/api/v1/cards", dict(peticion, filename="x-" + filename))
    comprobar("sin clave de idempotencia se rechaza", status in {400, 422}, str(status))

    print("\n=== 4. El origen: la tarjeta de un cliente autenticado se despacha ===")
    status, trabajo = call("GET", f"/api/v1/work?profiles={perfil['name']}")
    elegible = any(item["filename"] == filename for item in trabajo)
    comprobar("la tarjeta es trabajo elegible para el runner", elegible)
    _, estado = call("GET", f"/api/v1/cards/{filename}")
    comprobar("y no esta bloqueada por origen", estado["state"] != "blocked", estado["state"])

    print("\n=== 5. Esperar al runner de verdad ===")
    deadline = time.time() + 1800
    visto: set[str] = set()
    while time.time() < deadline:
        _, estado = call("GET", f"/api/v1/cards/{filename}")
        actual = estado["state"]
        if actual not in visto:
            print(f"    estado: {actual}")
            visto.add(actual)
        if actual in {"done", "blocked", "archive"}:
            break
        time.sleep(15)
    comprobar("la tarjeta termina", estado["state"] == "done", estado["state"])
    comprobar("paso por in-progress", "in-progress" in visto, str(sorted(visto)))

    if estado["state"] != "done":
        print(json.dumps(estado, ensure_ascii=False, indent=2)[:1500])
        print(f"\nFALLOS: {fallos}")
        return 1

    print("\n=== 6. Recoger el artefacto y validarlo con el esquema publicado ===")
    status, contenido = call("GET", f"/api/v1/cards/{filename}/artifacts/0")
    comprobar("el artefacto se descarga", status == 200, str(status))
    texto = contenido if isinstance(contenido, str) else (
        contenido.decode("utf-8", "replace") if isinstance(contenido, bytes) else json.dumps(contenido)
    )

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from agora.output_contract import OutputContractError, enforce

    try:
        documento = enforce(contrato["schema"], texto)
        comprobar("cumple el contrato que el catalogo publico", True)
    except OutputContractError as error:
        comprobar("cumple el contrato que el catalogo publico", False, str(error)[:140])
        documento = None

    if documento:
        comprobar(
            "el documento dice que contrato es",
            documento["contract"] == contrato["name"]
            and documento["contract_version"] == contrato["version"],
        )
        pasos = documento["steps"]
        comprobar("trae pasos con criterio", all(s["completion_criteria"].strip() for s in pasos))
        cuerpo = json.dumps(documento, ensure_ascii=False).lower()
        comprobar(
            "no agenda pese a que se lo pidieron",
            not any(d in cuerpo for d in ("lunes", "martes", "miercoles", "jueves", "viernes")),
        )
        print(f"    kind={documento.get('kind')!r} pasos={len(pasos)} "
              f"total={documento['total_estimated_minutes']}")
        for aviso in documento.get("warnings", []):
            print(f"    aviso: {aviso[:150]}")

    print("\n=== 7. Cancelar ===")
    otro = f"a07-cancelar-{uuid.uuid4().hex[:8]}.md"
    call("POST", "/api/v1/cards", dict(peticion, filename=otro), key=str(uuid.uuid4()))
    clave_cancel = str(uuid.uuid4())
    status, _ = call(
        "POST", f"/api/v1/cards/{otro}/cancel", {"reason": "prueba de A07"}, key=clave_cancel
    )
    comprobar("cancelar responde 200", status == 200, str(status))
    _, tras = call("GET", f"/api/v1/cards/{otro}")
    comprobar("la tarjeta queda archivada", tras["state"] == "archive", tras["state"])
    status, repetida = call(
        "POST", f"/api/v1/cards/{otro}/cancel", {"reason": "prueba de A07"}, key=clave_cancel
    )
    comprobar("cancelar dos veces con la misma clave da lo mismo", status == 200)

    print("\n=== 8. Errores ===")
    status, _ = call("GET", "/api/v1/cards/no-existe-jamas.md")
    comprobar("tarjeta desconocida es 404", status == 404, str(status))

    print(f"\n{'=' * 62}")
    print(f"VEREDICTO A07: {'CORRECTO' if not fallos else 'REVISAR ' + str(fallos)}")
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
