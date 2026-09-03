"""Headless AI-PC runner attached to the AI_Broker that the machine already runs."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from agora import __version__
from agora.broker.client import BrokerClient
from agora.broker.contracts import BrokerPolicy
from agora.broker.credentials import (
    BrokerConnection,
    EnvironmentSessionToken,
    KeyringSessionToken,
    SessionTokenUnavailable,
)
from agora.broker.executor import BrokerExecutor
from agora.broker.preflight import report, run_preflight
from agora.broker.runner import AiRunner
from agora.broker.supervisor import BrokerSupervisor
from agora.models import ModelCatalog
from agora.remote.client import AgoraApiClient


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agora-ai-runner")
    parser.add_argument("--version", action="version", version=f"agora {__version__}")
    parser.add_argument("api_url")
    parser.add_argument("--runner-id", required=True)
    parser.add_argument("--profile", action="append", required=True)
    parser.add_argument("--profiles-root", type=Path, required=True)
    parser.add_argument("--ca-cert", type=Path, required=True)
    parser.add_argument(
        "--pin-spki",
        help="anclaje a la clave pública del tablero, como lo imprime `agora-certs show`",
    )
    parser.add_argument(
        "--models",
        type=Path,
        help="catálogo models.yml; por defecto, el de la raíz de PROFILEs si existe",
    )
    parser.add_argument(
        "--broker-credential",
        choices=("keyring", "env", "supervise"),
        default="keyring",
        help=(
            "keyring: leer el token de sesión que publica el broker (despliegue real); "
            "env: token fijado por el operador; "
            "supervise: Agora lanza el broker como proceso hijo (sólo desarrollo)"
        ),
    )
    parser.add_argument("--broker-port", type=int, default=8765)
    parser.add_argument("--token-env", default="AGORA_API_TOKEN")
    parser.add_argument("--broker-token-env", default="AI_BROKER_ADMIN_TOKEN")
    parser.add_argument("--broker-keyring-service", default="ai-broker")
    parser.add_argument("--broker-keyring-username", default="session_admin_token")
    supervise_only = "sólo con --broker-credential supervise"
    parser.add_argument("--broker-repository", type=Path, help=supervise_only)
    parser.add_argument("--broker-config", type=Path, help=supervise_only)
    parser.add_argument("--broker-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="comprobar credencial, broker, tablero, PROFILEs y catálogo; no reclama nada",
    )
    return parser


def _supervisor(args: argparse.Namespace) -> BrokerSupervisor:
    if args.broker_repository is None or args.broker_config is None:
        raise SystemExit(
            "--broker-credential supervise requires --broker-repository and --broker-config"
        )
    repository = args.broker_repository.resolve()
    command = [
        str(args.broker_python.resolve()),
        str(repository / "scripts" / "run_broker.py"),
        "--config",
        str(args.broker_config.resolve()),
        "--host",
        "127.0.0.1",
        "--port",
        str(args.broker_port),
    ]
    return BrokerSupervisor(
        command,
        repository,
        token_env=args.broker_token_env,
        base_url=f"http://127.0.0.1:{args.broker_port}",
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    print(f"Agora AI runner {__version__} (runner-id {args.runner_id})", file=sys.stderr)
    agora_token = os.environ.get(args.token_env)
    if not agora_token:
        raise SystemExit(f"Missing Agora credential in environment variable {args.token_env}")

    base_url = f"http://127.0.0.1:{args.broker_port}"
    supervisor: BrokerSupervisor | None = None
    if args.broker_credential == "supervise":
        supervisor = _supervisor(args)
        broker = supervisor.start()
        connect: Callable[[], BrokerClient] = supervisor.restart
        origin = "supervised child process"
    else:
        source = (
            KeyringSessionToken(args.broker_keyring_service, args.broker_keyring_username)
            if args.broker_credential == "keyring"
            else EnvironmentSessionToken(args.broker_token_env)
        )
        connection = BrokerConnection(source, base_url=base_url)
        try:
            broker = connection.connect()
        except SessionTokenUnavailable as exc:
            raise SystemExit(f"No local AI_Broker credential: {exc}") from exc
        connect = connection.connect
        origin = connection.origin
    print(f"AI_Broker credential source: {origin}", file=sys.stderr)

    policy = BrokerPolicy()
    profiles_root = args.profiles_root.resolve()
    catalog = (
        ModelCatalog.load(args.models.resolve())
        if args.models is not None
        else ModelCatalog.discover(profiles_root)
    )
    print(
        "model catalog: "
        + (f"{catalog.source} ({', '.join(sorted(catalog.capacities))})" if catalog else "none"),
        file=sys.stderr,
    )
    with AgoraApiClient(
        args.api_url,
        agora_token,
        verify=str(args.ca_cert.resolve()),
        pin_spki_sha256=args.pin_spki,
    ) as agora:
        if args.check:
            checks = run_preflight(
                version=__version__,
                credential_origin=origin,
                broker=broker,
                agora=agora,
                profiles_root=profiles_root,
                catalog=catalog,
                profiles=tuple(args.profile),
            )
            broker.close()
            if supervisor is not None:
                supervisor.stop()
            return report(checks)
        executor = BrokerExecutor(broker, profiles_root, policy, catalog=catalog)
        runner = AiRunner(
            agora,
            broker,
            executor,
            args.runner_id,
            tuple(args.profile),
            renew_broker=connect,
        )
        try:
            while True:
                if supervisor is not None and not supervisor.running:
                    replacement = supervisor.restart()
                    runner.broker.close()
                    runner.broker = replacement
                    runner.executor.client = replacement
                outcome = runner.run_once()
                print(outcome.model_dump_json())
                if args.once:
                    return 0 if outcome.status in {"completed", "idle"} else 1
                time.sleep(max(0.1, args.poll_seconds))
        except KeyboardInterrupt:
            return 130
        finally:
            runner.broker.close()
            if supervisor is not None:
                supervisor.stop()


if __name__ == "__main__":
    raise SystemExit(main())
