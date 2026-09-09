# A11 — Estado simplificado para clientes

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A11_estado_simplificado_para_clientes.md`.

## 1. Resumen

El tablero tiene seis estados que son carpetas del sistema de ficheros. Eso es
la verdad de Agora y no se toca. Pero obligar a un cliente a conocerlas
significa dos cosas malas: que tiene que aprenderse la estructura interna de
otro sistema, y que el día que Agora añada un estado —o parta uno en dos— el
cliente se rompe.

A11 añade una **proyección**: `status`, con el vocabulario que un cliente
necesita para decidir qué hacer. El estado interno **sigue viajando al lado**:
esto traduce, no oculta.

Verificado contra el tablero real, incluidos los dos casos de `blocked` (§6).

## 2. Archivos

Creados:

- `src/agora/api/projection.py` — el mapeo, en un solo sitio.
- `tests/test_client_status_projection.py` — 21 pruebas.

Modificados:

- `src/agora/board.py` — el bloqueo registra un `code`; la cancelación guarda
  también el motivo.
- `src/agora/dispatcher.py` — pasa `code="untrusted_origin"` al bloquear.
- `src/agora/api/app.py` — `status` y `error` en el estado de la tarjeta y en
  cada evento.
- `pyproject.toml`, `src/agora/__init__.py` — **0.2.6 → 0.2.7**.

## 3. El mapeo

| Estado interno | Condición | `status` | `terminal` |
|---|---|---|---|
| `pending` | — | `queued` | `false` |
| `scheduled` | — | `queued` | `false` |
| `in-progress` | — | `running` | `false` |
| `done` | — | `completed` | `true` |
| `archive` | — | `cancelled` | `true` |
| `blocked` | agotó sus intentos | **`failed`** | `false` |
| `blocked` | cualquier otro motivo | **`blocked`** | `false` |

Hay una prueba que recorre `BoardState` entero: un estado sin traducir sería un
cliente sin respuesta.

### Dos decisiones que conviene tener escritas

**`scheduled` es `queued`.** Para el cliente no hay diferencia entre «aún no le
toca» y «espera turno»: en los dos casos su trabajo no ha empezado y no hay nada
que hacer. Que Agora distinga entre las dos es asunto de Agora.

**`blocked` se parte en dos.** Una tarjeta que agotó sus intentos **falló**, y
eso es un desenlace del trabajo. Una parada por cualquier otro motivo —un origen
que no es de confianza, un bloqueo administrativo— **no es un fallo del
trabajo**: es que alguien tiene que mirarlo. Meter las dos cosas en la misma
palabra le diría al cliente que su encargo salió mal cuando puede que ni
siquiera se haya intentado.

### `cancelled`, que no estaba en la lista del prompt

El prompt pedía `queued/running/completed/failed/blocked`. Falta un sitio para
una tarjeta cancelada, y las dos alternativas eran peores:

- llamarla `failed` diría que el trabajo salió mal, cuando lo que pasó es que el
  usuario cambió de idea;
- llamarla `completed` sería directamente falso.

Así que la proyección tiene seis valores, no cinco. Es una desviación del
enunciado, y está aquí escrita a propósito.

### La terminalidad no se reescribe

`terminal` sale de `BoardState.is_terminal` (A10), no de una segunda lista en
este módulo. Hay una prueba que compara las dos para todos los estados: dos
listas que dicen lo mismo acaban discrepando.

## 4. Errores tipados

Cuando `status` es `failed`, `blocked` o `cancelled`, la respuesta lleva un
`error`:

```json
{
  "state": "blocked",
  "status": "failed",
  "terminal": false,
  "error": {
    "code": "attempts_exhausted",
    "message": "AI_Broker task ended as failed tras 3 intentos",
    "attempts": 3,
    "max_attempts": 3
  }
}
```

Los códigos son estables y un cliente puede ramificar sobre ellos:
`attempts_exhausted`, `untrusted_origin`, `cancelled_by_client`, `unknown`. El
`message` es para una persona y puede cambiar sin avisar.

**El código se escribe en el momento de bloquear**, no se deduce después leyendo
el texto. Quien bloquea es quien sabe el motivo; adivinarlo buscando palabras en
una frase se rompe en cuanto alguien reescribe la frase. Por eso
`Board.block_pending` y `_block_loaded` reciben un `code`, y el despachador pasa
`untrusted_origin` al suyo.

Una tarjeta bloqueada **antes** de A11 no lleva código. La proyección dice
`unknown` y conserva el texto: es la verdad, y es mejor que inventarse una
categoría. Hay una prueba de eso, y otra de que un código no reconocido tampoco
se acepta.

## 5. El estado interno no se pierde

`status` es una clave **más**. `state` sigue ahí, y también toda la `metadata` y
la bitácora. Un cliente que ya leía `state` no tiene que cambiar nada; uno nuevo
puede ignorar `state` por completo.

Lo mismo en el cursor de A09: cada evento lleva ahora `status`, `terminal` y
—cuando lo hay— `error`, además del `state` de siempre.

## 6. Resultados reales

Contra el tablero real en 0.2.7, con el runner del PC de IA y el broker.

### Un encargo de principio a fin

```
pending      -> queued
in-progress  -> running
done         -> completed
```

Nace `queued` sin `error`, pasa por `running`, acaba `completed` y `terminal`.
En el cursor: `['queued', 'running', 'running', 'running', 'completed']`.

### Cancelado no es fallado

| Comprobación | Resultado |
|---|---|
| Una cancelada se llama `cancelled` | sí |
| Con código tipado | `cancelled_by_client` |
| Y el motivo que dio el cliente | «cambio de planes» |
| El estado interno sigue siendo `archive` | sí |

### Los dos `blocked`, sobre tarjetas bloqueadas de verdad

```
failed:  {'code': 'attempts_exhausted', 'message': 'AI_Broker task ended as failed
          tras 3 intentos', 'attempts': 3, 'max_attempts': 3}
blocked: {'code': 'untrusted_origin', 'message': 'Untrusted origin: alguien-de-fuera'}
```

Las dos están en la carpeta `blocked`. El cliente ve `failed` en una y `blocked`
en la otra, con su código, y ninguna es terminal — porque las dos se pueden
desbloquear.

**Veredicto: CORRECTO** en las dos tandas, sin ninguna comprobación en rojo.

## 7. Pruebas añadidas

`tests/test_client_status_projection.py`, 21 pruebas:

- **El mapeo** (4 + 4 parametrizadas): cada estado interno a su proyección;
  ninguno se queda sin traducir; `scheduled` y `pending` son lo mismo para el
  cliente; la terminalidad coincide con la del tablero para todos.
- **`blocked` partido en dos** (3): agotar intentos es `failed` con sus cifras;
  otro motivo es `blocked`; ninguno es terminal.
- **Errores tipados** (5): el código se escribe al bloquear; una tarjeta antigua
  sin código dice `unknown` y conserva el texto; un código inventado no se
  acepta; una cancelada explica quién y por qué; lo que fue bien no lleva error.
- **El interno no se pierde** (2): viaja al lado; la proyección no lo reemplaza.
- **El cursor** (3): el evento lleva `status`; el de una cancelación se explica;
  `state` sigue estando para quien lo usara.

Suite completa: **437 pruebas, todas en verde** (eran 416 al cerrar A10).

## 8. Compatibilidad hacia atrás

- `status`, `terminal` y `error` son claves nuevas junto a las de siempre.
- Las CARD bloqueadas antes de A11 no tienen `code` y se proyectan como
  `unknown` sin fallar.
- `card.metadata["cancelled"]` pasa de ser una marca de tiempo suelta a
  `{"at", "reason"}`. Se comprobó que **nadie más lo leía** antes de cambiarlo;
  una CARD antigua con el formato viejo se proyecta igual, porque el motivo se
  busca con `isinstance(..., dict)` y si no está se usa un texto neutro.

## 9. Riesgos y deuda

- **`unknown` es la puerta de escape.** Cualquier bloqueo que no pase por los
  dos sitios instrumentados aparecerá así. Es honesto, pero conviene revisar que
  no se acumulen: si aparecen muchos, es que falta instrumentar un camino.
- **El runner del PC de IA sigue en 0.2.2.** Nada de A07–A11 le afecta, pero la
  distancia crece. Deuda arrastrada de A10 §9.
- **`ProfileSummary.source` sigue exponiendo una ruta del sistema de ficheros.**
  Abierta desde A07 §9; el sitio es A12.
- **Las tarjetas de muestra `a11-fallada.md` y `a11-parada.md`** se quedan en
  `blocked` en el tablero real como evidencia. Se pueden desbloquear o archivar
  cuando estorben.

## 10. Siguiente fase

A12 — versionado de perfiles y contratos.
