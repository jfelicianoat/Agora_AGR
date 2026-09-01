"""TLS-only Agora API server entry point."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from agora.api.app import ApiSettings, create_api
from agora.application import AgoraApplication
from agora.security.oauth import ClientRegistry, OAuthAuthority


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agora-api")
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8741)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--token-env", default="AGORA_API_TOKEN")
    parser.add_argument("--principal", default="provisional-client")
    parser.add_argument("--issuer")
    parser.add_argument("--oauth-clients", type=Path)
    parser.add_argument("--oauth-signing-key", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    token = os.environ.get(args.token_env)
    oauth_values = (args.issuer, args.oauth_clients, args.oauth_signing_key)
    if any(oauth_values) and not all(oauth_values):
        raise SystemExit(
            "--issuer, --oauth-clients and --oauth-signing-key must be provided together"
        )
    authority = None
    if all(oauth_values):
        issuer = args.issuer.rstrip("/")
        authority = OAuthAuthority(
            issuer,
            f"{issuer}/oauth/token",
            ClientRegistry(args.oauth_clients.resolve()),
            args.oauth_signing_key.resolve().read_bytes(),
        )
    if not token and authority is None:
        raise SystemExit(
            f"Configure OAuth or provide a credential in environment variable {args.token_env}"
        )
    app = create_api(
        AgoraApplication(args.workspace),
        ApiSettings(
            token=token,
            principal=args.principal,
            require_https=True,
            oauth_authority=authority,
        ),
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
