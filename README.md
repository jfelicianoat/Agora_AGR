# Agora

Agora is a Windows desktop-first system for durable work carried by atomic agents. The
filesystem board on the primary PC is the source of truth. Workers pull compatible cards,
claim them atomically, produce explicit artifacts, and leave a human-readable record.

The project is implemented in gated phases. F0 is deliberately offline: no network, Qt,
OAuth, A2A, AI_Broker, Directors, or Wake-on-LAN.

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

