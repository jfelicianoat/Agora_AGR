"""A09 contra el tablero real: seguir una tarjeta entera **solo con el cursor**.

Ni un `GET /cards/{filename}` durante todo el ciclo. Si el sondeo es de verdad
suficiente, el cliente tiene que poder enterarse de que su trabajo termino sin
preguntar por la tarjeta ni una sola vez.
"""

from __future__ import annotations

import json
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid

BOARD = "https://127.0.0.1:8741"
CA = r"C:\Agora\pki\ca.crt"
BOARD_PYTHON = r"C:\Agora\venv\Scripts\python.exe"


def board_token() -> str:
    result = subprocess.run(
        [BOARD_PYTHON, "-c",
         "import keyring; print(keyring.get_password('agora-board','api_token') or '')"],
        capture_output=True, text=True)
    token = result.stdout.strip()
    if not token:
        raise SystemExit("no hay token del tablero en el llavero")
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
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, raw.decode("utf-8", "replace")


def main() -> int:
    fallos: list[str] = []

    def comprobar(nombre: str, ok: bool, detalle: str = "") -> None:
        print(f"  [{'ok' if ok else 'FALLA'}] {nombre}{f' — {detalle}' if detalle else ''}")
        if not ok:
            fallos.append(nombre)

    marca = uuid.uuid4().hex[:8]
    referencia = {"system": "cliente-sondeo", "id": f"tarea-{marca}", "version": 1}

    print("=== 1. Guardar el cursor antes de nada ===")
    _, inicio = call("GET", "/api/v1/events?after=0&limit=1000")
    cursor = inicio["cursor"]
    comprobar("la pagina trae cursor y ventana", "oldest_available" in inicio,
              f"cursor={cursor} newest={inicio['newest']}")
    comprobar("un cliente al dia no ha perdido nada", inicio["missed"] is False)

    print("\n=== 2. Crear la tarjeta y no volver a preguntar por ella ===")
    nombre = f"a09-{marca}.md"
    status, _ = call("POST", "/api/v1/cards", {
        "filename": nombre,
        "function": "decompose",
        "request": "break down work: preparar el poster del congreso",
        "body": "Un poster A0 para el congreso de la semana que viene.\n",
        "external_reference": referencia,
    }, key=str(uuid.uuid4()))
    comprobar("crear devuelve 201", status == 201, str(status))

    print("\n=== 3. Seguirla solo por el cursor ===")
    vistos: list[tuple[str, str]] = []
    peticiones = 0
    vacias = 0
    deadline = time.time() + 1800
    terminada = False
    while time.time() < deadline and not terminada:
        _, pagina = call("GET", f"/api/v1/events?after={cursor}&limit=100")
        peticiones += 1
        if pagina["missed"]:
            comprobar("no se pierden eventos siguiendo el cursor", False, "missed=True")
            break
        if not pagina["events"]:
            vacias += 1
            time.sleep(10)
            continue
        for evento in pagina["events"]:
            datos = evento["data"]
            if datos.get("filename") != nombre:
                continue
            vistos.append((evento["kind"], datos.get("state", "?")))
            print(f"    {evento['kind']:18} -> {datos.get('state')}"
                  f"  ref={datos.get('external_reference', {}).get('id', '—')}")
            if datos.get("state") in {"done", "blocked", "archive"}:
                terminada = True
        cursor = pagina["cursor"]
        if not pagina["more"] and not terminada:
            time.sleep(10)

    comprobar("la tarjeta llega a done sin haber leido la tarjeta ni una vez",
              any(estado == "done" for _, estado in vistos), str(vistos))
    comprobar("se vio nacer y cerrarse",
              {"card.created", "card.closed"} <= {kind for kind, _ in vistos},
              str(sorted({kind for kind, _ in vistos})))
    comprobar("cada evento traia su estado",
              all(estado not in {"?", None} for _, estado in vistos))
    print(f"    peticiones al cursor: {peticiones} (de ellas {vacias} sin novedades)")

    print("\n=== 4. Cada evento traia el identificador del cliente ===")
    _, todos = call("GET", f"/api/v1/events?after={inicio['cursor']}&limit=1000")
    mios = [e for e in todos["events"] if e["data"].get("filename") == nombre]
    comprobar("todos los eventos de la tarjeta llevan la referencia",
              bool(mios) and all(e["data"].get("external_reference") == referencia for e in mios),
              f"{len(mios)} eventos")

    print("\n=== 5. Sondear sin novedades es barato y estable ===")
    _, quieta = call("GET", f"/api/v1/events?after={todos['cursor']}")
    comprobar("no devuelve nada", quieta["events"] == [])
    comprobar("y el cursor no retrocede", quieta["cursor"] == todos["cursor"],
              f"{quieta['cursor']} vs {todos['cursor']}")
    comprobar("ni inventa un hueco", quieta["missed"] is False)

    print("\n=== 6. Paginacion: recorrer todo una sola vez ===")
    ids: list[int] = []
    caminante = 0
    while True:
        _, pagina = call("GET", f"/api/v1/events?after={caminante}&limit=5")
        ids.extend(e["id"] for e in pagina["events"])
        caminante = pagina["cursor"]
        if not pagina["more"]:
            break
    comprobar("ningun evento repetido", len(ids) == len(set(ids)), f"{len(ids)} eventos")
    comprobar("en orden", ids == sorted(ids))

    print("\n=== 7. No hay ningun endpoint de suscripcion ===")
    for ruta in ("/api/v1/webhooks", "/api/v1/callbacks", "/api/v1/subscriptions"):
        status, _ = call("GET", ruta)
        comprobar(f"{ruta} no existe", status == 404, str(status))

    print(f"\n{'=' * 62}")
    print(f"VEREDICTO A09: {'CORRECTO' if not fallos else 'REVISAR ' + str(fallos)}")
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
