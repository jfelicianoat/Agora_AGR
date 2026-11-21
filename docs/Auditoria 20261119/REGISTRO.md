# Registro de la auditoría integral de Agora — 19-nov-2026

Versión auditada: **0.2.17** (commit `5df8f39`, árbol limpio). Versión entregada: **0.2.18**
(sin commitear: los commits los hace el usuario).
Pruebas de regresión: `tests/test_auditoria_20261119.py`. Evidencias: `evidencias/`.

Estados: IDENTIFICADO, EN ANÁLISIS, PENDIENTE, EN CURSO, IMPLEMENTADO SIN VERIFICAR,
VERIFICADO, BLOQUEADO.

## 1. Situación inicial

| Control | Resultado sobre 0.2.17 |
|---|---|
| `py -3.14 -m pytest -q` | 643 verdes |
| `ruff check .` | limpio |
| `mypy src tests` (strict) | 93 errores, todos previos (sobre todo en `tests/test_recovery.py`) |
| Tablero instalado en `C:\Agora` | 0.2.14 (no se ha tocado) |
| Broker del PC IA (192.168.1.52:8765) | apagado durante toda la auditoría |

## 2. Registro por ID

Categorías: **DEF** defecto · **LOG** mejora de lógica · **USA** mejora de usabilidad ·
**TEC** tarea técnica necesaria. Certeza: **R** reproducido · **L** deducido de la lectura.

| ID | Cat. | Descripción y origen | Evidencia (antes) | Impacto / prioridad | Criterio de aceptación | Componentes | Estado | Implementación | Verificación |
|---|---|---|---|---|---|---|---|---|---|
| D-01 | DEF | «Ejecutar ronda» sin «Simular» (ventana) y `agora dispatch` reclaman tarjetas de IA de un cliente y las cierran `completed` con «Agora deterministic F0 artifact» en vez del contrato (`task-brief@1`…). El arnés determinista no tenía nombre y el despachador entiende «sin nombre» como «vale para todo»; tampoco respetaba `harness: broker`. Lectura de `harnesses.py`/`dispatcher.py`. | R: `scratchpad/scripts/repro1.py` sobre copia del tablero real → `h2.md` `done`/`completed`, `model: deterministic-f0`. | Alta: resultado falso entregado al cliente como bueno, trabajo de IA perdido. | La ronda real no toca perfiles con contrato de salida ni `harness` distinto de `fake`; el runner de IA los sigue viendo en `/work`; los perfiles de demo siguen ejecutándose. | `harnesses.py`, `api/service.py` | VERIFICADO | `DeterministicHarness.harness_name="fake"` + `is_available` (rechaza skills con `output_contract`); el API simula con `_RemoteRunners`, que no ejecuta. | T-01…T-03; E2E F-A; ventana real «Ronda: 1 para el runner de IA…» |
| D-02 | DEF | Desbloquear una tarjeta `attempts_exhausted` la deja en `pending` con 3/3: el despachador la salta («attempt limit reached») y el cliente la ve `queued` para siempre (el mismo patrón que AG-04). | R: `repro1.py`: `unblock 200` → `queued`, `/work` no la ofrece. | Alta: callejón sin salida; la única vía de recuperación no recupera. | Tras desbloquear: `queued`, `max_attempts` ampliado, anotado en el Record, ofrecida al runner. Una bloqueada por origen conserva su presupuesto. | `board.py` | VERIFICADO | `Board.unblock` amplía `max_attempts = attempts + max_attempts`. No se reinician intentos: el intento entra en la clave del broker y repetirlo devolvería la tarea fallida (`broker_idempotency_key`). | T-04, T-05; E2E F-B (tarjeta real `job-c1124fe05083.md`) |
| D-03 | DEF | Tras un `yield` sin gastar intento (Core deniega, contrato no soportado), el mismo runner reclama con la misma clave `{intento}:claim` y el API responde `replayed: true` «in-progress» **sin reclamar**; el runner envía al broker y falla al anotar, en bucle. | R: `scratchpad/scripts/repro_h4.py`: `claim2 … replayed: True`, estado real `pending`, `progress` 409. | Alta: bucle con gasto en el broker y tarjeta atascada. | El segundo claim reclama de verdad; las tarjetas que nunca volvieron conservan su clave antigua (compatibilidad con runners en vuelo). | `board.py`, `remote/runner.py` | VERIFICADO | `releases` cuenta cada vuelta a `pending` (`return_pending`, `unblock`); `_attempt_key` lo añade si es > 0. La clave del broker no cambia. | T-06, T-07; E2E F-C con `AgoraApiClient` real |
| D-04 | DEF | Las plantillas de `KANBAN/scheduled/` no se materializan nunca: `Scheduler.materialize` no tiene llamantes. La norma (`atomic-agents-standard.md`, paso 0) y F0 lo prometen; la ventana muestra «Programadas». Además una plantilla ilegible habría tumbado todo y la hora se evaluaba en UTC. | R: `repro_h5.py`: plantilla diaria, dos latidos, 0 pendientes. | Media: función documentada y visible que no hace nada. | Cada latido del API y cada ronda real crean la instancia del periodo una vez; la simulación no escribe; una plantilla rota se informa y no para a las demás; hora local del PC del tablero. | `dispatcher.py`, `scheduler.py`, `api/service.py`, `application.py` | VERIFICADO | `Dispatcher.materialize_scheduled` (paso cero de `run_once`, y llamado por `work()`); `ScheduledMaterialization.error`; `DispatchStatus.MATERIALIZED`. | T-08…T-10; E2E F-E |
| D-05 | DEF | Lo que mueve el latido del API (rescate de abandonadas → `pending` o `failed`) no emite eventos: quien sigue `/events` (A09) ve `running` para siempre una tarjeta fallada. | R: `repro_h5.py`: `blocked failed`, `eventos nuevos: []`. | Media: el contrato de sondeo promete estado al día. | Rescate → `card.recovered` con su proyección; programada → `card.created` (`principal: scheduler`). | `api/app.py`, `api/service.py`, A09 | VERIFICADO | `on_heartbeat` publica los desenlaces con `final_path`; `GET /work` bajo `operation_lock`. Límite documentado: lo que hace la ventana (otro proceso) se ve por `GET /cards/{f}`, no por el cursor. | T-11, T-12; E2E F-D |
| D-06 | DEF | Una excepción no prevista en `run_once` sale del bucle de `agora-ai-runner` y el runner del PC IA deja de trabajar hasta reiniciarlo a mano. | L: `broker/main.py` sólo captura `KeyboardInterrupt`; `run_once` traduce excepciones concretas. No se forzó un caso real. | Media | Una vuelta que lanza devuelve `failed` con el tipo y el bucle sigue. | `broker/main.py` | VERIFICADO (unitario) | `_guarded_run`. | T-13 |
| D-07 | DEF | README con caracteres de control (`\x08`, `\x07`): las órdenes de instalación salían como `scriptsuild-…` y `distgora-…`. | R: búsqueda de bytes < 32. | Baja | Sin caracteres de control; rutas correctas. | `README.md` | VERIFICADO | Bytes corregidos. | T-14 |
| U-01 | USA | La ventana no permite recuperar nada: desbloquear o cancelar exige `POST /admin/...` con token de administración. | L + ventana real. | Alta para el operador: bloqueadas = callejón. | Botones «Desbloquear…» y «Cancelar…» con motivo obligatorio (y confirmación al cancelar), activos sólo donde aplican; el resultado se refleja en el tablero y en el API. | `application.py`, `desktop/window.py` | VERIFICADO | `AgoraApplication.unblock/cancel`, botones con diálogos sustituibles. | T-15, T-17; ventana real 02/03/06; E2E F-F |
| U-02 | USA | No se puede ver el resultado de una tarjeta terminada desde la ventana (sólo una ruta dentro del JSON). | L + ventana real. | Media | «Abrir resultado» activo cuando hay fichero; lo abre con la aplicación de Windows. | `application.py`, `desktop/window.py` | VERIFICADO | `AgoraApplication.artifacts`; `QDesktopServices.openUrl`. | T-17; ventana real 05 (apertura interceptada por el arnés) |
| U-03 | USA | Tras «Ejecutar ronda» no hay respuesta visible: lo hecho sólo queda en Actividad; los controles no explican qué hacen. | Ventana real. | Media | Resumen de la ronda junto al indicador; tooltips en «Simular» y «Ejecutar ronda». | `desktop/window.py` | VERIFICADO | `_summary`, `round_summary`, tooltips. | T-17; ventana real 04 |
| U-04 | USA | Una pendiente que no avanza (sin perfil, para el runner de IA, esperando entradas) se ve igual que una en cola. | L + ventana real. | Media | Fila «Situación» con el motivo, calculado con la misma simulación del despachador. | `application.py`, `desktop/window.py` | VERIFICADO | `AgoraApplication.explain`, `_EXPLANATIONS`, `_SITUATIONS`. | T-16; ventana real 03 |
| U-05 | USA | (Descubierto al implementar U-01) los botones deshabilitados se veían igual que los activos. | Captura 03 de la primera pasada. | Baja | Estilo `:disabled` distinto. | `desktop/window.py` | VERIFICADO | `QPushButton:disabled`. | Ventana real 04 |
| T-V | TEC | Versión visible y entrega regenerada. | — | — | 0.2.18 en `pyproject`, título y barra de estado; wheel, zip y `.bat` del runner generados. | `pyproject.toml`, `__init__.py`, `window.py`, `dist/` | VERIFICADO | `dist/agora_atomic_work-0.2.18-py3-none-any.whl`, `dist/agora-runner-0.2.18.zip`, `dist/Actualizar runner Agora 0.2.18.bat`. | Salida de `build-runner-package.ps1`; captura 04 |
| B-01 | TEC | Recorrido con IA real (runner → broker → modelo) de la versión 0.2.18. | — | — | Una tarjeta real llega a `done` con artefacto de contrato. | — | VERIFICADO | Copia del tablero + runner 0.2.18 real + broker real del PC IA (token de AI OS Core, reenviador loopback). | `evidencias/ia_real.txt`: `task-brief@1` válido, `ollama/local/qwen3.8:27b`, coste 0; el runner **retomó** la misma tarea del broker tras un corte (recuperación real). |
| B-02 | TEC | Desplegar 0.2.18: tablero de este PC y runner del PC IA. | — | — | Tablero en 0.2.18 sin pérdida de datos. | — | VERIFICADO (tablero) | Autorizado por el usuario. Copia previa en `C:\Agora\respaldo-workspace-0.2.14-20261119`; `install-board.ps1` con el wheel 0.2.18; mismo `board_id` y token. El runner del PC IA lo actualiza el usuario allí (`dist\Actualizar runner Agora 0.2.18.bat`): desde este PC no hay acceso a esa máquina. | API real: salud, 5 perfiles, 53 tarjetas, 0 errores; ventana instalada `evidencias/ventana/07-instalada-produccion.png`. Tablero dejado parado, como estaba. |

### Objeciones del arbitraje, ronda 1 (incorporadas al registro)

| ID | Cat. | Gravedad | Objeción | Estado | Corrección | Verificación |
|---|---|---|---|---|---|---|
| A-1 | DEF | Alta | La instancia programada se volvía a crear en cuanto salía de `pending` (se pagaba dos veces y el duplicado se atascaba en `in-progress`). D-04 lo dejaba abierto: su E2E nunca reclamaba la instancia. | VERIFICADO | `scheduler.py` comprueba el nombre en todas las carpetas. | `test_a1_…`; E2E F-E «no se recrea al reclamarla»; script del árbitro `a1_api.py`/`a1_dup.py` ya sin duplicado |
| A-2 | DEF (regresión de D-03) | Media-alta | Con D-03, un runner que suelta gratis cada vuelta (contrato no soportado) reescribía tarjeta, idempotencia y eventos cada 15 s sin fin. | VERIFICADO | `free_releases`; a las 5 vueltas gratis se bloquea con `repeatedly_released` (nuevo código, A11); desbloquear lo pone a cero. | `test_a2_…`; E2E F-G |
| A-3 | DEF (seguridad, de U-02) | Media | «Abrir resultado» abría cualquier ruta de `paths`, incluido un `.exe` (lo ejecutaría). | VERIFICADO | Sólo ficheros dentro del workspace; extensiones que no son de documento abren su carpeta; el botón usa la misma regla. | `test_a3_…`; ventana real: con rutas fuera de la copia, botón desactivado |
| A-4 | DEF | Baja-media (preexistente) | Mutaciones del tablero desde dos procesos (ventana y API) podían dejar una tarjeta en dos carpetas. | VERIFICADO | Candado entre procesos en todas las mutaciones de `Board` (versión final en B-2: bloqueo del sistema sobre `KANBAN/.board.lock`, sin caducidad, espera máx. 10 s). | `test_a4_…` (proceso hijo con el candado); `evidencias/carrera_procesos.txt`: 4 procesos × 40 tarjetas → 0 duplicadas (0.2.17: 29) |
| A-5 | DEF | Baja | Cada desbloqueo duplicaba el presupuesto (3→6→12). | VERIFICADO | `attempt_budget` guarda el original. | `test_a5_…` |
| A-6 | DEF | Baja | Materializar a la vez ventana y API informaba ERROR. D-05 no llevaba la salvedad del límite. | VERIFICADO | Choque → «already materialized». Límite de D-05 anotado: lo que hace la ventana no pasa por el cursor de eventos (A09). | Lectura + `test_a1_…` |
| A-7 | DEF | Baja | Errores de plantillas en el latido del API se perdían. | VERIFICADO | `logging` `agora.api` (warning). | `test_a7_…` (caplog) |
| A-8 | DEF | Baja | La instancia heredaba `remote`, `external_reference`, `flow_id`… de la plantilla. | VERIFICADO | Se quitan los campos de ejecución. | `test_a8_…` |
| A-9 | DOC | Baja | README describía mal qué ejecuta la ronda local. | VERIFICADO | Texto corregido. | Lectura |
| A-10 | TEC | — | Evidencia insuficiente en D-03/D-04 y B-02. | VERIFICADO | E2E ampliado (16 comprobaciones) y nota en B-02. | `evidencias/e2e_api.txt` |
| T-GC | TEC | — | Con las pruebas nuevas, la suite completa abortaba **al salir** (todas PASSED, luego «Fatal Python error: Aborted»: Qt recogido por el GC en un hilo de `anyio`). 0.2.17 sin el fichero nuevo no abortaba. | VERIFICADO | `conftest.py`: `gc.collect()` por módulo en el hilo principal; los `TestClient` nuevos se cierran. | 5 pasadas completas seguidas sin aborto |

### Objeciones del arbitraje, ronda 2

| ID | Cat. | Gravedad | Objeción | Estado | Corrección | Verificación |
|---|---|---|---|---|---|---|
| B-1 | DEF | Media | El API modificaba metadatos (`claim`, `progress` con checkpoint, `close` con auditoría) fuera del candado: cancelar desde la ventana durante un `close` dejaba la tarjeta en `done` y `archive`. | VERIFICADO | `RemoteWorkService.claim/progress/close` hacen toda la secuencia leer-modificar-guardar dentro de `Board._mutation_lock()`. | Script del árbitro `b_close_cancel.py`: una sola carpeta; `test_b1_…` |
| B-2 | DEF | Baja-media | El candado era un fichero que se creaba y borraba: si un tercero (indexador, antivirus) lo tenía abierto, el `unlink` fallaba, se informaba error de una operación ya hecha y quedaba huérfano; además la caducidad de 30 s podía dejar entrar a dos. | VERIFICADO | Bloqueo de rango del sistema (`msvcrt.locking` / `fcntl.flock`) sobre un fichero permanente `KANBAN/.board.lock`: el sistema lo suelta si el proceso muere; no hay caducidad. Reentrante por tablero y por hilo. | `b_lockhandle.py`: 40/40 sin errores; `b_contention.py`: 6×150 = 900; `test_b2_…` (proceso con el candado muerto con `taskkill /F`); árbitro ronda 3: `c_threads.py` (hilos del API) y `c_close_cancel_proc.py` (entre procesos). Rama `fcntl` verificada en Linux (Docker `python:3.14`): 51 pruebas y carrera sin duplicados, `evidencias/linux_fcntl.txt`. |
| B-3 | DEF | Baja | «Tablero ocupado» salía como 409 de conflicto. | VERIFICADO (Agora y GTI 1.50.2) | Excepción `BoardBusy` → **503** con `Retry-After: 2`. GTI convierte hoy cualquier error HTTP en `AgoraRejected` (`agora_client.py:349-353`) y al crear marca el encargo como fallido: tratar 503 como «no disponible» es un cambio en GTI. | `test_b3_…` (503 y reintento 201) |
| B-4 | DEF | Baja | El tope de vueltas gratis contaba en total, no «seguidas» como dice A11. | VERIFICADO | Un intento real reinicia `free_releases`. | `test_b4_…` |
| B-5 | DOC/TEC | Muy baja | Evidencia guardada en cp1252; GTI no conoce el código `repeatedly_released` (lo muestra como `unknown` con el mensaje correcto, ramifica por `status`). | VERIFICADO (evidencia y GTI 1.50.2) | Evidencias regrabadas en UTF-8. Añadir el código a `REMOTE_ERROR_CODES` de GTI es un cambio en otro repositorio: anotado. | `evidencias/*.txt` |

### Observaciones sin calidad de incidencia (no cambiadas, con motivo)

- `paths` de las tarjetas son rutas absolutas: mover el workspace de carpeta deja los
  resultados sin abrir (y el API los rechaza por estar fuera del workspace). Es el contrato
  de F0 desde el principio; cambiarlo afecta a clientes. No se ha tocado.
- Los motivos de la ronda («compatible runner is unavailable») siguen en inglés entre
  paréntesis: es la decisión de idioma por capa de AG-06 (motivos = contrato del API).

## 3. Pruebas

| ID | Objetivo | Comando / datos | Esperado | Observado | Estado |
|---|---|---|---|---|---|
| T-01…T-17 (+10 de A-*/B-*) | Regresiones D-01…D-07, U-01…U-04, arbitraje | `py -3.14 -m pytest tests/test_auditoria_20261119.py` (perfiles reales de `profiles/task-planning`) | 28 verdes; contra 0.2.17 fallan las de D/U (14 de las 17 primeras; 3 son guardas) | 28 verdes; contra HEAD (worktree) 14 de las 17 primeras fallan, 3 pasan | PASA |
| T-S | Suite completa | `py -3.14 -m pytest -q` | Todo verde | 671 verdes, sin aborto al salir | PASA |
| T-R | ruff | `ruff check .` | limpio | limpio | PASA |
| T-M | mypy strict | `mypy src tests` | sin errores nuevos | 93 (los mismos 93 previos) | PASA |
| E-API | Recorridos reales por HTTPS | uvicorn `agora.api.main` en 8751 con `C:\Agora\pki`, copia del workspace real; `scratchpad/scripts/e2e_api.py` (otro proceso, `AgoraApiClient` real) | 16 comprobaciones F-A…F-G | ver `evidencias/e2e_api.txt` | PASA |
| E-GUI | Ventana real (plataforma `windows`) | `scratchpad/scripts/gui_e2e.py` sobre otra copia | desbloquear, ronda, abrir, cancelar | `evidencias/ventana/registro.txt` + capturas 01–06 | PASA |
| E-IA | Runner con broker real | — | — | PC IA apagado | BLOQUEADA |

Componentes reales frente a simulados: API, tablero, ventana, cliente HTTP del runner y
perfiles son reales; en E-GUI se sustituyen sólo los diálogos modales y la apertura del
fichero; el broker y AI OS Core **no** participaron.

## 4. Arbitraje

Revisor independiente (agente separado, sin permiso de escritura en el repo), tres rondas
formales; detalle en [`ARBITRAJE.md`](ARBITRAJE.md).

| Ronda | Veredicto | Objeciones | Resultado |
|---|---|---|---|
| 1 | No aprueba | A-1…A-10 (1 alta, 1 media-alta, 1 media) | Todas corregidas |
| 2 | No aprueba | B-1 (media) … B-5 | Corregidas; B-3/B-5 con pendiente en GTI |
| 3 | **Aprueba el código** | C-1…C-4 (texto del registro) | Corregidas en este documento |

## 5. Estado final

**COMPLETADO Y VERIFICADO** para el alcance de la auditoría: todos los defectos y mejoras
(D-01…D-07, U-01…U-05, A-1…A-10, B-1…B-4, T-V, T-GC) están implementados y verificados con
pruebas (671), E2E por HTTPS real, la ventana real, IA real (B-01), la rama Linux del
candado (Docker) y el tablero instalado en producción (B-02). El arbitraje aprobó el código
en la ronda 3.

Pasos operativos que quedan en manos del usuario (no son verificaciones pendientes del
código):

- Actualizar el runner del PC IA con `dist\Actualizar runner Agora 0.2.18.bat` (el arreglo
  D-03 actúa cuando ese runner es 0.2.18).
- Gestión Tareas IA ya está adaptada (1.50.2, a petición del usuario): 503 → «no disponible» y
  códigos `repeatedly_released`, `no_matching_profile`, `ambiguous_profile`; probado cliente real
  contra API real (`evidencias/gti_1.50.2_vs_agora_0.2.18.txt`).
