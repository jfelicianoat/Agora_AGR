# RESULTADO DE FASE F3 — AI Runner + AI_Broker

## Implementado

- Cliente de AI_Broker 2.9 restringido a loopback.
- Supervisor del proceso AI_Broker con credencial aleatoria por arranque, inyectada sólo por entorno del proceso hijo.
- Captura de salida con redacción íntegra de la credencial.
- Renovación local automática ante 401/403 y reinicio del proceso supervisado.
- Traducción `PROFILE -> SKILLS declaradas -> CARD -> inputs explícitos`.
- Attachments con upload y espera obligatoria a estado `ready` antes del claim de Agora.
- Ejecución asíncrona, polling, checkpoint recuperable, cancelación de zombies e idempotencia estable por intento.
- Políticas `strict` y `routed`, con `prompt_compression=off`.
- Auditoría persistente de modelo real, invocations, coste, parámetros efectivos y fingerprint.
- Ejecutable headless `agora-ai-runner`; fuerza AI_Broker a `127.0.0.1`.

## Ciclo real de X-Admin-Token investigado

En el AI_Broker existente, `scripts/run_broker.py`:

- acepta `--host` (línea 16);
- consulta la variable configurada mediante `os.environ.get` (línea 35);
- si falta, genera `secrets.token_urlsafe(24)` (línea 38);
- lo publica en el entorno del propio proceso (línea 39);
- imprime el token completo (línea 48).

`app/admin_auth.py` resuelve primero el valor del entorno (línea 73), tiene fallback a keyring (línea 82) y compara credenciales en tiempo constante (líneas 110 y 133).

La solución aplicada no modifica AI_Broker: Agora genera el secreto en memoria, lo inyecta al hijo, obliga al broker a escuchar sólo en loopback y redacta la salida donde el launcher actual lo imprime. El cliente que contiene `X-Admin-Token` rechaza por construcción cualquier URL que no sea loopback. La API remota de Agora usa una credencial distinta y no posee ningún campo para el token del broker.

## Archivos principales

- `src/agora/broker/client.py`
- `src/agora/broker/contracts.py`
- `src/agora/broker/request_builder.py`
- `src/agora/broker/supervisor.py`
- `src/agora/broker/executor.py`
- `src/agora/broker/runner.py`
- `src/agora/broker/main.py`
- `src/agora/api/service.py`
- `src/agora/remote/client.py`
- `tests/test_broker.py`
- `tests/test_ai_runner.py`

## Tests y resultado

Comando ejecutado en Windows con el Python 3.14 disponible y temporal aislado dentro del workspace:

```powershell
$env:TEMP='D:\Desarrollo\Proyectos TFM\Agora\tmp'
$env:TMP=$env:TEMP
python -m pytest -q --basetemp 'D:\Desarrollo\Proyectos TFM\Agora\tmp\pytest-f3e'
python -m compileall -q src tests
```

Resultado: **63 passed in 5.00s**; compilación completa sin errores.

Cobertura de las 13 pruebas obligatorias:

1. Broker/PC IA offline: CARD queda `pending`, `attempts=0`.
2. Broker disponible: runner comprueba salud/capacidades, reclama y completa.
3. Reinicio/token: el token anterior recibe 403, se renueva localmente y la ejecución continúa.
4. Logs: supervisor sustituye cualquier token completo por `[REDACTED]`.
5. Red: el cliente administrativo sólo admite loopback; el cliente Agora no contiene ni serializa el secreto.
6. Attachment `converting`: espera sin claim y sin intento consumido.
7. Strict: identidad del modelo y parámetros efectivos (`temperature`, `seed`, `top_p` y estados `sent`) se verifican; un mismatch vuelve a `pending` con fallo visible.
8. Routed: modelo servido real queda registrado.
9. Timeout legítimo: no se cancela antes del umbral zombie.
10. Zombie real: se cancela en broker antes de redispatch.
11. Retry interno del broker: no incrementa `CARD.attempts`.
12. Idempotency-Key: dos envíos del mismo intento conservan una única clave facturable.
13. E2E: CARD -> broker -> artifact -> done, con path, Record, coste, modelo, invocations y fingerprint.

## Verificado realmente

- Suite completa de Agora con transporte HTTP simulado fiel al contrato 2.9.
- Arranque de un proceso hijo real para comprobar rotación y redacción del secreto.
- Rechazo estructural de direcciones no-loopback para `X-Admin-Token`.
- Transferencia de inputs mediante la API de Agora, validación SHA-256 y conversión a `broker_file`.

## No verificado

- No se ejecutó una inferencia facturable contra el AI_Broker del PC IA: el sandbox rechazó la conexión LAN con `WinError 10013`.
- No se utilizó ni se registró la credencial real facilitada por el usuario.

## Deuda/riesgos

- El launcher original de AI_Broker imprime el secreto; el supervisor lo neutraliza en su salida capturada, pero una ejecución manual de AI_Broker fuera del supervisor mantiene el comportamiento original.
- La instalación como servicio permanente y las políticas de encendido pertenecen a F8.
