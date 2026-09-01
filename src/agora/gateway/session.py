"""Local broker session that renews the dynamic administrator credential once."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from agora.broker.client import BrokerApiError, BrokerClient


@dataclass(slots=True)
class BrokerSession:
    client: BrokerClient
    renew: Callable[[], BrokerClient] | None = field(default=None, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def call(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        current = self.client
        try:
            return getattr(current, operation)(*args, **kwargs)
        except BrokerApiError as exc:
            if exc.status_code not in {401, 403} or self.renew is None:
                raise
        with self._lock:
            if self.client is current:
                replacement = self.renew()
                self.client = replacement
                current.close()
            return getattr(self.client, operation)(*args, **kwargs)

    def close(self) -> None:
        self.client.close()
