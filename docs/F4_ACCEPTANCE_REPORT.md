# RESULTADO DE FASE F4 — Integración opcional con Athena

## Implementado

- Adaptador opcional Athena -> Agora sobre la API autenticada existente: `submit`, `status`, `cancel` y recuperación de artifacts.
- Runner/harness local Agora -> Athena sobre el servicio loopback real de Athena.
- Composición controlada `PROFILE -> SKILLS declaradas -> CARD` en modo CARD no interactivo.
- Disponibilidad autenticada antes del claim: si Athena no está, la CARD permanece `pending` y no consume intentos.
- Capacidades `writes` y `exec` tomadas exclusivamente del PROFILE; la CARD no puede ampliarlas.
- `ask` rechazado antes de crear el run.
- Cierre sólo con estado Athena `completed`, verificación `passed` y deliverables reales, no vacíos y confinados al workspace.
- Verificación inconclusa aplica la failure policy de Agora y nunca produce falso `done`.
- Estado, cancelación durable a `archive` y descarga segura de artifacts en la API de Agora.
- `origin` de CARD remota derivado de la identidad autenticada, nunca del payload.

## Arquitectura real resultante

```text
Athena (opcional) ──HTTPS/Bearer──> Agora API ──> BOARD filesystem
       submit/status/cancel/artifacts          único propietario

Agora Dispatcher ──AthenaHarness──HTTP loopback/Bearer──> Athena Service
       PROFILE policy                    AgentLoop existente
       CARD mode                         ModelProvider existente
```

Agora no importa paquetes de Athena. Athena no importa Agora y no contiene cambios. El adaptador usa el wire contract v1 ya publicado por el servicio de Athena; ADR-002 permanece intacto porque AI_Broker sigue siendo un `ModelProvider` opcional detrás de la frontera neutral de Athena.

## Archivos

- `src/agora/integrations/athena.py`
- `src/agora/dispatcher.py`
- `src/agora/board.py`
- `src/agora/api/app.py`
- `src/agora/api/service.py`
- `src/agora/remote/client.py`
- `tests/test_athena_integration.py`

## Pruebas Agora

```powershell
python -m pytest -q --basetemp D:\Desarrollo\Proyectos TFM\Agora\tmp\pytest-f4d
python -m compileall -q src tests
```

Resultado final: **69 passed in 5.06s**; compilación completa sin errores.

## Pruebas Athena original

Proveedor AI_Broker y ensamblado desktop:

```powershell
.venv\Scripts\python.exe -m pytest -q tests\test_ai_broker_adapter.py tests\test_desktop.py
```

Resultado: **24 passed in 0.59s**.

Núcleo, AgentLoop, servicio, orquestación y verificación:

```powershell
.venv\Scripts\python.exe -m pytest -q tests\test_agent_loop.py tests\test_service_adapter.py tests\test_service_orchestration.py tests\test_ai_broker_adapter.py tests\test_verification.py tests\test_verification_inconclusive.py
```

Resultado: **123 passed in 65.27s**.

El repositorio Athena quedó limpio (`git status --short` sin salida); `AgentLoop`, ADR-002 y el proveedor AI_Broker no fueron modificados.

## Cobertura de aceptación F4

1. Athena sin Agora: 123 pruebas originales focalizadas pasan.
2. ModelProvider AI_Broker actual: sus 24 pruebas pasan.
3. Activar adaptador no modifica AgentLoop: integración vive sólo en Agora; Athena está limpio.
4. Athena crea CARD correctamente mediante `submit` idempotente.
5. `origin == athena`, derivado por autenticación; el contrato rechaza campos extra.
6. `submit` devuelve de inmediato; Athena puede terminar tras delegar.
7. La CARD persiste en BOARD.
8. `status` y `artifacts` recuperan después resultado y bytes.
9. Harness Athena sólo reclama después de health + auth check.
10. Runner ausente: `pending`, `attempts=0`.
11. CARD que pide `exec=allow` no amplía PROFILE `exec=off`.
12. `ask` se rechaza antes de iniciar Athena.
13. Verificación `passed` + archivo real termina en `done`.
14. Verificación inconclusa vuelve según failure policy; no hay falso `done`.

## Verificado realmente

- Las dos direcciones del adaptador con transportes reales de aplicación en proceso.
- Persistencia CARD, derivación de origin, cancelación, descarga binaria y reapertura de estado.
- Ejecución del harness con política PROFILE, respuesta Athena, evidencia y cierre BOARD.
- Suite original de Athena sin cambios en su árbol Git.

## No verificado / riesgo ambiental

Una ejecución amplia de toda la suite Athena alcanzó 512 pruebas aprobadas y falló en `test_cancellation_leaves_no_orphan_grandchild`. El proceso nieto de Windows siguió escribiendo porque la cuenta `CodexSandboxOffline` no puede administrar ese árbol (`Get-CimInstance` devuelve “Acceso denegado”). Es una prueba de procesos de Athena ajena a esta integración; no se cambió su código. La ejecución amplia posterior llegó al 85% con ese mismo único fallo antes de detenerse por el proceso huérfano. Las suites relevantes de F4 pasan completas.

No se abrió Athena a la LAN y no se eliminó su acceso directo actual a AI_Broker.
