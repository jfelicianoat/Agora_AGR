# A10 — Recuperación robusta de trabajos de IA

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A10_recuperacion_robusta_de_trabajos_ia.md`.

## 1. Resumen

Lo caro cuando algo se cae no es el fallo: es que al recuperarse **el trabajo se
ejecute dos veces**. Una tarea de IA cuesta minutos y dinero, y un artefacto
duplicado o un estado reescrito llegan al cliente como si fueran buenos.

La maquinaria de recuperación **ya existía y estaba bien pensada**: el runner
reclama su propia tarjeta antes de buscar trabajo nuevo, guarda el `task_id` del
broker en la CARD, reanuda esa tarea en vez de mandar otra, y cancela los
zombies. Lo que faltaba era demostrarlo, y una cosa que estaba implícita:

- **Ninguna prueba cubría el reinicio del runner.** Existían el broker caído y
  los zombies, pero no el caso central de la fase: el proceso muere con trabajo
  en vuelo y vuelve.
- **La distinción entre estados recuperables y terminales no existía en el
  código.** Estaba repartida en listas escritas a mano, y el cliente tenía que
  deducirla.

## 2. Archivos

Creados:

- `tests/test_recovery.py` — 19 pruebas.
- `scripts/verify_a10_recovery.py` — mata el tablero de verdad y mira qué pasa.

Modificados:

- `src/agora/board.py` — `BoardState.is_terminal`, `.is_in_flight`,
  `.needs_intervention`.
- `src/agora/api/app.py` — `terminal` en el estado de la tarjeta y en cada
  evento.
- `pyproject.toml`, `src/agora/__init__.py` — **0.2.5 → 0.2.6**.

## 3. Estados recuperables y terminales

Cada estado cae en **exactamente una** categoría, y hay una prueba que lo
comprueba recorriéndolos todos. Sin eso, la clasificación sería una opinión y no
una partición.

| Estado | Categoría | Qué significa |
|---|---|---|
| `pending`, `in-progress`, `scheduled` | **en vuelo** | Avanza solo, sin que nadie intervenga |
| `blocked` | **necesita intervención** | Se paró. No es lo mismo que haber terminado: alguien puede desbloquearla |
| `done`, `archive` | **terminal** | Acabó, y nada lo reabre |

Que `blocked` **no** sea terminal es la decisión que importa. Una tarjeta que
agotó sus intentos se ha parado, no ha terminado; tratarla como final cerraría
la puerta a `POST /admin/blocked/{filename}/unblock`, que existe justamente para
eso.

El cliente lo recibe hecho: `GET /api/v1/cards/{filename}` devuelve
`"terminal": true|false`, y **cada evento del cursor también**. Un cliente que
sondea ya no necesita llevar escrita la lista de estados finales — mientras sea
`false`, sigue mirando.

## 4. Por qué no se ejecuta dos veces

Tres mecanismos encadenados, todos ya en el código y ahora con pruebas:

1. **El runner mira lo suyo antes que lo nuevo.** `run_once` pregunta primero
   por `claimed(runner_id)`. Si buscara trabajo nuevo primero, dejaría su tarjeta
   huérfana y cogería otra.
2. **La CARD recuerda la tarea del broker.** `remote.task_id` está en el disco,
   no en memoria. Al recuperarse, el runner llama a `executor.resume(task_id)`
   en vez de volver a enviar.
3. **Las claves de idempotencia se derivan de `runner_id + filename +
   attempts`.** Un runner que reintenta el mismo intento manda exactamente las
   mismas claves, así que `claim`, `progress` y `close` se reproducen en vez de
   repetirse.

Y para lo que no se puede recuperar: si la tarea del broker lleva viva más que
el `zombie_timeout_seconds` del **perfil** —no un valor global—, se cancela en el
broker *antes* de devolver la tarjeta. Devolverla sin cancelar dejaría la tarea
corriendo y pagándose sin que nadie recoja el resultado.

## 5. Pruebas añadidas

`tests/test_recovery.py`, 19 pruebas. Casi todas terminan mirando lo mismo:
**cuántas veces se mandó trabajo al broker**.

- **Estados** (4): cada estado cae en una sola categoría; `done` y `archive` son
  terminales; `blocked` está parado pero no terminado; lo que avanza solo está
  en vuelo.
- **El runner se reinicia** (4): reanuda en vez de reenviar (la prueba central:
  `len(submissions) == 1` tras el reinicio, y el mismo `task_id`); coge su
  tarjeta antes que una nueva; con el broker caído no toca nada ni consume
  intento; **tres reinicios seguidos siguen siendo un solo envío**.
- **Idempotencia de `close` y `progress`** (4): cerrar dos veces con la misma
  clave no duplica el artefacto y marca `replayed`; la misma clave con otros
  artefactos es `409`; un hito repetido se escribe una vez; una clave distinta
  sí registra progreso nuevo —la idempotencia no puede silenciar lo legítimo—.
- **No resucitar lo terminado** (3): una tarjeta cerrada no se vuelve a ofrecer;
  un runner reiniciado no la resucita; cerrar una que nadie reclamó se rechaza.
- **Zombies** (2): se cancela en el broker antes de reintentar; el umbral sale
  de la política del perfil.
- **Lo que ve el cliente** (2): se le dice cuándo dejar de sondear; el evento
  también lo dice.

Suite completa: **416 pruebas, todas en verde** (eran 397 al cerrar A09).

## 6. Punta a punta: matar el tablero con trabajo en vuelo

Las pruebas unitarias cubren el runner reiniciado y el broker caído con un
broker falso. Lo que ahí no se puede probar es lo otro: **que se muera el
tablero mientras el runner trabaja**.

Así que se hizo de verdad. Se creó una tarjeta, se esperó a que el runner la
reclamara y enviara la tarea al broker, y entonces se **mató el proceso del
tablero**, se dejó caído 20 segundos y se levantó otra vez.

La bitácora de la tarjeta, con el corte en medio:

```
- 21:54 UTC  agora-api: CARD accepted from authenticated client provisional-client.
- 21:55 UTC  ai-1: Claimed CARD atomically and began admission.
- 21:55 UTC  ai-1: AI_Broker task submitted: task_b7938e3c9e54430e93a5c82db1550aed.
        ←── aquí se mató el tablero y se volvió a levantar ──→
- 21:58 UTC  ai-1: AI_Broker task completed: task_b7938e3c9e54430e93a5c82db1550aed.
- 21:58 UTC  ai-1: Effective model: ollama/local/qwen3.8:27b.
- 21:58 UTC  ai-1: Broker invocations: 1; cost USD: 0.00000000.
- 21:58 UTC  ai-1: CARD closed.
```

| Comprobación | Resultado |
|---|---|
| La tarjeta llega a `done` | sí |
| El API la marca `terminal` | `true` |
| Artefactos | **1**, no duplicado |
| Intentos consumidos por el corte | **0** |
| Tarea del broker antes y después | **la misma**: `task_b7938e3c…` |
| Invocaciones al broker | **1**, de 21:55:02 a 21:58:40 |
| Se encuentra por su referencia externa | sí |
| Ciclo completo visible en el cursor | `created → claimed → progressed → progressed → closed` |
| El cursor denuncia huecos | **no** |
| Una cancelada es terminal | `archive`, `terminal: true` |

**Veredicto: CORRECTO.** La invocación al broker abarca el corte de punta a
punta: empezó antes de matar el tablero y terminó después de levantarlo, una
sola vez.

### Una prueba mia inestable, encontrada y arreglada

La suite completa fallo una vez de cada tres, siempre en una de las dos pruebas
de reinicio, y pasaba siempre al ejecutarla sola. La causa era mia: la fixture
de `_runner` trae `zombie_timeout_seconds=3` —correcto para las pruebas de
zombi— y bajo carga el reinicio del runner tardaba mas de esos tres segundos,
asi que la tarjeta se cancelaba **como zombi** en vez de reanudarse.

No decia nada del codigo: decia que la prueba dependia de un reloj para medir
algo que no es tiempo. Las pruebas de reanudacion usan ahora una ventana amplia
(`SIN_PRISA`), y el umbral conserva sus propias pruebas. Cinco pasadas seguidas
de la suite entera, en verde.

### Un error del guion, no del sistema

La primera ejecución del verificador se quedó colgada sin consumir CPU (0,07 s y
parado). La causa: los subprocesos de PowerShell heredaban `stdin` y se quedaban
esperando en una consola sin terminal. Se arregló con `stdin=DEVNULL` y está
anotado en la cabecera del guion. El trabajo del tablero, mientras tanto, había
ido bien: la evidencia de arriba es de esa misma ejecución.

## 7. Compatibilidad hacia atrás

Las tres propiedades de `BoardState` son añadidos; nada las consume
obligatoriamente. `terminal` es una clave **más** en respuestas que ya existían:
un cliente que no la lea sigue funcionando igual.

Los eventos ya guardados en disco no la llevan, como pasaba en A09 con `state`.
Son historia; los nuevos sí.

## 8. Limitaciones declaradas

El prompt admite dobles cuando el entorno no da la integración real, y aquí hay
dos casos:

- **El reinicio del runner** se prueba con un broker falso y un `AiRunner` nuevo
  con el mismo id, que es lo que sobrevive a un reinicio real: el tablero y el
  disco, no el objeto en memoria. Reiniciar el runner de verdad exige acceso al
  PC de IA, que desde aquí no hay.
- **El broker caído** se simula con un transporte que lanza `ConnectError`.
  Apagar el broker real dejaría sin servicio al resto del laboratorio.

Lo que sí se hizo contra el sistema real es el corte del tablero (§6), que era
el escenario que ningún doble podía reproducir.

## 9. Riesgos y deuda

- **El runner del PC de IA sigue en Agora 0.2.2.** Todo lo de A07 a A10 es del
  lado del tablero, así que funciona; pero la distancia entre las dos piezas ya
  es de cuatro versiones. Conviene igualarlas en la próxima visita al PC de IA.
- ~~**No se ha probado el runner muriendo a mitad contra el sistema real** (§8).~~
  Ejecutado el 19-09-2026, y hacía falta: el escenario ocurrió de verdad y dejó
  una tarjeta irrecuperable. Ver
  [HALLAZGO_20260919_TARJETA_HUERFANA.md](HALLAZGO_20260919_TARJETA_HUERFANA.md)
  §9, con lo que ese montaje no llega a probar (§9.4).
- **`ProfileSummary.source` sigue exponiendo una ruta del sistema de ficheros.**
  Deuda abierta desde A07 §9; el sitio es A12.
- **Un `blocked` no vuelve solo.** Es deliberado —hace falta una decisión
  humana— pero significa que un cliente puede quedarse esperando un trabajo que
  no se moverá. Con `terminal: false` al menos sabe que no ha acabado; saber que
  está atascado exige mirar el estado, no solo la bandera.

## 10. Siguiente fase

A11 — estado simplificado para clientes.
