"""TLS Gateway entry point with a locally supervised AI_Broker child process."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from agora.broker.supervisor import BrokerSupervisor
from agora.gateway.app import GatewaySettings, create_gateway
from agora.gateway.session import BrokerSession
from agora.security.oauth import ClientRegistry, OAuthAuthority


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agora-gateway")
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--oauth-clients", type=Path, required=True)
    parser.add_argument("--oauth-signing-key", type=Path, required=True)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--broker-repository", type=Path, required=True)
    parser.add_argument("--broker-config", type=Path, required=True)
    parser.add_argument("--broker-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--broker-port", type=int, default=8765)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8742)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    issuer = args.issuer.rstrip("/")
    if not issuer.startswith("https://"):
        raise SystemExit("--issuer must use HTTPS")
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
    authority = OAuthAuthority(
        issuer,
        f"{issuer}/oauth/token",
        ClientRegistry(args.oauth_clients.resolve()),
        args.oauth_signing_key.resolve().read_bytes(),
    )
    supervisor = BrokerSupervisor(
        command,
        repository,
        base_url=f"http://127.0.0.1:{args.broker_port}",
    )
    session = BrokerSession(supervisor.start(), renew=supervisor.restart)
    app = create_gateway(session, GatewaySettings(authority))
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            ssl_certfile=str(args.cert.resolve()),
            ssl_keyfile=str(args.key.resolve()),
            access_log=False,
        )
    finally:
        session.close()
        supervisor.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
