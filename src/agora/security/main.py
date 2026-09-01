"""Minimal local administration CLI for OAuth keys and client grants."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from agora.documents import atomic_write_text
from agora.security.keys import generate_private_key, public_jwk
from agora.security.oauth import ClientRecord, ClientRegistry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agora-oauth")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("init-authority")
    initialize.add_argument("--registry", type=Path, required=True)
    initialize.add_argument("--signing-key", type=Path, required=True)
    for name in ("register-client", "rotate-client"):
        command = commands.add_parser(name)
        command.add_argument("client_id")
        command.add_argument("--registry", type=Path, required=True)
        command.add_argument("--private-key", type=Path, required=True)
        if name == "register-client":
            command.add_argument("--scope", action="append", required=True)
            command.add_argument("--audience", action="append", required=True)
    revoke = commands.add_parser("revoke-client")
    revoke.add_argument("client_id")
    revoke.add_argument("--registry", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init-authority":
        if args.registry.exists() or args.signing_key.exists():
            raise SystemExit("Refusing to overwrite existing OAuth material")
        _write_secret(args.signing_key, generate_private_key())
        atomic_write_text(args.registry, '{"version": 1, "clients": []}\n')
        return 0
    registry = ClientRegistry(args.registry)
    if args.command == "revoke-client":
        registry.revoke(args.client_id)
        return 0
    if args.private_key.exists():
        raise SystemExit("Refusing to overwrite an existing client private key")
    private_key = generate_private_key()
    if args.command == "register-client":
        registry.register(
            ClientRecord(
                args.client_id,
                public_jwk(private_key),
                tuple(sorted(set(args.scope))),
                tuple(sorted(set(args.audience))),
            )
        )
    else:
        registry.rotate(args.client_id, public_jwk(private_key))
    _write_secret(args.private_key, private_key)
    return 0


def _write_secret(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
