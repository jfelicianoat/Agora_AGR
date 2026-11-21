# Auditoría integral de Agora — informe de entrega (19-nov-2026)

Versión auditada **0.2.17** (commit `5df8f39`) → versión entregada **0.2.18**, sin commitear.
Registro completo por ID, criterios y evidencias: [`REGISTRO.md`](REGISTRO.md).

## 1. Resumen

Agora es el tablero de tarjetas que reparte trabajo entre los clientes (Gestión Tareas IA y
otros), el runner de IA del PC IA y una ventana de escritorio. La auditoría encontró que
**tres recorridos principales daban resultados incorrectos**, y que varias funciones
documentadas o necesarias no hacían nada:

- **D-01 (alta).** «Ejecutar ronda» de la ventana cerraba tarjetas de IA de un cliente como
  `completed` con un texto de relleno en lugar del documento del contrato.
- **D-02 (alta).** Desbloquear una tarjeta que agotó sus intentos la dejaba «en cola» para
  siempre.
- **D-03 (alta).** Tras soltar una tarjeta sin gastar intento, el runner creía reclamarla
  (el API le devolvía la respuesta guardada) y la mandaba al broker en bucle.
- **D-04.** Las tarjetas programadas no se creaban nunca. **D-05.** Los rescates no generaban
  eventos. **D-06.** Un error inesperado paraba el runner. **D-07.** README con rutas rotas.
- **Usabilidad.** La ventana no permitía recuperar nada: no se podía desbloquear, cancelar
  ni abrir un resultado, y no explicaba por qué una tarjeta no avanzaba (U-01…U-05).

El arbitraje independiente hizo dos rondas formales con objeciones y una tercera de cierre
(§8). Encontró, entre otras cosas, que la primera corrección de D-04 duplicaba tareas
programadas (A-1), un bucle de escrituras que había introducido D-03 (A-2), que «Abrir
resultado» podía ejecutar un `.exe` (A-3), y una carrera entre procesos que **ya existía en
0.2.17**: una tarjeta podía quedar en dos carpetas (A-4/B-1/B-2). Todo eso está corregido.

## 2. Registro

Ver [`REGISTRO.md`](REGISTRO.md): D-01…D-07, U-01…U-05, A-1…A-10, B-1…B-5, T-V, T-GC, B-01/B-02.

## 3. Mapa de funcionalidades y flujos (0.2.18)

| Objetivo del usuario | Flujo | Reglas | Componentes | Pruebas |
|---|---|---|---|---|
| Un cliente encarga trabajo de IA | `POST /api/v1/cards` → `pending` → runner `GET /work` → `claim` → broker → `close` → `done` → `GET /cards/{f}` + artefacto | Empareja por `function` + handle; sólo un runner de IA ejecuta perfiles con contrato; la ventana no los toca (D-01) | `api/*`, `dispatcher.py`, `broker/runner.py`, `harnesses.py` | T-01…T-03, E2E F-A |
| Seguir el estado sin preguntar tarjeta a tarjeta | `GET /api/v1/events` con cursor | El latido publica rescates y programadas (D-05); lo que hace la ventana se ve por `GET /cards` | `api/app.py`, `api/storage.py` | T-11, T-12, E2E F-D/F-E |
| Recuperar una tarjeta parada | Ventana «Desbloquear…» o `POST /admin/blocked/{f}/unblock` | Agotada → `max_attempts = attempts + presupuesto original` (D-02, A-5); 5 vueltas gratis seguidas → `repeatedly_released` (A-2, B-4) | `board.py`, `application.py`, `window.py` | T-04, T-05, A-2, A-5, B-4, E2E F-B/F-G, ventana 02/03 |
| Abandonar un encargo | Ventana «Cancelar…» (confirmación + motivo) o `POST /cards/{f}/cancel` | `pending`/`in-progress` → `archive`; el cliente ve `cancelled` | `board.py`, `application.py`, `window.py` | U-01, E2E F-F, ventana 06 |
| Ver el resultado | Ventana «Abrir resultado» | Sólo ficheros del workspace; lo que no es documento abre su carpeta (A-3) | `application.py`, `window.py` | A-3, ventana 05 |
| Entender por qué algo no avanza | Ventana, fila «Situación» | La misma simulación del despachador | `application.explain` | U-04, ventana 03 |
| Trabajo periódico | Plantilla en `KANBAN/scheduled/` con `trigger` | Una instancia por periodo, en cualquier carpeta (A-1); hora local; plantilla rota no para el latido | `scheduler.py`, `dispatcher.py` | T-08…T-10, A-1, A-8, E2E F-E |
| Runner robusto | `agora-ai-runner` en bucle | Clave de intento con `releases` (D-03); una excepción inesperada no lo para (D-06) | `remote/runner.py`, `broker/main.py` | T-06, T-07, T-13, E2E F-C |
| Integridad entre procesos | Ventana y API sobre las mismas carpetas | Candado del sistema en `KANBAN/.board.lock`; ocupado → 503 | `board.py`, `api/service.py`, `api/app.py` | A-4, B-1…B-3, `evidencias/carrera_procesos.txt` |

## 4. Cambios relevantes

| Fichero | Cambio | Motivo |
|---|---|---|
| `src/agora/harnesses.py` | `harness_name="fake"`, `is_available` | D-01 |
| `src/agora/api/service.py` | `_RemoteRunners`; latido con `on_heartbeat`; `claim/progress/close` bajo candado | D-01, D-05, B-1 |
| `src/agora/api/app.py` | Eventos del latido, `work` bajo `operation_lock`, `BoardBusy`→503, log | D-05, B-3, A-7 |
| `src/agora/api/projection.py` | Código `repeatedly_released` | A-2 |
| `src/agora/board.py` | Presupuesto al desbloquear, `releases`, `free_releases`, candado del sistema | D-02, D-03, A-2, A-4, A-5, B-2, B-4 |
| `src/agora/remote/runner.py` | `_attempt_key` con `releases` | D-03 |
| `src/agora/dispatcher.py`, `scheduler.py` | Paso cero `materialize_scheduled`; idempotencia en todas las carpetas; campos no heredados | D-04, A-1, A-6, A-8 |
| `src/agora/broker/main.py` | `_guarded_run` | D-06 |
| `src/agora/errors.py` | `BoardBusy` | B-3 |
| `src/agora/application.py` | `unblock`, `cancel`, `artifacts`, `open_target`, `explain` | U-01…U-04, A-3 |
| `src/agora/desktop/window.py` | Botones, «Situación», resumen de ronda, tooltips, estilo deshabilitado, versión en la barra | U-01…U-05 |
| `tests/test_auditoria_20261119.py` | 28 pruebas de regresión | — |
| `tests/conftest.py` | `gc.collect()` por módulo | T-GC |
| `README.md`, `docs/A09`, `A10`, `A11` | Uso de la ventana, eventos del latido, desbloqueo, códigos | Documentación |
| `pyproject.toml`, `src/agora/__init__.py` | 0.2.18 | T-V |

**Compatibilidad:** sin migraciones. Los campos nuevos de la tarjeta (`releases`,
`free_releases`, `attempt_budget`) son opcionales; una tarjeta sin `releases` conserva la
misma clave de idempotencia de antes, así que un runner que esté trabajando durante la
actualización sigue igual. El contrato del API sólo añade cosas: el evento `card.recovered`,
el código `repeatedly_released` y la respuesta 503. Gestión Tareas IA ramifica por `status`,
así que no le afecta.

## 5. Pruebas ejecutadas

| Control | Resultado final |
|---|---|
| `py -3.14 -m pytest -q` | **671 verdes** (643 previas + 28 nuevas), tres pasadas seguidas sin aborto |
| Pruebas nuevas contra 0.2.17 | Las de D-01…D-07 y U-* fallan; las guardas de regresión pasan |
| `ruff check .` | limpio |
| `mypy src tests` (strict) | 93 errores, **los mismos 93 de antes**; ninguno nuevo |
| E2E real por HTTPS (uvicorn + certificados de `C:\Agora\pki`, copia del workspace real, cliente real del runner) | 16/16 — `evidencias/e2e_api.txt` |
| Ventana real (plataforma `windows`) sobre otra copia | Desbloquear, ronda, abrir y cancelar — `evidencias/ventana/` |
| Carrera entre 4 procesos sobre 40 tarjetas | 0 duplicadas (0.2.17: 29) — `evidencias/carrera_procesos.txt` |
| IA real (runner 0.2.18 → broker del PC IA → modelo) | `done` con `task-brief@1` válido (`ollama/local/qwen3.8:27b`, coste 0); retomó la misma tarea tras un corte — `evidencias/ia_real.txt` |
| Tablero instalado (`C:\Agora`, 0.2.18) | API real y ventana sobre los datos reales: 53 tarjetas, 0 errores — `evidencias/ventana/07-instalada-produccion.png` |
| Candado en Linux (`fcntl`, Docker `python:3.14`) | 51 pruebas verdes y carrera sin duplicados — `evidencias/linux_fcntl.txt` |

**Qué es real y qué no.** Son reales el API, el tablero, la ventana, el cliente HTTP del
runner y los perfiles. En las pruebas de la ventana sólo se sustituyen los diálogos modales
y la llamada que abre el fichero. **No participaron el broker ni AI OS Core.**

## 6. Cómo ejecutar y comprobar 0.2.18

```powershell
cd "D:\Desarrollo\Proyectos TFM\Agora"
py -3.14 -m pytest -q                                  # suite
py -3.14 -m pytest -q tests/test_auditoria_20261119.py # regresiones de esta auditoría
# ruff/mypy: el .venv del repo está roto; usar un venv aparte con
#   pip install -e ".[dev,api,oauth,runner,desktop]"
```

Para comprobarlo como usuario: abrir «Agora Desktop» y seleccionar una tarjeta bloqueada.
Al pulsar «Desbloquear…», la tarjeta pasa a Pendientes y la fila «Situación» dice quién la
recogerá. «Ejecutar ronda» no toca las tarjetas de IA y muestra un resumen. En una tarjeta
terminada, «Abrir resultado» abre el documento.

## 7. Actualización

- **Tablero de este PC (`C:\Agora`, hoy 0.2.14):** `.\scripts\install-board.ps1` con el wheel
  `dist\agora_atomic_work-0.2.18-py3-none-any.whl`. Después hay que reiniciar «Agora Tablero».
- **Runner del PC IA:** copiar `dist\Actualizar runner Agora 0.2.18.bat` y hacer doble clic.
  D-03 sólo surte efecto cuando el runner está en 0.2.18.
- No hay migraciones de datos. La primera mutación crea `KANBAN\.board.lock` (fichero vacío,
  permanente) y no hay que borrarlo.

## 8. Arbitraje

- **Cómo se hizo:** con un agente revisor independiente, sin permiso para modificar el repo.
  Recibió los requisitos, el diff, los scripts y las evidencias, y verificó ejecutando.
- **Ronda 1:** no aprobó. Planteó 10 objeciones (A-1…A-10); se aceptaron y corrigieron todas.
- **Ronda 2:** no aprobó. Una objeción media (B-1) y cuatro bajas (B-2…B-5), también corregidas.
- **Ronda 3:** aprueba el código; las 4 correcciones del registro (C-1…C-4) aplicadas. Después se cerraron B-01 (IA real) y B-02 (tablero instalado): estado final **COMPLETADO Y VERIFICADO**.

## 9. Estado final y pendientes

**COMPLETADO Y VERIFICADO** (ver `REGISTRO.md` §5). El tablero de este PC ya está en 0.2.18;
copia previa en `C:\Agora\respaldo-workspace-0.2.14-20261119`.

Pasos que quedan en tus manos:
- Actualizar el runner del PC IA: copiar `dist\Actualizar runner Agora 0.2.18.bat` allí y
  hacer doble clic.
- Gestión Tareas IA ya está adaptada en 1.50.2 (503 → «no disponible», códigos nuevos;
  ejecutable regenerado y validado; `evidencias/gti_1.50.2_vs_agora_0.2.18.txt`).
