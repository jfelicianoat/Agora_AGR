"""CLI de la PKI interna: `agora-certs`."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agora.security.certificates import (
    CA_CERTIFICATE,
    DEFAULT_CA_DAYS,
    DEFAULT_SERVER_DAYS,
    CertificateAuthority,
    CertificateError,
    describe,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agora-certs",
        description=(
            "CA privada para el TLS interno de Agora. La frase de paso de la CA se "
            "lee de AGORA_CA_PASSPHRASE, nunca de la línea de órdenes."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init-ca", help="crear la raíz de confianza")
    initialize.add_argument("--ca-dir", type=Path, required=True)
    initialize.add_argument("--common-name", default="Agora internal CA")
    initialize.add_argument("--days", type=int, default=DEFAULT_CA_DAYS)

    for name, help_text in (
        ("issue", "emitir el certificado del tablero"),
        ("renew", "renovar el certificado del tablero"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--ca-dir", type=Path, required=True)
        command.add_argument("--out", type=Path, required=True)
        command.add_argument(
            "--host",
            action="append",
            required=True,
            help="nombre DNS o IP por el que el runner llegará al tablero (repetible)",
        )
        command.add_argument("--days", type=int, default=DEFAULT_SERVER_DAYS)
        if name == "renew":
            command.add_argument(
                "--reuse-key",
                action="store_true",
                help="conservar la clave para no invalidar un anclaje SPKI existente",
            )

    show = commands.add_parser("show", help="ver caducidad y anclaje de un certificado")
    show.add_argument("certificate", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "show":
            print(json.dumps(describe(args.certificate).as_dict(), indent=2, ensure_ascii=False))
            return 0

        authority = CertificateAuthority(args.ca_dir.resolve())
        if args.command == "init-ca":
            path = authority.create(common_name=args.common_name, days=args.days)
            print(f"CA creada: {path}")
            print(f"Copia {CA_CERTIFICATE} al PC IA; la clave privada NO sale de esta máquina.")
            return 0

        certificate, key = authority.issue(
            tuple(args.host),
            args.out.resolve(),
            days=args.days,
            reuse_key=getattr(args, "reuse_key", False),
        )
        details = describe(certificate)
        print(f"certificado: {certificate}")
        print(f"clave      : {key}")
        print(f"hosts      : {', '.join(details.hosts)}")
        print(f"caduca     : {details.not_after} ({details.days_remaining} días)")
        print(f"anclaje    : --pin-spki {details.spki_sha256}")
        return 0
    except CertificateError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
