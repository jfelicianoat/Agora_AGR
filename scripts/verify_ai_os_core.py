"""Recorrido real: AI OS Core decide qué tarjetas lleva el runner al AI_Broker de verdad.

Uso: python scripts/verify_ai_os_core.py <core_url> <token_agora> <token_director> <carpeta>
     con AI_BROKER_ADMIN_TOKEN en el entorno (opcional AGORA_VERIFY_MODEL, por defecto
     ollama/local/gemma4:12b).

El tablero es uno de usar y tirar en <carpeta> (el API de Agora en proceso, sin TLS), así
que el tablero real del usuario no se toca. El runner es el `AiRunner` real con su
`BrokerClient` real: este cumple el contrato de loopback y un transporte reenvía
127.0.0.1:8765 al broker del PC IA, sin relajar la restricción del código.
Core ha de tener las políticas de `sembrar_ecosistema.py` y el token por app de agora; el
token director (común) pone y quita la política DENY de la prueba.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from agora.api import ApiSettings, create_api  # noqa: E402
from agora.application import AgoraApplication  # noqa: E402
from agora.board import BoardState  # noqa: E402
from agora.broker.ai_os_core import AgoraCoreGate  # noqa: E402
from agora.broker.client import BrokerClient  # noqa: E402
from agora.broker.contracts import BrokerPolicy  # noqa: E402
from agora.broker.executor import BrokerExecutor, broker_idempotency_key  # noqa: E402
from agora.broker.runner import AiRunner  # noqa: E402
from agora.cards import Card  # noqa: E402
from conftest import create_card, write_profile  # noqa: E402
from test_ai_runner import InProcessAgoraClient  # noqa: E402

CORE, TOK_AGORA, TOK_DIR, WORK = sys.argv[1:5]
MODEL = os.environ.get("AGORA_VERIFY_MODEL", "ollama/local/gemma4:12b").split("/")
BROKER_LAN = "http://192.168.1.52:8765"
ok = 0


def step(name: str, cond: bool, detail: str = "") -> None:
    global ok
    print(f"[{'OK ' if cond else 'FALLO'}] {name} {detail}", flush=True)
    if not cond:
        sys.exit(1)
    ok += 1


class ForwardToAiPc(httpx.BaseTransport):
    """127.0.0.1:8765 -> broker del PC IA; el cliente sigue viendo loopback."""

    def __init__(self) -> None:
        self._inner = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        request.url = request.url.copy_with(host="192.168.1.52")
        return self._inner.handle_request(request)


work_dir = Path(WORK)
work_dir.mkdir(parents=True, exist_ok=True)
application = AgoraApplication(work_dir / f"board-{os.getpid()}")
application.initialize()
write_profile(application.profiles_root, "summarizer", handles=["summarize"], harness="broker")
api_token = "verify-ai-os-core-token-0123456789"
board_api = TestClient(
    create_api(application, ApiSettings(token=api_token, require_https=True)),
    base_url="https://testserver",
    headers={"Authorization": f"Bearer {api_token}"},
)
broker = BrokerClient(
    "http://127.0.0.1:8765", os.environ["AI_BROKER_ADMIN_TOKEN"], transport=ForwardToAiPc()
)
policy = BrokerPolicy(
    determinism="strict",
    timeout_seconds=600,
    zombie_timeout_seconds=900,
    target_model={"provider": MODEL[0], "deployment": MODEL[1], "model": MODEL[2]},
    max_output_tokens=1024,
)
runner = AiRunner(
    InProcessAgoraClient(board_api),  # type: ignore[arg-type]
    broker,
    BrokerExecutor(broker, application.profiles_root, policy),
    "verify-runner",
    ("summarizer",),
    core=AgoraCoreGate(CORE, TOK_AGORA),
)
director = httpx.Client(base_url=CORE, headers={"Authorization": f"Bearer {TOK_DIR}"}, timeout=10)
deny = {
    "policy_id": "verify_agora_deny",
    "policy_key": "verify_agora_deny",
    "scope": "agora:broker",
    "decision_if_matched": "DENY",
    "conditions": [{"field": "profile", "operator": "equals", "value": "summarizer"}],
}

step("broker real alcanzable por loopback", broker.health())
create_card(
    application.board,
    request="summarize document",
    body="Resume en una sola frase: Agora reparte tarjetas a agentes atómicos.",
)
step("director pone DENY", director.post("/api/v1/policies", json=deny).status_code in (200, 201))
outcome = runner.run_once()
pending = application.board.directory(BoardState.PENDING) / "task.md"
step("con DENY no se reclama", outcome.status == "core_denied", outcome.detail[:160])
step("con DENY la tarjeta sigue pendiente sin gastar intento", Card.load(pending).attempts == 0)
step(
    "director quita DENY",
    director.post("/api/v1/policies", json={**deny, "enabled": False}).status_code in (200, 201),
)

down = runner.core
runner.core = AgoraCoreGate("http://127.0.0.1:9", TOK_AGORA)
outcome = runner.run_once()
step("con Core caído no se reclama", outcome.status == "core_unavailable" and pending.exists())
runner.core = down

key = broker_idempotency_key(runner.agora.work(("summarizer",))[0])
outcome = runner.run_once()
step("con ALLOW la tarjeta va al broker real", outcome.status == "completed", outcome.detail[:200])
done = Card.load(application.board.directory(BoardState.DONE) / "task.md")
model = done.metadata.get("model")
step("el entregable vino del broker", bool(model), str(model))
receipt = director.post(
    "/api/v1/operations/status", json={"operation_id": key, "target_service_id": "ai_broker"}
).json()
step("Core tiene el recibo succeeded", receipt.get("status") == "succeeded", str(receipt)[:200])
print(f"\n{ok} comprobaciones OK")
