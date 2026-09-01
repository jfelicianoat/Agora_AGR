"""Local child-process supervisor owning the dynamic broker credential in memory."""

from __future__ import annotations

import os
import secrets
import subprocess
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from threading import Thread
from typing import TextIO

from agora.broker.client import BrokerClient


@dataclass(slots=True)
class BrokerSupervisor:
    command: Sequence[str]
    working_directory: Path
    token_env: str = "AI_BROKER_ADMIN_TOKEN"
    base_url: str = "http://127.0.0.1:8765"
    token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32)
    log_limit: int = 500
    _process: subprocess.Popen[str] | None = field(default=None, init=False, repr=False)
    _token: str | None = field(default=None, init=False, repr=False)
    _logs: deque[str] = field(default_factory=deque, init=False, repr=False)
    _reader: Thread | None = field(default=None, init=False, repr=False)

    def start(self) -> BrokerClient:
        if self._process is not None and self._process.poll() is None:
            raise RuntimeError("broker is already running under this supervisor")
        token = self.token_factory()
        if len(token) < 24:
            raise ValueError("generated broker credential is unexpectedly short")
        environment = os.environ.copy()
        environment[self.token_env] = token
        self._token = token
        self._logs = deque(maxlen=max(1, self.log_limit))
        self._process = subprocess.Popen(
            list(self.command),
            cwd=self.working_directory,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if self._process.stdout is not None:
            self._reader = Thread(target=self._capture, args=(self._process.stdout,), daemon=True)
            self._reader.start()
        return BrokerClient(self.base_url, token)

    def restart(self) -> BrokerClient:
        self.stop()
        return self.start()

    def stop(self, *, timeout: float = 10.0) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if self._reader is not None:
            self._reader.join(timeout=1)
        self._process = None
        self._reader = None
        self._token = None

    @property
    def logs(self) -> tuple[str, ...]:
        return tuple(self._logs)

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _capture(self, stream: TextIO) -> None:
        for line in stream:
            token = self._token
            safe = line.rstrip("\r\n")
            if token:
                safe = safe.replace(token, "[REDACTED]")
            self._logs.append(safe)

    def __enter__(self) -> BrokerClient:
        return self.start()

    def __exit__(self, *_args: object) -> None:
        self.stop()
