"""TLS-only Agora API server entry point."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from agora.api.app import ApiSettings, create_api
from agora.application import AgoraApplication


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agora-api")
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8741)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--token-env", default="AGORA_API_TOKEN")
    parser.add_argument("--principal", default="provisional-client")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    token = os.environ.get(args.token_env)
    if not token:
        raise SystemExit(f"Missing API credential in environment variable {args.token_env}")
    app = create_api(
        AgoraApplication(args.workspace),
        ApiSettings(token=token, principal=args.principal, require_https=True),
    )
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        ssl_certfile=str(args.cert.resolve()),
        ssl_keyfile=str(args.key.resolve()),
        access_log=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
