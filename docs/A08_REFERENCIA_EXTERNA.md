# A08 — Referencia externa y reconciliación

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A08_correlation_external_reference.md`.

## 1. Resumen

Un cliente ya tiene un identificador para su trabajo. Hasta ahora, para atarlo
con una CARD tenía que **codificarlo dentro del `filename`**, que es el nombre
de un fichero: charset limitado, longitud limitada, y el espacio de nombres del
tablero, no el suyo.

A08 añade `external_reference`: un campo genérico y versionado que Agora guarda
tal cual y deja buscar, más `GET /api/v1/cards?system=…&external_id=…` para
reconciliar. Verificado **contra el tablero real** tirando el `filename` a
propósito (§7).

La parte más importante del diseño es lo que este campo **no** es:

| No es | Por qué importa | Cómo se sostiene |
|---|---|---|
| **Una clave de seguridad** | Conocer una referencia no puede dar acceso a nada | Buscar exige `cards:read` como cualquier lectura; hay una prueba sin credenciales |
| **Única** | Un cliente puede partir su trabajo en dos, o reintentar | Buscar devuelve una **lista**; hay una prueba con dos tarjetas compartiéndola |
| **La identidad de la tarjeta** | Esa sigue siendo el `filename` | La respuesta de creación no la devuelve; la idempotencia no la mira |

Cualquiera de las tres la convertiría en una clave primaria de facto, y una
clave inventada por accidente es muy difícil de quitar después.

## 2. Archivos

Creados:

- `tests/test_external_reference.py` — 19 pruebas.
- `scripts/verify_a08_external_reference.py` — el caso contra el tablero real.

Modificados:

- `src/agora/api/contracts.py` — `ExternalReference` y el campo opcional en
  `CreateCardRequest`.
- `src/agora/api/service.py` — se guarda al crear; `find_by_external_reference`.
- `src/agora/api/app.py` — `GET /api/v1/cards`.
- `src/agora/cards.py` — `Card.create` admite la referencia.
- `pyproject.toml`, `src/agora/__init__.py` — **0.2.3 → 0.2.4**.

## 3. El campo

```json
POST /api/v1/cards
{
  "filename": "curso-python.md",
  "function": "decompose",
  "request": "break down work: impartir un curso de Python",
  "external_reference": {
    "system": "cliente-cualquiera",
    "id": "tarea-4821",
    "version": 1
  }
}
```

- **`system`**: de quién es el identificador. Un nombre, no una URL ni una
  credencial.
- **`id`**: el identificador, con la forma que tenga. Agora **no lo interpreta**:
  `urn:uuid:9f3c/tarea#7` se guarda igual de bien que `4821`. Hay una prueba.
- **`version`**: la forma **de este campo**, no la del trabajo del cliente. Por
  defecto `1`. Sube el día que se le añadan campos, para que un cliente antiguo
  sepa qué está leyendo. Colar una clave no declarada da `422`.

Se devuelve en `GET /api/v1/cards/{filename}`, dentro de `metadata`.

## 4. Reconciliar

```http
GET /api/v1/cards?system=cliente-cualquiera&external_id=tarea-4821
```

```json
{
  "cards": [
    { "filename": "curso-python.md", "state": "done",
      "external_reference": { "system": "cliente-cualquiera", "id": "tarea-4821", "version": 1 } }
  ]
}
```

El caso real: un corte de red justo después de crear la tarjeta, o una base de
datos del cliente que se restaura desde una copia. El cliente conserva su id,
no el `filename`. Con esto lo recupera sin adivinar.

Busca **en todos los estados**, porque reconciliar sirve sobre todo para trabajo
que ya terminó o se canceló.

El `system` se compara sin distinguir mayúsculas, porque es un nombre. El `id`
**no**: es del cliente y no se toca. Hay una prueba de las dos cosas.

### Sobre la indexación

La TAREA decía «indexación/búsqueda si aporta valor». La búsqueda aporta —es el
caso de uso entero de la fase—; **el índice no**, y por eso no está. El tablero
es un directorio de trabajo humano, no una base de datos: recorrerlo cuesta lo
que hay. Un índice sería un segundo sitio donde vive la verdad, que habría que
mantener sincronizado con el sistema de ficheros —que es la fuente de verdad de
Agora— y reconstruir cuando se desincronizara. Complejidad sin un caso que la
pida. Si algún día un tablero tiene miles de tarjetas y esto se nota, el sitio
para arreglarlo es `find_by_external_reference`.

## 5. Compatibilidad hacia atrás

El campo es opcional. Una tarjeta sin él se crea, se despacha y se cierra
exactamente igual que antes, no aparece en las búsquedas, y su metadata no lleva
la clave. Hay dos pruebas de esto.

Las CARD antiguas del tablero no necesitan migración: la ausencia del campo es
un estado válido y permanente, no un pendiente.

## 6. Pruebas añadidas

`tests/test_external_reference.py`, 19 pruebas:

- **Se guarda y se devuelve** (5): sobrevive en la CARD; el cliente la recupera
  al preguntar por la tarjeta; la versión por defecto es 1; una versión
  declarada se respeta; un identificador con forma rara se guarda tal cual.
- **Reconciliar** (5): se encuentra la tarjeta habiendo perdido el `filename`;
  el `system` no distingue mayúsculas y el `id` sí; dos tarjetas pueden
  compartir referencia y se devuelven las dos; una referencia que nadie usó
  devuelve lista vacía; se busca en todos los estados, incluida una cancelada.
- **No es una clave** (3): sin credenciales no se busca; la respuesta de
  creación no devuelve la referencia; dos tarjetas con la misma referencia y
  claves de idempotencia distintas son dos tarjetas.
- **Compatibilidad** (3): una tarjeta sin referencia funciona igual y no lleva
  la clave; no aparece en las búsquedas.
- **Validación** (3): una referencia vacía es `422`; un campo inventado dentro
  de la referencia es `422`; buscar con solo una mitad es `422`.

Suite completa: **380 pruebas, todas en verde** (eran 361 al cerrar A07).

## 7. Punta a punta contra el tablero real: **hecha**

Las 19 pruebas corren contra el API real con `TestClient` —mismo FastAPI, misma
idempotencia en disco, mismo tablero en el sistema de ficheros, sin dobles—, y
ademas se ejercio el caso completo contra el **tablero real en 0.2.4**, con el
runner del PC de IA y el broker de verdad:
`scripts/verify_a08_external_reference.py`.

El guion reproduce el caso que justifica la fase: **se crea la tarjeta y acto
seguido se tira el `filename`**. A partir de ahi el cliente solo tiene su propio
identificador.

| Comprobacion | Resultado |
|---|---|
| Crear con referencia | `201` |
| La respuesta **no** devuelve la referencia | correcto: no es la identidad |
| Buscar solo con el id propio | encuentra **una** tarjeta y devuelve su `filename` |
| La referencia vuelve intacta | `{system, id, version: 1}` |
| Esta en la metadata de la CARD | si |
| Dos tarjetas con la misma referencia | la busqueda devuelve **las dos** |
| Buscar sin credenciales | `401` |
| Buscar con media referencia | `422` |
| Campo inventado dentro de la referencia | `422` |
| Tarjeta **sin** referencia | se crea igual y no lleva la clave |
| Ciclo con runner real | `pending` → `in-progress` → `done` |
| Buscarla ya terminada | la sigue encontrando, con `state: done` |

**Veredicto: CORRECTO**, sin ninguna comprobacion en rojo. Las tarjetas de
prueba quedaron canceladas.

Lo unico que no se pudo comprobar contra el tablero real es la sensibilidad a
mayusculas del `system` frente al `id` (§4), porque exige controlar como se
escribio la referencia original; esta cubierto por prueba unitaria.

## 8. Riesgos y deuda

- **La búsqueda recorre el tablero entero** (§4). Es una decisión, no un olvido,
  pero conviene saber dónde está el techo.
- **`ProfileSummary.source` sigue exponiendo una ruta del sistema de ficheros**
  en `GET /api/v1/profiles`. Deuda arrastrada de A07 §9; el sitio para quitarlo
  con su versión es A12.
- **La referencia no se puede añadir después de crear la tarjeta.** Si un
  cliente la olvida, tiene que cancelar y crear otra. No se ha visto un caso que
  lo pida, y un `PATCH` abriría la puerta a reescribir la CARD por el API, que
  hoy es de escritura estrictamente controlada.

## 9. Siguiente fase

A09 — polling primero, callbacks opcionales después.
