# Agora

Agora is a Windows desktop-first system for durable work carried by atomic agents. The
filesystem board on the primary PC is the source of truth. Workers pull compatible cards,
claim them atomically, produce explicit artifacts, and leave a human-readable record.

The project is implemented in gated phases. The F0 core is deliberately offline and has
no Qt dependency. F1 adds an optional Windows desktop client on top of the same services.

## Instalar y arrancar (usuario, no desarrollo)

Las dos mitades de Agora se instalan por separado, porque viven en máquinas
distintas: el **tablero** en el PC de trabajo y el **AI Runner** junto al
AI_Broker.

```powershell
# 1. Construir el paquete
.\scriptsuild-runner-package.ps1        # -> distgora-runner-<version>.zip

# 2. En el PC del tablero
.\scripts\install-board.ps1               # -> C:\Agora + accesos directos
```

`install-board.ps1` crea un venv aislado, inicializa el KANBAN, genera el token
del tablero en el Administrador de credenciales y deja dos lanzadores con
acceso directo en el Escritorio:

| Lanzador | Qué arranca |
|---|---|
| **Agora Desktop** | La ventana. Es la aplicación |
| **Agora Tablero** | La API HTTPS que sirve tarjetas al runner del PC IA |

La ventana se puede abrir sin el tablero: lee el KANBAN del disco. El tablero
sólo hace falta cuando el PC IA tiene que trabajar, y necesita certificado
(`agora-certs`) y una regla de cortafuegos para el puerto 8741.

Para el PC IA, copia `distgora-runner-<version>.zip` y sigue
`docs/INSTALAR_RUNNER.md`.

## F0 development

Para tarjetas explícitas de revisión de resultados, el AI Runner puede usar
System-1 a través del AI_Broker. La función requiere configuración explícita,
está deshabilitada por defecto y comienza en modo sombra. Véase
[la guía y el benchmark](docs/SYSTEM1_REVIEW_GATE.md).

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m mypy src tests
```

Create an empty board and inspect a dry run:

```powershell
agora init KANBAN
agora dispatch KANBAN AGENTS --dry-run
agora check-overlap AGENTS
```

## F1 desktop

Install the optional desktop dependency and open a workspace containing `KANBAN` and
`AGENTS`:

```powershell
python -m pip install -e ".[dev,desktop]"
agora-desktop .
```

The window never edits CARD files itself. It reads snapshots and requests dispatcher
actions through the Qt-free `AgoraApplication` façade, so the core and scheduler remain
usable without a graphical session.

## F2 HTTPS API and remote runner

Install the API dependencies, place a provisional credential in an environment variable,
and provide a certificate/key pair trusted by each runner:

```powershell
python -m pip install -e ".[api]"
$env:AGORA_API_TOKEN = Read-Host -MaskInput
agora-api . --host 127.0.0.1 --port 8741 --cert .\certs\server.pem --key .\certs\server-key.pem
```

On a runner machine, copy only the CA certificate—not the server key or BOARD—and run:

```powershell
$env:AGORA_API_TOKEN = Read-Host -MaskInput
agora-runner https://agora-host:8741 --runner-id worker-1 --profile summarizer --ca-cert .\certs\ca.pem
```

The API rejects plain HTTP, requires bearer authentication and idempotency keys for every
mutation, validates names before filesystem use, and remains the only remote owner of
BOARD writes. OAuth replaces the provisional shared credential in F5.
