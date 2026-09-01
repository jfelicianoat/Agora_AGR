"""Headless AI-PC runner with locally supervised AI_Broker credentials."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from agora.broker.client import BrokerClient
from agora.broker.contracts import BrokerPolicy
from agora.broker.executor import BrokerExecutor
from agora.broker.runner import AiRunner
from agora.broker.supervisor import BrokerSupervisor
from agora.remote.client import AgoraApiClient


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agora-ai-runner")
    parser.add_argument("api_url")
    parser.add_argument("--runner-id", required=True)
    parser.add_argument("--profile", action="append", required=True)
    parser.add_argument("--profiles-root", type=Path, required=True)
    parser.add_argument("--ca-cert", type=Path, required=True)
    parser.add_argument("--broker-repository", type=Path, required=True)
    parser.add_argument("--broker-config", type=Path, required=True)
    parser.add_argument("--broker-python", type=Path, default=Path(sys.executable))
    parser.add_argument("--broker-port", type=int, default=8765)
    parser.add_argument("--token-env", default="AGORA_API_TOKEN")
    parser.add_argument("--broker-token-env", default="AI_BROKER_ADMIN_TOKEN")
    parser.add_argument("--poll-seconds", type=float, default=15.0)
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    agora_token = os.environ.get(args.token_env)
    if not agora_token:
        raise SystemExit(f"Missing Agora credential in environment variable {args.token_env}")
    broker_script = args.broker_repository.resolve() / "scripts" / "run_broker.py"
    command = [
        str(args.broker_python.resolve()),
        str(broker_script),
        "--config",
        str(args.broker_config.resolve()),
        "--host",
        "127.0.0.1",
        "--port",
        str(args.broker_port),
    ]
    supervisor = BrokerSupervisor(
        command,
        args.broker_repository.resolve(),
        token_env=args.broker_token_env,
        base_url=f"http://127.0.0.1:{args.broker_port}",
    )
    broker = supervisor.start()
    policy = BrokerPolicy()
    with AgoraApiClient(
        args.api_url,
        agora_token,
        verify=str(args.ca_cert.resolve()),
    ) as agora:
        executor = BrokerExecutor(broker, args.profiles_root.resolve(), policy)

        def renew() -> BrokerClient:
            return supervisor.restart()

        runner = AiRunner(
            agora,
            broker,
            executor,
            args.runner_id,
            tuple(args.profile),
            renew_broker=renew,
        )
        try:
            while True:
                if not supervisor.running:
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
            supervisor.stop()


if __name__ == "__main__":
    raise SystemExit(main())
