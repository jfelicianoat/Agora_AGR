"""AG-01: la puerta cumple sus criterios sobre los entregables sembrados."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_la_puerta_cumple_los_criterios_sobre_los_sembrados() -> None:
    spec = importlib.util.spec_from_file_location(
        "medir_review_gate", ROOT / "scripts" / "medir_review_gate.py"
    )
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    informe = modulo.medir()
    assert informe["incumplidos"] == []
    resumen = informe["resumen"]
    # Con el peor juez, solo pasan los defectos que únicamente un juez puede ver.
    assert resumen["complaciente"]["falsos_aceptados_deterministas"] == 0
    assert resumen["estricto"]["autoaprobados"] == 0


def test_el_modo_real_cuenta_falsos_aceptados_del_juez() -> None:
    """Con un juez que aprueba todo, el modo real señala los defectos que solo él podía ver."""
    import json as _json

    import httpx

    from agora.broker.client import BrokerClient

    spec = importlib.util.spec_from_file_location(
        "medir_review_gate", ROOT / "scripts" / "medir_review_gate.py"
    )
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    juicio = {
        "use_case": "agora_review_gate",
        "accepted": True,
        "decision": True,
        "confidence": 0.99,
        "confidence_is_calibrated": False,
        "alternatives": [{"value": False, "confidence": 0.01}],
        "provider": "ollama_system1",
        "model": "x",
        "latency_ms": 1.0,
        "fallback_used": False,
        "reason_code": None,
        "attempts": [
            {
                "provider": "ollama_system1",
                "model": "x",
                "latency_ms": 1.0,
                "reason_code": None,
                "tokens_input": 1,
                "tokens_output": 1,
            }
        ],
    }
    vistos: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        vistos.append(_json.loads(request.content)["input"]["goal"])
        return httpx.Response(200, json=juicio)

    client = BrokerClient(
        "http://127.0.0.1:8765", "t" * 20, transport=httpx.MockTransport(responder)
    )
    informe = modulo.medir_real(client)
    assert sorted(informe["falsos_aceptados"]) == [
        "I01-codigo-todo",
        "I05-cifra-contradice-evidencia",
        "I06-criterio-sin-cumplir",
        "I09-instruccion-incrustada",
    ]
    assert informe["falsos_rechazados"] == []
    assert len(vistos) == 7  # los deterministas no llegan al juez
    assert informe["incumplidos"]
