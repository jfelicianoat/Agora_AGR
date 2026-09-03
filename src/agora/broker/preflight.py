"""Diagnóstico del runner antes de dejarlo trabajar solo.

Un runner que arranca con la máquina y reclama tarjetas sin nadie delante falla
de formas que nadie ve: el llavero vacío, un certificado que no cubre la IP del
tablero, un broker con un contrato anterior. Esto lo comprueba de una vez y dice
en cuál de esos pasos se rompe, en vez de dejar un `403` sin explicación en un
log.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str

    def render(self) -> str:
        return f"[{'OK  ' if self.ok else 'FALLO'}] {self.name}: {self.detail}"


def _check(name: str, probe: Callable[[], str]) -> Check:
    try:
        return Check(name, True, probe())
    except Exception as exc:  # el diagnóstico no puede caerse por lo que diagnostica
        return Check(name, False, f"{type(exc).__name__}: {exc}")


def run_preflight(
    *,
    version: str,
    credential_origin: str,
    broker: Any,
    agora: Any,
    profiles_root: Path,
    catalog: Any,
    profiles: tuple[str, ...],
) -> tuple[Check, ...]:
    from agora.profiles import load_profiles

    checks: list[Check] = [Check("agora", True, f"version {version}")]

    checks.append(
        _check(
            "credencial del broker",
            lambda: f"leída de {credential_origin}",
        )
    )

    def broker_contract() -> str:
        if not broker.health():
            raise RuntimeError("/health/live did not answer 200")
        contract = broker.contract()
        listed = ".".join(str(part) for part in contract.version)
        missing = [
            flag
            for flag, present in (
                ("invocation_contract", contract.invocation_contract),
                ("prompt_compression_echo", contract.prompt_compression_echo),
                ("canonical_artifacts", contract.canonical_artifacts),
            )
            if not present
        ]
        if missing:
            return (
                f"contrato {listed}; SIN {', '.join(missing)} — "
                "las tarjetas estrictas se rechazarán"
            )
        return f"contrato {listed}, con las garantías de 2.10"

    checks.append(_check("AI_Broker en loopback", broker_contract))

    def board() -> str:
        payload = agora.health()
        return f"tablero accesible por TLS: {payload}"

    checks.append(_check("tablero de Agora", board))

    def declared() -> str:
        found = {item.name for item in load_profiles(profiles_root)}
        missing = sorted(set(profiles) - found)
        if missing:
            raise RuntimeError(f"PROFILE no encontrado en {profiles_root}: {', '.join(missing)}")
        return f"{len(found)} PROFILE en {profiles_root}; este runner sirve {', '.join(profiles)}"

    checks.append(_check("PROFILEs", declared))

    def capacities() -> str:
        if catalog is None:
            return "sin models.yml: todos los perfiles comparten la política del runner"
        return f"{catalog.source} ({', '.join(sorted(catalog.capacities))})"

    checks.append(_check("catálogo de capacidades", capacities))
    return tuple(checks)


def report(checks: tuple[Check, ...]) -> int:
    for check in checks:
        print(check.render())
    failed = [check for check in checks if not check.ok]
    if failed:
        print(f"\n{len(failed)} comprobación(es) fallan; el runner no puede trabajar solo.")
        return 1
    print("\nTodo listo: el runner puede reclamar tarjetas.")
    return 0
