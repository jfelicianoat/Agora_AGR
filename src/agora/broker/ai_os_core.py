"""AI OS Core como director del runner: decide qué tarjetas pueden ir al AI_Broker.

Core es el plano de control del ecosistema del TFM. El runner no lo necesita: sin
`--core-url` todo sigue igual. Con él, antes de reclamar una tarjeta el runner evalúa
la política `agora:broker` (perfil y clasificación de datos del PROFILE) y solo se
lleva una con ALLOW ejecutable; las denegadas se quedan en la cola sin gastar intento,
por si la política cambia. Si Core no contesta no se reclama nada: quien configuró un
director no quiere que se le salte cuando se cae. Cada tarea enviada deja recibos en
Core (`accepted` al enviarla, `succeeded` o `failed_terminal` al cerrarla).
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any

import httpx

POLICY_SCOPE = "agora:broker"
#: Un salto para el monitor de Core nunca debe hacer esperar al runner.
HOP_TIMEOUT_SECONDS = 3.0
#: Cómo se presenta el runner ante Core (debe coincidir con su token por app).
PRINCIPAL = "agora"
#: Core guarda el token del Broker sólo en memoria: se repone cada minuto por si se reinició.
SHARE_EVERY_SECONDS = 60.0


class CoreUnavailable(RuntimeError):
    """Core no contestó o contestó un error: por seguridad no se reclama nada."""


class AgoraCoreGate:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        verify: str | bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("--core-url debe ser una URL http o https")
        headers = {"Accept": "application/json", "X-Principal-Id": PRINCIPAL}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=10.0, transport=transport,
            verify=verify,
        )
        self._clock = clock
        self._shared: tuple[str, float] | None = None

    def close(self) -> None:
        self._http.close()

    def decide(
        self, card: str, profile: str, data_classification: str, flow_id: str | None = None
    ) -> dict[str, Any]:
        """La decisión de Core para una tarjeta. Lanza `CoreUnavailable` si no la hay."""
        decision = self._post(
            "/api/v1/policies/evaluate",
            {
                "scope": POLICY_SCOPE,
                "principal_id": PRINCIPAL,
                "resource_id": card,
                "context": {
                    "operation_type": "card",
                    "profile": profile,
                    "data_classification": data_classification,
                    **({"flow_id": flow_id} if flow_id else {}),
                },
            },
        )
        return decision

    @staticmethod
    def allows(decision: dict[str, Any]) -> bool:
        return decision.get("authorization") == "ALLOW" and decision.get("executable") is True

    def receipt(self, operation_id: str, status: str, evidence: dict[str, Any]) -> None:
        """Un recibo perdido no tumba la tarjeta: Core verá el siguiente estado."""
        with contextlib.suppress(CoreUnavailable):
            self._post(
                "/api/v1/operations/receipts",
                {
                    "operation_id": operation_id,
                    "receiver_service": "ai_broker",
                    "status": status,
                    "evidence": evidence,
                },
            )

    def hop(
        self, flow_id: str, kind: str, summary: str = "", details: dict[str, Any] | None = None
    ) -> None:
        """Cuenta al monitor de Core lo que el runner manda al Broker. Enviar y olvidar."""
        body = {
            "flow_id": flow_id,
            "source_system": PRINCIPAL,
            "target_system": "ai_broker",
            "kind": kind,
            "summary": summary[:200],
            "details": details or {},
        }
        with contextlib.suppress(Exception):  # el monitor es secundario: jamás para una tarjeta
            self._http.post("/api/v1/flows/hops", json=body, timeout=HOP_TIMEOUT_SECONDS)

    def share_broker_token(self, token: str) -> bool:
        """Publica en Core el token de sesión del Broker para que las demás apps lo pidan.

        Sólo cuando cambia (el Broker se reinició) o ha pasado un minuto (Core pudo
        reiniciarse y lo perdió). Devuelve True si lo ha enviado. Un fallo no para el
        runner: lo vuelve a intentar en la siguiente vuelta.
        """
        now = self._clock()
        if self._shared is not None:
            last_token, last_at = self._shared
            if last_token == token and now - last_at < SHARE_EVERY_SECONDS:
                return False
        try:
            response = self._http.put("/api/v1/credentials/broker", json={"token": token})
        except httpx.HTTPError:
            return False
        if response.status_code >= 400:
            return False
        self._shared = (token, now)
        return True

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._http.post(path, json=body)
        except httpx.HTTPError as exc:
            raise CoreUnavailable(f"AI OS Core no contesta: {exc}") from exc
        if response.status_code >= 400:
            raise CoreUnavailable(
                f"AI OS Core respondió {response.status_code}: {response.text[:300]}"
            )
        result: dict[str, Any] = response.json()
        return result


def denied_text(decision: dict[str, Any]) -> str:
    authorization = decision.get("authorization")
    if authorization == "INDETERMINATE":
        missing = ", ".join(decision.get("unknown_inputs") or []) or "algún dato"
        return f"AI OS Core no puede decidir (le falta: {missing})"
    return f"AI OS Core no autoriza ({authorization}); revisa las políticas de «{POLICY_SCOPE}»"
