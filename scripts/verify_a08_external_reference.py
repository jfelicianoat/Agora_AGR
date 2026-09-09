"""A08 contra el tablero real: perder el nombre de la tarjeta y recuperarla.

El caso que justifica la fase entera: un cliente crea trabajo, se cae a mitad y
solo conserva **su** identificador. Con `filename` como unica asa, ese trabajo
quedaba huerfano.
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
        raise SystemExit("no hay token del tablero en el llavero")
    return token


TOKEN = board_token()
CTX = ssl.create_default_context(cafile=CA)
CTX.check_hostname = False


def call(method: str, path: str, payload: dict | None = None, key: str | None = None,
         token: str | None = TOKEN):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
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
    referencia = {"system": "cliente-de-prueba", "id": f"tarea-{marca}", "version": 1}
    peticion = {
        "function": "decompose",
        "request": "break down work: preparar el seminario de metodologia",
        "body": "Un seminario de dos horas para el grupo de investigacion.\n",
        "external_reference": referencia,
    }

    print("=== 1. Crear con referencia, y perder el nombre a proposito ===")
    nombre = f"a08-{marca}.md"
    status, creada = call(
        "POST", "/api/v1/cards", dict(peticion, filename=nombre), key=str(uuid.uuid4())
    )
    comprobar("crear devuelve 201", status == 201, str(creada))
    comprobar(
        "la respuesta NO devuelve la referencia (no es la identidad)",
        "external_reference" not in creada,
        str(creada),
    )
    del nombre  # a partir de aqui el cliente solo tiene su propio id

    print("\n=== 2. Reconciliar: encontrarla solo con el identificador propio ===")
    status, encontrado = call(
        "GET",
        f"/api/v1/cards?system={referencia['system']}&external_id={referencia['id']}",
    )
    comprobar("la busqueda responde 200", status == 200, str(status))
    tarjetas = encontrado.get("cards", [])
    comprobar("encuentra exactamente una tarjeta", len(tarjetas) == 1, str(tarjetas))
    if not tarjetas:
        print(f"\nFALLOS: {fallos}")
        return 1
    recuperado = tarjetas[0]["filename"]
    print(f"    recuperado: {recuperado}")
    comprobar(
        "y devuelve la referencia intacta",
        tarjetas[0]["external_reference"] == referencia,
        str(tarjetas[0]["external_reference"]),
    )

    print("\n=== 3. La referencia viaja en la tarjeta ===")
    _, estado = call("GET", f"/api/v1/cards/{recuperado}")
    comprobar(
        "esta en la metadata de la CARD",
        estado["metadata"].get("external_reference") == referencia,
    )

    print("\n=== 4. No es unica: dos tarjetas pueden compartirla ===")
    segundo = f"a08-{marca}-b.md"
    call("POST", "/api/v1/cards", dict(peticion, filename=segundo), key=str(uuid.uuid4()))
    _, ambas = call(
        "GET",
        f"/api/v1/cards?system={referencia['system']}&external_id={referencia['id']}",
    )
    comprobar(
        "la busqueda devuelve las dos",
        len(ambas.get("cards", [])) == 2,
        str([c["filename"] for c in ambas.get("cards", [])]),
    )

    print("\n=== 5. No es una clave de seguridad ===")
    status, _ = call(
        "GET",
        f"/api/v1/cards?system={referencia['system']}&external_id={referencia['id']}",
        token=None,
    )
    comprobar("sin credenciales no se busca", status in {401, 403}, str(status))
    status, _ = call("GET", "/api/v1/cards?system=cliente-de-prueba")
    comprobar("buscar con media referencia es 422", status == 422, str(status))
    status, _ = call(
        "POST",
        "/api/v1/cards",
        dict(peticion, filename=f"a08-{marca}-c.md",
             external_reference={**referencia, "prioridad": 3}),
        key=str(uuid.uuid4()),
    )
    comprobar("un campo inventado en la referencia es 422", status == 422, str(status))

    print("\n=== 6. Compatibilidad: una tarjeta sin referencia sigue igual ===")
    sin_ref = f"a08-{marca}-sin.md"
    status, _ = call(
        "POST",
        "/api/v1/cards",
        {k: v for k, v in dict(peticion, filename=sin_ref).items()
         if k != "external_reference"},
        key=str(uuid.uuid4()),
    )
    comprobar("se crea sin problema", status == 201, str(status))
    _, plana = call("GET", f"/api/v1/cards/{sin_ref}")
    comprobar(
        "y no lleva la clave",
        "external_reference" not in plana["metadata"],
    )

    print("\n=== 7. El trabajo sigue corriendo, y se reconcilia cuando termina ===")
    deadline = time.time() + 1800
    visto: set[str] = set()
    estado_final = ""
    while time.time() < deadline:
        _, actual = call("GET", f"/api/v1/cards/{recuperado}")
        estado_final = actual["state"]
        if estado_final not in visto:
            print(f"    estado: {estado_final}")
            visto.add(estado_final)
        if estado_final in {"done", "blocked", "archive"}:
            break
        time.sleep(15)
    comprobar("la tarjeta termina", estado_final == "done", estado_final)

    _, tras = call(
        "GET",
        f"/api/v1/cards?system={referencia['system']}&external_id={referencia['id']}",
    )
    estados = {c["filename"]: c["state"] for c in tras.get("cards", [])}
    comprobar(
        "la busqueda la sigue encontrando ya terminada",
        estados.get(recuperado) == "done",
        str(estados),
    )

    print("\n=== 8. Limpieza ===")
    for pendiente, state in estados.items():
        if state in {"pending", "in-progress"}:
            call("POST", f"/api/v1/cards/{pendiente}/cancel",
                 {"reason": "fin de la prueba A08"}, key=str(uuid.uuid4()))
    call("POST", f"/api/v1/cards/{sin_ref}/cancel",
         {"reason": "fin de la prueba A08"}, key=str(uuid.uuid4()))
    print("    tarjetas de prueba canceladas")

    print(f"\n{'=' * 62}")
    print(f"VEREDICTO A08: {'CORRECTO' if not fallos else 'REVISAR ' + str(fallos)}")
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
