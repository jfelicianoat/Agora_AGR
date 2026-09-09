# A07 — Contrato de integración cliente/Agora

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A07_contrato_de_integracion_cliente_agora.md`.

Este documento **es** el entregable: lo que un cliente externo necesita saber
para depositar trabajo de IA en Agora y recogerlo más tarde. Aquí no aparece
ningún concepto de Gestión de Tareas, y hay una prueba que lo comprueba
recorriendo todo lo que el API responde.

## 1. Resumen

Verificado **de punta a punta contra el tablero real**, autenticandose como un
cliente externo: descubrir → crear → estado → artefacto → cancelar (§8).

El flujo `create CARD → status → artifacts → cancel` **ya existía**: los cuatro
endpoints estaban implementados, con idempotencia real y estados persistentes.
Lo que faltaba era que estuviera escrito, que el cliente pudiera **descubrir**
qué pedir y qué va a recibir, y —esto no se sabía— que el flujo funcionara de
verdad para un cliente que no se llame `human`.

Porque no funcionaba. Ver §6.

## 2. Archivos

Creados:

- `docs/A07_CONTRATO_INTEGRACION_CLIENTE.md` — esto.
- `tests/test_client_integration_contract.py` — 23 pruebas.
- `scripts/verify_a07_client_flow.py` — el flujo contra el tablero real.

Modificados:

- `src/agora/api/app.py` — nuevo `GET /api/v1/contracts`; el API declara de
  confianza a su propio principal al arrancar.
- `src/agora/application.py` — `trusted_origins` configurable y `trust_origin()`;
  `ProfileSummary.produces`.
- `src/agora/dispatcher.py` — `DEFAULT_TRUSTED_ORIGINS` con nombre propio.
- `src/agora/api/service.py` — el despachador del API usa los orígenes de la
  aplicación.

---

## 3. El flujo, con ejemplos

Todo el API vive bajo `https://<tablero>:8741/api/v1`. **HTTPS obligatorio**:
una petición por HTTP se rechaza con `400 HTTPS is required`. Toda petición
lleva `Authorization: Bearer <token>`.

### 3.0 Antes de la primera tarjeta: descubrir

Un cliente no debe adivinar ni el vocabulario ni la forma de la respuesta.

```http
GET /api/v1/profiles
```

```json
{
  "profiles": [
    {
      "name": "task-decomposer",
      "version": "1.3.0",
      "function": "decompose",
      "description": "Parte un encargo en pasos verificables.",
      "handles": ["decompose work", "break down work", "split into steps"],
      "refuses": ["schedule", "plan calendar"],
      "skills": ["decompose-work", "decompose-course", "decompose-study", "decompose-software"],
      "produces": ["work-breakdown@1"]
    }
  ]
}
```

Dos campos mandan aquí:

- **`handles` es vocabulario público.** El emparejamiento de una tarjeta con un
  perfil se hace **por handle, no por `function`**. Una petición que no empiece
  por uno de los handles declarados **no empareja**, y la tarjeta se queda
  quieta en `pending` sin que nadie diga nada. Es la trampa más fácil de pisar
  al integrarse; se pisó al verificar A05.
- **`produces`** dice qué documento devolverá, como `nombre@version`.

Y para saber qué forma tiene ese documento:

```http
GET /api/v1/contracts
```

```json
{
  "contracts": [
    {
      "name": "work-breakdown",
      "version": 1,
      "reference": "work-breakdown@1",
      "description": "Un encargo partido en pasos verificables, con esfuerzo y dependencias.",
      "schema": { "type": "object", "additionalProperties": false, "...": "..." },
      "example": { "contract": "work-breakdown", "contract_version": 1, "...": "..." }
    }
  ]
}
```

El cliente puede validar por su cuenta lo que recibe. El documento siempre
declara dentro de sí qué contrato y qué versión es, así que no hay que
adivinarlo por el contexto.

Ambos endpoints leen del disco en cada petición: corregir un contrato o un
perfil **no exige reiniciar el tablero**.

### 3.1 Crear la tarjeta

```http
POST /api/v1/cards
Idempotency-Key: 8f2c1e94-...
Content-Type: application/json

{
  "filename": "curso-python.md",
  "function": "decompose",
  "request": "break down work: impartir un curso de introduccion a Python",
  "body": "Seis sesiones para companeros sin experiencia previa.",
  "destination": "artifacts/curso.json",
  "max_attempts": 3,
  "priority": "normal"
}
```

```json
201 Created
{ "filename": "curso-python.md", "state": "pending", "replayed": false }
```

- **`filename` lo elige el cliente.** Es su asa: con él pregunta el estado y
  recoge el artefacto, sin tener que guardar ningún identificador que Agora
  invente.
- **`request` tiene que empezar por un handle declarado** (§3.0).
- `body` es el enunciado largo; `request` es la línea que empareja.
- `Idempotency-Key` es **obligatoria**. Sin ella: `400`.

### 3.2 Preguntar el estado

```http
GET /api/v1/cards/curso-python.md
```

```json
{
  "state": "in-progress",
  "metadata": { "attempts": 1, "profile": "task-decomposer", "paths": [] },
  "record": ["... la bitácora de la tarjeta ..."]
}
```

Esto es lo que hace que el trabajo sea **duradero**: el cliente puede apagarse,
volver al día siguiente y preguntar por `curso-python.md`. Si el PC de IA estaba
apagado, la tarjeta sigue en `pending` esperando.

### 3.3 Recoger el artefacto

```http
GET /api/v1/cards/curso-python.md/artifacts/0
```

Devuelve el fichero. El índice es la posición en `metadata.paths`. Una tarjeta
que aún no ha terminado no tiene artefactos: `404`.

Cuando el perfil declara un contrato, el artefacto **ya ha sido validado contra
él** antes de escribirse. Un documento que no cumple no llega nunca al cliente:
la tarjeta falla y reintenta. Esto no es teórico — ver A06 §7, donde un modelo
corrompió los nombres de campo y el contrato lo rechazó.

### 3.4 Cancelar

```http
POST /api/v1/cards/curso-python.md/cancel
Idempotency-Key: 4a7b...

{ "reason": "el usuario cambio de idea" }
```

La tarjeta pasa a `archive` con el motivo escrito en su bitácora. Cancelar no
borra nada: el historial se conserva.

---

## 4. Idempotencia

**Toda escritura exige `Idempotency-Key`.** La semántica es la que un cliente
necesita para reintentar sin miedo tras un corte de red:

| Situación | Respuesta |
|---|---|
| Primera vez con esa clave | Se ejecuta. `"replayed": false` |
| Misma clave, **mismo cuerpo** | No se ejecuta otra vez. Se devuelve la respuesta guardada con `"replayed": true` |
| Misma clave, **cuerpo distinto** | `409 Conflict`. La clave ya se gastó en otra cosa |
| Sin clave | `400` |

El cuerpo se compara por un digest, no por igualdad literal de bytes, así que
reordenar las claves del JSON no cuenta como cuerpo distinto.

Las claves son **por operación**: la misma clave puede usarse para un `create` y
para un `cancel` sin chocar. Aun así, conviene una clave nueva por intento
lógico.

---

## 5. Estados y errores

### Estados de una tarjeta

| Estado | Qué significa para el cliente |
|---|---|
| `pending` | Aceptada, esperando a que un runner la reclame. Si el PC de IA está apagado, se queda aquí |
| `in-progress` | Un runner la reclamó y está trabajando |
| `done` | Terminada. Hay artefacto, y cumple el contrato |
| `blocked` | No pudo terminar. El motivo está en su bitácora |
| `archive` | Cancelada, o retirada. Se conserva el historial |
| `scheduled` | Programada para más tarde |

Una tarjeta llega a `blocked` cuando agota `max_attempts`, o cuando su origen no
es de confianza (§6). El motivo siempre está escrito: `GET /api/v1/cards/...`
lo devuelve en el `record`.

### Códigos de error

| Código | Cuándo |
|---|---|
| `400` | HTTP en vez de HTTPS; falta `Idempotency-Key` |
| `401` / `403` | Sin credenciales, o sin el ámbito necesario |
| `404` | La tarjeta no existe; el artefacto no existe todavía |
| `409` | Clave de idempotencia reusada con otro cuerpo; transición de estado imposible (reclamar una tarjeta ya cerrada, cerrar una que no se reclamó) |
| `422` | El cuerpo no cumple el contrato del endpoint, o la CARD está mal formada |

Todos responden `{"detail": "..."}` con una frase que dice qué pasó.

### Ámbitos

`cards:read` para leer; `cards:write` para crear y cancelar; `board:claim` para
reclamar y cerrar (eso es de los runners, no del cliente); `board:admin` para
desbloquear.

---

## 6. El defecto que encontró esta fase

Escribir la prueba del ciclo entero destapó que **un cliente externo no podía
completar el flujo**.

El API pone `origin` al principal autenticado. El despachador tenía la lista de
orígenes de confianza clavada en el código: `("human", "athena", "agora",
"test")`. Cualquier cliente con otro nombre recibía **`201 Created`** y, acto
seguido, su tarjeta era bloqueada con «Untrusted origin». Lo había hecho todo
bien y su trabajo moría igual.

La lista no sobra: existe para que una tarjeta dejada a mano en la carpeta del
tablero no se ejecute sola. Pero **confundía dos cosas distintas**: una tarjeta
que llegó por HTTPS con un token válido ya ha pasado una comprobación más fuerte
que llamarse de una manera concreta.

El arreglo: `trusted_origins` es configurable, y `create_api` declara de
confianza a su propio principal al arrancar. Una tarjeta dejada a mano con un
origen extraño **sigue bloqueándose**, y hay una prueba de eso. Los valores por
defecto no cambian, y hay otra prueba de eso.

**Limitación declarada:** con el token provisional hay un solo principal y queda
cubierto. Con OAuth, el `sub` de cada token puede variar y no se registra
automáticamente; una tarjeta de un sujeto no registrado se bloqueará de forma
**visible** —estado `blocked` con el motivo en la bitácora—, pero se bloqueará.
Si hace falta, el sitio es `trust_origin()` al configurar el despliegue.

---

## 7. Pruebas añadidas

`tests/test_client_integration_contract.py`, 23 pruebas:

- **Descubrimiento** (6): el cliente ve `handles`, `refuses` y `function`; ve
  `produces`; un perfil sin contrato dice `[]`; el catálogo da esquema y
  ejemplo; un tablero sin contratos responde vacío y no un error; un contrato
  corregido se ve sin reiniciar.
- **El ciclo** (5): crear → estado → cancelar; crear → reclamar → cerrar →
  recoger el artefacto **y validarlo contra el esquema que el catálogo publicó**;
  404 de tarjeta desconocida; 404 de artefacto que no está; una tarjeta cerrada
  no se puede reclamar otra vez.
- **Idempotencia** (4): repetir no duplica y marca `replayed`; misma clave con
  otro cuerpo es `409`; sin clave se rechaza; cancelar dos veces con la misma
  clave da la misma respuesta.
- **Origen** (4): una tarjeta creada por el API es despachable; una dejada a
  mano con origen extraño sigue bloqueada; confiar dos veces no cambia nada;
  los valores por defecto no cambian.
- **Seguridad y frontera** (4): nada responde sin credenciales; HTTP se rechaza;
  el `health` dice de quién es el tablero; y **ningún concepto del cliente
  aparece en la superficie pública** — se recorre lo que devuelven `profiles`,
  `contracts`, `board` y `health` buscando `workblock`, `pomodoro`,
  `availability`, `calendar`, `scheduler`, `vacacion` y `gestion de tareas`.

Suite completa: **361 pruebas, todas en verde** (eran 338 al cerrar A06).

---

## 8. Punta a punta contra el tablero real: **hecha**

Las 23 pruebas corren contra el API real con `TestClient` —mismo FastAPI, misma
idempotencia en disco, mismo tablero— pero simulan el runner. Asi que ademas se
recorrio el flujo entero contra el **tablero real en 0.2.3**, con el runner del
PC de IA y el broker de verdad: `scripts/verify_a07_client_flow.py`.

Se autentica con el token del llavero del tablero, es decir **como un cliente
externo cualquiera**, con un principal que no es `human`. Eso es justo lo que no
se podia probar antes del arreglo del origen.

| Paso | Resultado |
|---|---|
| Descubrir `handles` y `produces` | `['decompose work', 'break down work', 'split into steps']`, `work-breakdown@1` |
| Descubrir el esquema y el ejemplo | del catalogo del propio tablero |
| Crear (`201`) | nombre elegido por el cliente, `pending`, `replayed: false` |
| Repetir con la misma clave | mismo fichero, `replayed: true`, **sin duplicar** |
| Misma clave, otro cuerpo | `409 Idempotency-Key was reused with a different request` |
| Sin clave | `400` |
| **Origen** | la tarjeta **es trabajo elegible** y no se bloquea |
| Ciclo de estados | `pending` → `in-progress` → `done` |
| Descargar el artefacto | `200` |
| Validar contra el esquema **que publico el catalogo** | cumple |
| Cancelar otra tarjeta | `200`, queda en `archive`; repetir con la misma clave da lo mismo |
| Tarjeta desconocida | `404` |

El documento devuelto: `kind='study'`, 3 pasos, 300 minutos, todos con criterio
de terminado. El encargo pedia ademas repartir los dias, y el aviso lo devolvio
donde corresponde:

> Se pidio tambien repartir el trabajo en dias concretos. Eso no corresponde a
> esta descomposicion: decidir que dia se hace cada paso lo hace el planificador
> del cliente.

**Veredicto: CORRECTO**, sin ninguna comprobacion en rojo.

## 9. Riesgos y deuda

- ~~El tablero de este PC sigue en 0.2.0~~ — **actualizado a 0.2.3**, con lo que
  se cierran tambien las paradas pendientes de A05 §8 y A06 §8. Board y runner
  quedan en 0.2.3 y 0.2.2 respectivamente; el runner no usa nada de lo que
  cambio A07.
- **OAuth y los orígenes de confianza** (§6): sujetos variables no se registran
  solos.
- **`ProfileSummary.source` expone una ruta del sistema de ficheros** en
  `GET /api/v1/profiles`. No es un secreto, pero tampoco le sirve de nada a un
  cliente y revela la estructura del PC del tablero. No se toca aquí porque
  quitarlo cambia una respuesta pública que alguien puede estar leyendo; el
  sitio para hacerlo con su versión es A12.
- **El catálogo no dice qué perfil produce cada contrato**, solo al revés
  (`produces` en el perfil). Basta para lo que hace falta hoy.

## 10. Siguiente fase

A08 — correlación y referencia externa. Es lo que le permite al cliente atar una
tarjeta de Agora con su propio identificador; hoy solo tiene `filename`.
