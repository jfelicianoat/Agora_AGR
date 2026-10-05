"""Offline control-flow benchmark using the same explicit mocks as the gate tests.

Times are local client/validation times. Tokens are simulated fixture values;
this benchmark makes no inference-speed or monetary-saving claims.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from test_system1_review_gate import System1Broker, context, execute, gate_config  # noqa: E402


def scenario(name: str) -> tuple[System1Broker, dict[str, Any], dict[str, Any]]:
    broker = System1Broker()
    data = context()
    config: dict[str, Any] = {}
    if name == "Código incompleto":
        data.update(task_type="code", agent_output="TODO: implement function")
        broker.judgment.update(decision=False, alternatives=[{"value": True, "confidence": 0.01}])
    elif name == "Tests fallidos":
        data["verifications"][0]["status"] = "failed"
    elif name == "Confianza 0.96":
        broker.judgment["confidence"] = 0.96
    elif name == "Broker de juicio caído":
        broker.judge_failure = "offline"
    elif name == "Ollama falla, LAYA acepta":
        broker.judgment.update(provider="laya_mcp", fallback_used=True, reason_code="TIMEOUT")
        broker.judgment["attempts"].insert(
            0,
            {
                "provider": "ollama_system1",
                "model": None,
                "latency_ms": 1.0,
                "reason_code": "TIMEOUT",
                "tokens_input": 5,
                "tokens_output": 0,
            },
        )
        broker.judgment["attempts"][1]["provider"] = "laya_mcp"
    elif name == "Feature off":
        config["enabled"] = False
    elif name == "Shadow mode":
        config["shadow_mode"] = True
    elif name == "Respuesta inválida":
        broker.judgment["decision"] = "true"
    elif name == "Revisión obligatoria":
        data["mandatory_review"] = True
    elif name == "Score sin calibrar, política conservadora":
        config["uncalibrated_policy"] = "review"
    return broker, data, config


def run(iterations: int) -> dict[str, Any]:
    names = [
        "Informe correcto",
        "Código incompleto",
        "Tests fallidos",
        "Confianza 0.96",
        "Broker de juicio caído",
        "Ollama falla, LAYA acepta",
        "Feature off",
        "Shadow mode",
        "Respuesta inválida",
        "Revisión obligatoria",
        "Score sin calibrar, política conservadora",
    ]
    rows: list[dict[str, Any]] = []
    scratch_root = (ROOT / "tmp").resolve()
    if not scratch_root.is_relative_to(ROOT):
        raise ValueError("benchmark scratch directory must stay inside Agora")
    scratch_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gate-benchmark-", dir=scratch_root) as directory:
        assert Path(directory).resolve().parent == scratch_root
        for index, name in enumerate(names):
            elapsed: list[float] = []
            baseline_elapsed: list[float] = []
            for repeat in range(iterations):
                broker, data, config = scenario(name)
                start = time.perf_counter()
                result, gate, _ = execute(
                    Path(directory) / f"gate-{index}-{repeat}",
                    broker,
                    data=data,
                    config=gate_config(**config),
                )
                elapsed.append((time.perf_counter() - start) * 1000)
                baseline, baseline_data, _ = scenario(name)
                start = time.perf_counter()
                execute(
                    Path(directory) / f"baseline-{index}-{repeat}",
                    baseline,
                    data=baseline_data,
                    config=gate_config(enabled=False),
                )
                baseline_elapsed.append((time.perf_counter() - start) * 1000)
            # Feature off is the previous flow exactly: the gate leaves no audit.
            audit = result.audit.get("review_gate") or {
                "tokens_input": None,
                "tokens_output": None,
                "confidence": None,
                "reason": None,
                "reviewer_executed": bool(broker.submissions),
                "reviewer_skipped": False,
            }
            judgment_tokens = (
                audit["tokens_input"] + audit["tokens_output"]
                if audit["tokens_input"] is not None and audit["tokens_output"] is not None
                else (0 if not broker.judgments else None)
            )
            reviewer_tokens = 120 if broker.submissions else 0  # Declared mock invocation usage.
            rows.append(
                {
                    "scenario": name,
                    "deterministic_validation": data["verifications"][0]["status"],
                    "confidence": audit["confidence"],
                    "reason": audit["reason"],
                    "reviewer_executed": audit["reviewer_executed"],
                    "result": "Aprobación gate"
                    if audit["reviewer_skipped"]
                    else "Entrega reviewer mock",
                    "baseline_client_ms_median": statistics.median(baseline_elapsed),
                    "gate_client_ms_median": statistics.median(elapsed),
                    "baseline_tokens_simulated": 120,
                    "gate_tokens_simulated": (
                        judgment_tokens + reviewer_tokens if judgment_tokens is not None else None
                    ),
                    "metrics_last_iteration": gate.metrics.snapshot(),
                }
            )
    return {
        "measurement_kind": "http_mock_transport",
        "iterations_per_scenario": iterations,
        "inference_time_saved_ms": None,
        "production_token_savings": None,
        "notes": "Local client times only; model token counts are explicitly simulated.",
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "SYSTEM1_BENCHMARK.json")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("iterations must be positive")
    report = run(args.iterations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Benchmark System-1 — simulación local",
        "",
        "Respuestas HTTP y tokens simulados. Tiempos medidos del cliente local, sin inferencia.",
        "La autoaprobación de este ensayo permite explícitamente scores sin calibrar.",
        "En código incompleto, la decisión es false con confianza 0.99: no es una aprobación.",
        "— significa que no se dispone de una medida; no equivale a cero.",
        "",
        "| Escenario | Validación | Score | Reviewer | Resultado | Cliente ms antes → gate | "
        "Tokens mock antes → gate |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in report["rows"]:
        score = row["confidence"] if row["confidence"] is not None else "—"
        tokens = row["gate_tokens_simulated"]
        tokens_label = tokens if tokens is not None else "—"
        lines.append(
            f"| {row['scenario']} | {row['deterministic_validation']} | {score} | "
            f"{'Sí' if row['reviewer_executed'] else 'No'} | {row['result']} | "
            f"{row['baseline_client_ms_median']:.2f} → {row['gate_client_ms_median']:.2f} | "
            f"{row['baseline_tokens_simulated']} → {tokens_label} |"
        )
    args.output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(report['rows'])} scenarios, {args.iterations} repeats each: {args.output}")


if __name__ == "__main__":
    main()
