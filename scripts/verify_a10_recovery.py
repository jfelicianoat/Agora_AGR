"""A10 contra el sistema real: tirar el tablero con una tarjeta en vuelo.

Nota de uso: los subprocesos van con `stdin=DEVNULL`. Sin eso, el PowerShell
hijo se queda esperando en una consola sin terminal y el guion no avanza —se
midio: 0,07 s de CPU y bloqueado indefinidamente—.

Las pruebas unitarias cubren el runner reiniciado y el broker caido con un
broker falso. Lo que ahi no se puede probar es lo otro: que **el tablero se
muera mientras el runner esta trabajando** y que, al volver, el trabajo siga su
curso sin duplicarse ni perderse.

Aqui se hace de verdad: se mata el proceso del tablero, se levanta otra vez, y
se mira si la tarjeta llega a `done` con un solo artefacto.
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
WORKSPACE = r"C:\Agora\workspace"
CERT = r"C:\Agora\pki\server.crt"
KEY = r"C:\Agora\pki\server.key"


def run_ps(script: str) -> str:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return (result.stdout or "").strip()


def board_token() -> str:
    token = subprocess.run(
        [BOARD_PYTHON, "-c",
         "import keyring; print(keyring.get_password('agora-board','api_token') or '')"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
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
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
        return 0, {"detail": f"{type(error).__name__}: {error}"}


def board_pid() -> str:
    return run_ps(
        "$c = Get-NetTCPConnection -LocalPort 8741 -State Listen "
        "-ErrorAction SilentlyContinue; if ($c) { $c[0].OwningProcess } else { '' }"
    )


def stop_board() -> None:
    pid = board_pid()
    if pid:
        run_ps(f"Stop-Process -Id {pid} -Force -ErrorAction SilentlyContinue")
    for _ in range(20):
        if not board_pid():
            return
        time.sleep(0.5)
    raise SystemExit("el tablero no se ha parado")


def start_board() -> None:
    run_ps(
        f"$t = & '{BOARD_PYTHON}' -c \"import keyring; "
        "print(keyring.get_password('agora-board','api_token') or '')\"; "
        "$env:AGORA_API_TOKEN = $t.Trim(); "
        f"Start-Process -FilePath '{BOARD_PYTHON}' -ArgumentList "
        f"'-m','agora.api.main','{WORKSPACE}','--host','0.0.0.0','--port','8741',"
        f"'--cert','{CERT}','--key','{KEY}' -WorkingDirectory 'C:\\Agora' "
        "-WindowStyle Minimized -RedirectStandardOutput 'C:\\Agora\\board.log' "
        "-RedirectStandardError 'C:\\Agora\\board.err.log'"
    )
    for _ in range(40):
        if board_pid() and call("GET", "/api/v1/health")[0] == 200:
            return
        time.sleep(1)
    raise SystemExit("el tablero no ha vuelto")


def main() -> int:
    fallos: list[str] = []

    def comprobar(nombre: str, ok: bool, detalle: str = "") -> None:
        print(f"  [{'ok' if ok else 'FALLA'}] {nombre}{f' — {detalle}' if detalle else ''}")
        if not ok:
            fallos.append(nombre)

    marca = uuid.uuid4().hex[:8]
    nombre = f"a10-{marca}.md"
    referencia = {"system": "cliente-recuperacion", "id": f"tarea-{marca}", "version": 1}

    print("=== 1. Estados terminales, tal y como los ve el cliente ===")
    efimera = f"a10-{marca}-cancelada.md"
    call("POST", "/api/v1/cards", {
        "filename": efimera, "function": "decompose",
        "request": "break down work: algo que se va a cancelar",
    }, key=str(uuid.uuid4()))
    _, viva = call("GET", f"/api/v1/cards/{efimera}")
    comprobar("una tarjeta en vuelo no es terminal", viva.get("terminal") is False,
              f"{viva.get('state')} terminal={viva.get('terminal')}")
    call("POST", f"/api/v1/cards/{efimera}/cancel", {"reason": "prueba A10"},
         key=str(uuid.uuid4()))
    _, muerta = call("GET", f"/api/v1/cards/{efimera}")
    comprobar("una cancelada si lo es", muerta.get("terminal") is True,
              f"{muerta.get('state')} terminal={muerta.get('terminal')}")

    print("\n=== 2. Crear trabajo y esperar a que el runner lo tenga en la mano ===")
    status, _ = call("POST", "/api/v1/cards", {
        "filename": nombre, "function": "decompose",
        "request": "break down work: preparar la memoria del TFM",
        "body": "Redactar la memoria completa del TFM, capitulo por capitulo.\n",
        "external_reference": referencia,
    }, key=str(uuid.uuid4()))
    comprobar("crear devuelve 201", status == 201, str(status))

    deadline = time.time() + 600
    while time.time() < deadline:
        _, estado = call("GET", f"/api/v1/cards/{nombre}")
        if estado.get("state") == "in-progress":
            break
        time.sleep(5)
    comprobar("el runner la ha reclamado", estado.get("state") == "in-progress",
              str(estado.get("state")))
    if estado.get("state") != "in-progress":
        print(f"\nFALLOS: {fallos}")
        return 1
    task_id = (estado.get("metadata", {}).get("remote") or {}).get("task_id")
    print(f"    tarea del broker en curso: {task_id}")

    print("\n=== 3. Matar el tablero con el trabajo en vuelo ===")
    pid = board_pid()
    print(f"    matando PID {pid}")
    stop_board()
    status, _ = call("GET", "/api/v1/health")
    comprobar("el tablero esta caido de verdad", status == 0, f"status={status}")
    time.sleep(20)

    print("\n=== 4. Levantarlo otra vez ===")
    start_board()
    status, salud = call("GET", "/api/v1/health")
    comprobar("el tablero responde", status == 200, str(salud))
    _, tras = call("GET", f"/api/v1/cards/{nombre}")
    comprobar("la tarjeta sigue donde estaba", tras.get("state") in {"in-progress", "done"},
              str(tras.get("state")))
    comprobar("y conserva su tarea del broker",
              (tras.get("metadata", {}).get("remote") or {}).get("task_id") == task_id)

    print("\n=== 5. El trabajo termina igual ===")
    deadline = time.time() + 1800
    while time.time() < deadline:
        _, estado = call("GET", f"/api/v1/cards/{nombre}")
        if estado.get("terminal") or estado.get("state") == "blocked":
            break
        time.sleep(15)
    comprobar("la tarjeta llega a done", estado.get("state") == "done",
              str(estado.get("state")))
    comprobar("y el API la marca terminal", estado.get("terminal") is True)

    rutas = estado.get("metadata", {}).get("paths") or []
    comprobar("un solo artefacto, no duplicado", len(rutas) == 1, str(rutas))
    comprobar("no se gastaron intentos de mas",
              int(estado.get("metadata", {}).get("attempts", 99)) <= 1,
              str(estado.get("metadata", {}).get("attempts")))

    print("\n=== 6. La reconciliacion sobrevive al reinicio ===")
    _, encontrado = call(
        "GET",
        f"/api/v1/cards?system={referencia['system']}&external_id={referencia['id']}",
    )
    comprobar("se encuentra por su referencia externa",
              [c["filename"] for c in encontrado.get("cards", [])] == [nombre],
              str(encontrado.get("cards")))

    print("\n=== 7. Los eventos del reinicio siguen el cursor ===")
    _, pagina = call("GET", "/api/v1/events?after=0&limit=1000")
    mios = [e for e in pagina["events"] if e["data"].get("filename") == nombre]
    comprobar("se ve todo el ciclo pese al corte",
              {"card.created", "card.claimed", "card.closed"} <= {e["kind"] for e in mios},
              str(sorted({e["kind"] for e in mios})))
    comprobar("el ultimo evento dice que acabo",
              bool(mios) and mios[-1]["data"].get("terminal") is True,
              str(mios[-1]["data"]) if mios else "sin eventos")
    comprobar("el cursor no denuncia huecos", pagina["missed"] is False)

    print("\n=== 8. Limpieza ===")
    print("    (las tarjetas de prueba quedan en done/archive)")

    print(f"\n{'=' * 62}")
    print(f"VEREDICTO A10: {'CORRECTO' if not fallos else 'REVISAR ' + str(fallos)}")
    return 0 if not fallos else 1


if __name__ == "__main__":
    raise SystemExit(main())
