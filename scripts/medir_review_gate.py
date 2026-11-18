"""AG-01 (plan 13-nov-2026): falsos aceptados y falsos rechazados de la puerta System-1.

Pasa cada entregable sembrado de `benchmarks/review_gate/sembrados_v1.json` por la
puerta real (`ReviewGate` + `BrokerExecutor`) con cinco jueces simulados:

- perfecto: dice la verdad sobre el contenido, con confianza 0,99;
- complaciente: aprueba todo con 0,99 (el peor juez posible);
- dudoso: aprueba todo con 0,90, por debajo del umbral;
- estricto: rechaza todo con 0,99;
- caido: el Broker de juicio no responde.

La configuración es la más arriesgada (activa, sin sombra, acepta scores sin
calibrar). Lo que mide es la POLÍTICA de la puerta. La calidad del juez real no se
mide aquí: para eso hay que lanzar los mismos casos contra el Broker desde el PC IA.

Sale con código 1 si se incumple un criterio de `criterios`.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from test_system1_review_gate import System1Broker, execute, gate_config  # noqa: E402

JUECES = ("perfecto", "complaciente", "dudoso", "estricto", "caido")
SEMBRADOS = ROOT / "benchmarks" / "review_gate" / "sembrados_v1.json"


def _juez(nombre: str, contenido_correcto: bool) -> System1Broker:
    broker = System1Broker()
    if nombre == "caido":
        broker.judge_failure = "offline"
        return broker
    decision = {
        "perfecto": contenido_correcto,
        "complaciente": True,
        "dudoso": True,
        "estricto": False,
    }[nombre]
    confianza = 0.90 if nombre == "dudoso" else 0.99
    broker.judgment.update(
        decision=decision,
        confidence=confianza,
        alternatives=[{"value": not decision, "confidence": round(1 - confianza, 2)}],
    )
    return broker


def medir(sembrados: Path = SEMBRADOS) -> dict[str, Any]:
    datos = json.loads(sembrados.read_text(encoding="utf-8"))
    filas: list[dict[str, Any]] = []
    scratch = ROOT / "tmp"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="medir-gate-", dir=scratch) as carpeta:
        for juez in JUECES:
            for caso in datos["casos"]:
                broker = _juez(juez, caso["contenido_correcto"])
                resultado, _, _ = execute(
                    Path(carpeta) / f"{juez}-{caso['id']}",
                    broker,
                    data=caso["contexto"],
                    config=gate_config(),
                )
                audit = resultado.audit["review_gate"]
                filas.append(
                    {
                        "juez": juez,
                        "caso": caso["id"],
                        "esperado": caso["esperado"],
                        "defecto_determinista": caso["defecto_determinista"],
                        "autoaprobado": bool(audit["reviewer_skipped"]),
                        "motivo": audit["reason"],
                    }
                )
    resumen: dict[str, dict[str, int]] = {}
    for juez in JUECES:
        suyas = [f for f in filas if f["juez"] == juez]
        resumen[juez] = {
            "falsos_aceptados": sum(
                f["autoaprobado"] and f["esperado"] == "revisor" for f in suyas
            ),
            "falsos_aceptados_deterministas": sum(
                f["autoaprobado"] and f["defecto_determinista"] for f in suyas
            ),
            "falsos_rechazados": sum(
                not f["autoaprobado"] and f["esperado"] == "aprobable" for f in suyas
            ),
            "autoaprobados": sum(f["autoaprobado"] for f in suyas),
            "casos": len(suyas),
        }
    criterios = datos["criterios"]
    incumplidos = []
    if any(
        r["falsos_aceptados_deterministas"] > criterios["falsos_aceptados_deterministas_max"]
        for r in resumen.values()
    ):
        incumplidos.append("un defecto determinista llegó a autoaprobarse")
    if resumen["perfecto"]["falsos_aceptados"] > criterios["falsos_aceptados_juez_perfecto_max"]:
        incumplidos.append("con juez perfecto hubo falsos aceptados")
    if resumen["perfecto"]["falsos_rechazados"] > criterios["falsos_rechazados_juez_perfecto_max"]:
        incumplidos.append("con juez perfecto hubo falsos rechazados")
    if (
        max(resumen["dudoso"]["autoaprobados"], resumen["caido"]["autoaprobados"])
        > criterios["autoaprobaciones_con_juez_dudoso_o_caido_max"]
    ):
        incumplidos.append("se autoaprobó con juez dudoso o caído")
    return {
        "version_sembrados": datos["version"],
        "configuracion": "enabled, sin sombra, threshold 0.97, uncalibrated_policy=allow",
        "mide": "política de la puerta con jueces simulados; no la calidad del juez real",
        "resumen": resumen,
        "criterios": criterios,
        "incumplidos": incumplidos,
        "filas": filas,
    }


def medir_real(client: Any, sembrados: Path = SEMBRADOS) -> dict[str, Any]:
    """Los mismos sembrados contra el juez System-1 real del Broker (PC IA, loopback).

    Solo se le pasan los casos que llegarían al juez: los defectos deterministas la
    puerta los rechaza antes, y eso ya lo fija `medir`. Una autoaprobación exige lo
    mismo que la puerta: aceptado, decisión true y confianza >= umbral.
    """
    from agora.broker.review_gate import ReviewGateConfig

    datos = json.loads(sembrados.read_text(encoding="utf-8"))
    config = ReviewGateConfig()
    filas: list[dict[str, Any]] = []
    for caso in datos["casos"]:
        if caso["defecto_determinista"]:
            continue
        ctx = caso["contexto"]
        fila: dict[str, Any] = {"caso": caso["id"], "esperado": caso["esperado"]}
        try:
            juicio = client.judge_system1(
                use_case=config.use_case,
                inputs={
                    "goal": ctx["goal"],
                    "acceptance_criteria": ctx["acceptance_criteria"],
                    "agent_output": ctx["agent_output"],
                    "verifications": ctx["verifications"],
                    "evidence": ctx["evidence"],
                    "metadata": {"task_type": ctx["task_type"]},
                },
                instructions=config.instructions,
                timeout_seconds=config.timeout_seconds,
            )
            fila.update(
                decision=juicio.decision,
                confianza=juicio.confidence,
                proveedor=juicio.provider,
                autoaprobaria=bool(
                    juicio.accepted
                    and juicio.decision is True
                    and juicio.confidence is not None
                    and juicio.confidence >= config.threshold
                ),
            )
        except Exception as error:  # un fallo del juez nunca autoaprueba: se informa
            fila.update(autoaprobaria=False, error=f"{type(error).__name__}: {error}")
        filas.append(fila)
    falsos_aceptados = [
        f["caso"] for f in filas if f["autoaprobaria"] and f["esperado"] == "revisor"
    ]
    falsos_rechazados = [
        f["caso"] for f in filas if not f["autoaprobaria"] and f["esperado"] == "aprobable"
    ]
    maximo = datos["criterios"]["falsos_aceptados_juez_real_max"]
    return {
        "version_sembrados": datos["version"],
        "mide": "el juez System-1 real del Broker sobre los casos que llegan a él",
        "umbral": config.threshold,
        "falsos_aceptados": falsos_aceptados,
        "falsos_rechazados": falsos_rechazados,
        "incumplidos": (
            [f"{len(falsos_aceptados)} falsos aceptados (máximo {maximo})"]
            if len(falsos_aceptados) > maximo
            else []
        ),
        "filas": filas,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sembrados", type=Path, default=SEMBRADOS)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "REVIEW_GATE_MEDICION.json")
    parser.add_argument(
        "--real",
        action="store_true",
        help="en el PC IA: preguntar al juez real del Broker (token del llavero, como el runner)",
    )
    parser.add_argument("--broker-url", default="https://127.0.0.1:8765")
    parser.add_argument("--ca", default=None, help="CA que firma el certificado del Broker")
    args = parser.parse_args()
    if args.real:
        from agora.broker.client import BrokerClient
        from agora.broker.credentials import KeyringSessionToken

        client = BrokerClient(args.broker_url, KeyringSessionToken().read(), verify=args.ca or True)
        real = medir_real(client, args.sembrados)
        salida = args.output.with_name("REVIEW_GATE_MEDICION_REAL.json")
        salida.write_text(json.dumps(real, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {k: real[k] for k in ("falsos_aceptados", "falsos_rechazados", "incumplidos")},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if real["incumplidos"] else 0
    informe = medir(args.sembrados)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(informe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lineas = [
        "# Puerta System-1: falsos aceptados y rechazados (AG-01)",
        "",
        f"Sembrados `{informe['version_sembrados']}` · configuración: {informe['configuracion']}.",
        f"Mide la {informe['mide']}.",
        "",
        "| Juez | Autoaprobados | Falsos aceptados | De ellos deterministas | Falsos rechazados |",
        "|---|---|---|---|---|",
    ]
    for juez, r in informe["resumen"].items():
        lineas.append(
            f"| {juez} | {r['autoaprobados']}/{r['casos']} | {r['falsos_aceptados']} | "
            f"{r['falsos_aceptados_deterministas']} | {r['falsos_rechazados']} |"
        )
    lineas += ["", "Incumplidos: " + ("; ".join(informe["incumplidos"]) or "ninguno") + "."]
    args.output.with_suffix(".md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    print("\n".join(lineas))
    return 1 if informe["incumplidos"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
