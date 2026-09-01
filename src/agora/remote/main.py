"""Single-round generic remote runner entry point."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from agora.remote.client import AgoraApiClient
from agora.remote.runner import DeterministicRemoteHarness, RemoteRunner


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agora-runner")
    parser.add_argument("api_url")
    parser.add_argument("--runner-id", required=True)
    parser.add_argument("--profile", action="append", required=True)
    parser.add_argument("--ca-cert", type=Path, required=True)
    parser.add_argument("--token-env", default="AGORA_API_TOKEN")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    token = os.environ.get(args.token_env)
    if not token:
        raise SystemExit(f"Missing API credential in environment variable {args.token_env}")
    with AgoraApiClient(args.api_url, token, verify=str(args.ca_cert.resolve())) as client:
        outcome = RemoteRunner(
            client,
            args.runner_id,
            tuple(args.profile),
            DeterministicRemoteHarness(),
        ).run_once()
    print(outcome.model_dump_json())
    return 0 if outcome.status in {"completed", "idle"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
