# A09 — Sondeo primero, callbacks como extensión

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A09_polling_primero_callbacks_opcionales_despues.md`.

## 1. Resumen

Un cliente de Agora **no necesita abrir un puerto, ni exponer una URL, ni
escuchar nada**. Le basta con guardar un número y volver cuando quiera. Esa es
la propiedad que esta fase asegura —verificada contra el tablero real siguiendo
una tarjeta de principio a fin sin leerla ni una vez (§9)— y por la que los
callbacks se quedan en diseño y no en código.

Los eventos con cursor ya existían (`GET /api/v1/events?after=N`), y ahí estaba
media capacidad. Lo que faltaba eran las dos cosas que hacen que sondear sea
suficiente de verdad:

1. **Los eventos no decían nada útil.** Solo llevaban `filename`, así que el
   cliente tenía que hacer un `GET /cards/{filename}` por cada evento para saber
   qué había pasado. Sondear salía caro justo cuando había movimiento.
2. **Un cursor que se quedaba atrás perdía eventos en silencio.** El almacén
   guarda una ventana; un cliente apagado el tiempo suficiente volvía, recibía
   lo que quedaba y **se creía al día**. Eso es peor que un error, porque no se
   nota.

## 2. Archivos

Creados:

- `tests/test_polling_and_events.py` — 17 pruebas.
- `scripts/verify_a09_polling.py` — el ciclo contra el tablero real.

Modificados:

- `src/agora/api/storage.py` — `EventPage`, y `EventStore.page()` junto a la
  `since()` de siempre.
- `src/agora/api/app.py` — `/api/v1/events` pagina y avisa del hueco; los
  eventos llevan `state` y `external_reference`.

## 3. El protocolo de sondeo

```http
GET /api/v1/events?after=0&limit=200
```

```json
{
  "events": [
    {
      "id": 41,
      "at": "2026-09-08T21:14:02Z",
      "kind": "card.closed",
      "data": {
        "filename": "curso-python.md",
        "state": "done",
        "external_reference": { "system": "cliente", "id": "tarea-4821", "version": 1 }
      }
    }
  ],
  "cursor": 41,
  "oldest_available": 12,
  "newest": 41,
  "missed": false,
  "more": false
}
```

El bucle del cliente es todo lo que hace falta:

1. Guardar `cursor`.
2. Volver con `after=cursor` cuando le convenga.
3. Si `more` es `true`, volver **inmediatamente**: quedan eventos en cola.
4. Si `missed` es `true`, reconciliar (§5).

### Qué trae cada evento, y por qué

- **`state`**: el estado en que quedó la tarjeta. Con esto el cliente actúa
  sobre el evento, sin un `GET` por tarjeta.
- **`external_reference`**: su propio identificador (A08), si la tarjeta lo
  lleva. Con esto ata el evento a su trabajo sin leer nada más. Una tarjeta sin
  referencia produce un evento limpio, sin la clave.
- Los `kind` son los del ciclo de vida: `card.created`, `card.claimed`,
  `card.progressed`, `card.closed`, `card.yielded`, `card.cancelled`,
  `card.unblocked`.

Una CARD ilegible no tumba su evento: lo que se estaba haciendo con ella ya
ocurrió, y el evento sale sin la referencia en vez de fallar.

## 4. Eficiencia

- **`limit`** acota la página (1..1000, por defecto 200). Un cliente que vuelve
  tras un fin de semana no se traga miles de eventos de golpe.
- **Sondear sin novedades es barato**: lista vacía, `more: false`, y el cursor
  no se mueve hacia atrás. Es el caso normal y no cuesta prácticamente nada.
- **Un evento por cambio, no un `GET` por tarjeta.** Un cliente con veinte
  tarjetas en vuelo hacía veinte peticiones por vuelta; ahora hace una.

Lo que **no** se ha hecho: *long polling*. Mantener la petición abierta
ahorraría latencia, pero ata un hilo del servidor por cliente y complica los
tiempos de espera en los dos extremos. La latencia que da un sondeo cada pocos
segundos es de sobra para trabajo que tarda minutos.

## 5. El hueco, y por qué se dice en voz alta

`EventStore` guarda una ventana de los últimos N eventos. Un cliente que vuelve
con un cursor anterior a esa ventana **tiene un agujero**: eventos que ocurrieron
y que no verá nunca.

La respuesta lo dice:

- **`oldest_available`**: el evento más antiguo que el tablero conserva.
- **`missed: true`**: el cursor quedó por detrás. No es un error HTTP —no ha
  fallado nada— sino el aviso de que hay que reconciliar.

`after=0` **nunca** es un hueco: es un cliente que empieza de cero y no tenía
cursor que quedarse atrás. Hay una prueba de eso, porque confundir las dos cosas
haría que todo cliente nuevo creyera haber perdido trabajo.

Cuando `missed` es `true`, la salida es la de A08: buscar por referencia externa
las tarjetas que le importan y leer su estado actual. Por eso las dos fases
encajan: el cursor es el camino barato, y la referencia externa es la red de
seguridad.

## 6. Callbacks: el diseño, y por qué no están

La TAREA pedía «diseñar callbacks como extensión no obligatoria». Están
diseñados aquí y **no implementados**, deliberadamente.

La forma que tendrían:

- Un registro por cliente: URL de destino, qué `kind` le interesan, y un secreto
  compartido para firmar la entrega.
- La entrega sería **al menos una vez**, con reintentos y desistimiento, y
  llevaría el mismo `id` de evento que el cursor. Así un cliente puede usar los
  dos a la vez sin duplicar trabajo: el `id` es el que deduplica.
- El sondeo seguiría siendo la verdad. Un callback perdido no pierde nada:
  el evento sigue en el cursor.

Y por qué no se implementan hoy:

- **Nadie los necesita.** Gestión de Tareas es una aplicación de escritorio que
  puede sondear. Exigirle un listener entrante sería pedirle que abra un puerto
  en el PC de un usuario.
- **Traen problemas que no traía nada de esto**: peticiones salientes hacia una
  URL que da el cliente (SSRF), gestión de secretos, colas de reintento,
  desistimiento, y un servicio que ahora falla por causas ajenas.
- Añadirlos «por si acaso» sería exactamente la sobrearquitectura que las
  restricciones de la fase piden evitar.

Hay una prueba que comprueba que **no existe** ningún endpoint de suscripción.
Si algún día se añade uno, esa prueba fallará y habrá que decidirlo a
conciencia, en vez de que aparezca sin querer.

## 7. Pruebas añadidas

`tests/test_polling_and_events.py`, 17 pruebas:

- **Sondear basta** (7): se sigue todo desde un cursor; el evento dice el estado
  sin una segunda petición; lleva el identificador del cliente; una tarjeta sin
  referencia da un evento limpio; la vida entera de una tarjeta se ve desde el
  cursor; sondear sin novedades da lista vacía y no mueve el cursor hacia atrás.
- **Paginación** (3): la página se acota y avisa de que hay más; seguir el
  cursor recorre cada evento **una sola vez**; un `limit` absurdo es `422`.
- **El hueco** (5): un cursor por detrás de la ventana recibe `missed: true`;
  uno dentro, `false`; un cliente que empieza de cero no ha perdido nada; un
  tablero vacío no inventa un hueco; el aviso llega por el API.
- **Sin listener** (2): no existe ningún endpoint de suscripción; sondear exige
  las mismas credenciales que todo lo demás.

Suite completa: **397 pruebas, todas en verde** (eran 380 al cerrar A08).

## 8. Compatibilidad hacia atrás

`GET /api/v1/events?after=N` responde igual que antes en la clave `events`; lo
que hay es **más** información al lado. Un cliente que solo leyera `events`
sigue funcionando sin tocar nada.

`EventStore.since()` se conserva intacta junto a la nueva `page()`.

Los eventos ya emitidos y guardados en disco no tienen `state` ni
`external_reference`. No se migran: son historia, y un cliente que los lea verá
un evento sin esas claves. Lo que cuenta es que los nuevos las llevan.

## 9. Punta a punta contra el tablero real: **hecha**

Las 17 pruebas corren contra el API real con `TestClient` y contra el
`EventStore` real en disco, sin dobles. Ademas se siguio una tarjeta entera
contra el **tablero real en 0.2.5**, con el runner del PC de IA y el broker de
verdad: `scripts/verify_a09_polling.py`.

La regla del guion es la que da sentido a la fase: **ni un solo
`GET /cards/{filename}` durante todo el ciclo**. Si sondear es suficiente de
verdad, el cliente tiene que enterarse de que su trabajo termino sin preguntar
por la tarjeta ni una vez.

Lo que vio, solo por el cursor:

```
card.created       -> pending      ref=tarea-39611938
card.claimed       -> in-progress  ref=tarea-39611938
card.progressed    -> in-progress  ref=tarea-39611938
card.progressed    -> in-progress  ref=tarea-39611938
card.closed        -> done         ref=tarea-39611938
```

| Comprobacion | Resultado |
|---|---|
| Llega a `done` sin leer la tarjeta | si |
| Se ve nacer y cerrarse | `card.created` … `card.closed` |
| Cada evento traia su estado | si |
| Cada evento traia la referencia del cliente | los 5 |
| Coste de seguirla | **19 peticiones al cursor**, 16 de ellas sin novedades |
| Sondear en reposo | lista vacia, cursor quieto, sin hueco inventado |
| Recorrer los 54 eventos del tablero paginando de 5 en 5 | ninguno repetido, en orden |
| `/webhooks`, `/callbacks`, `/subscriptions` | `404` los tres |

**Veredicto: CORRECTO**, sin ninguna comprobacion en rojo.

Las 16 peticiones vacias son el coste real de seguir un trabajo de unos tres
minutos con un sondeo cada diez segundos. Cada una devuelve una lista vacia y el
mismo cursor: es exactamente el caso barato que la fase queria asegurar.

## 10. Riesgos y deuda

- **El tamaño de la ventana no es configurable por despliegue**: `EventStore`
  se crea con `limit=10_000` en `create_api`. Suficiente de sobra hoy, y con
  `missed` el cliente ya no se queda a ciegas si algún día no lo fuera.
- **Los eventos antiguos no llevan `state`** (§8).
- **No hay filtro por `kind` ni por tarjeta.** Un cliente que solo quiera
  `card.closed` recibe todo y descarta. Con el volumen de un tablero personal no
  compensa; el sitio sería un parámetro más en `/events`.
- **La deuda de `ProfileSummary.source`** sigue abierta desde A07 §9.

## 11. Siguiente fase

A10 — recuperación robusta de trabajos de IA.
