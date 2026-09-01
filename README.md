# Agora

Agora is a Windows desktop-first system for durable work carried by atomic agents. The
filesystem board on the primary PC is the source of truth. Workers pull compatible cards,
claim them atomically, produce explicit artifacts, and leave a human-readable record.

The project is implemented in gated phases. The F0 core is deliberately offline and has
no Qt dependency. F1 adds an optional Windows desktop client on top of the same services.

## F0 development

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
