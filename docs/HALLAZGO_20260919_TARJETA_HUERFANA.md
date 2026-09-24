# Hallazgo — una tarjeta reclamada puede quedarse huérfana para siempre

**19 de septiembre de 2026.** Encontrado desde fuera: diagnosticando por qué el
cliente «Gestión de Tareas IA» veía un encargo suyo eternamente «en marcha».

**Estado: corregido el mismo día.** El diagnóstico de §1 a §3 se deja tal cual se
escribió, antes de tocar nada. Lo aplicado está en §7, y la comprobación contra
la tarjeta real en §8.

## 1. La evidencia

`job-ca559a4b560b.md`, en el tablero real, a las 15:47 UTC:

```
state    : in-progress
attempts : 0   (de max_attempts: 3)
agent    : ai-1
claimed  : 2026-09-19T06:43:39Z
profile  : task-intake@1.2.0
```

Su bitácora, **entera**:

```
06:43:23  agora-api: CARD accepted from authenticated client provisional-client.
06:43:39  ai-1: Claimed CARD atomically and began admission.
06:43:39  ai-1: Remote profile selected: task-intake@1.2.0.
```

Nada más. **Nueve horas y veintiséis minutos** de silencio.

Compárese con el recorrido sano que recoge [A10 §6](A10_RECUPERACION.md), donde
las dos primeras líneas y el envío al broker ocurren **en el mismo minuto**:

```
21:55  ai-1: Claimed CARD atomically and began admission.
21:55  ai-1: AI_Broker task submitted: task_b7938e3c9e54430e93a5c82db1550aed.
```

Falta justo esa tercera línea. Y las dos que sí están las escribe **la API**
(`api/service.py:141-144`), no el runner: son el acuse de la llamada a `claim`.
Es decir, `ai-1` reclamó la tarjeta y **murió o se colgó antes de llegar a
`submit()`**.

El tablero, mientras tanto, está perfectamente: responde, y el broker también
—ollama 94 s de latencia media ese día, lmstudio 205 s—. No es una caída.

## 2. Por qué no se recupera sola

Tres mecanismos deberían haberla rescatado. Ninguno puede.

### 2.1 El runner mira lo suyo primero — y no le sirve

`broker/runner.py:65-70` hace lo correcto: antes de buscar trabajo nuevo
pregunta por lo que ya tiene reclamado. Pero `_recover` (líneas 110-123) deja la
caducidad **dentro** de la rama que exige `task_id`:

```python
task_id = remote.get("task_id") if isinstance(remote, dict) else None
if isinstance(task_id, str) and task_id:
    claimed_at = _timestamp(card.metadata.get("claimed"))
    ...
    if age > zombie_after:
        return self._cancel_zombie(work, task_id)   # ← la edad se mira AQUÍ
```

Sin `task_id` no se comprueba ninguna edad. Y sin `task_id` tampoco hay nada que
cancelar en el broker, que es lo que `_cancel_zombie` necesita.

### 2.2 El `task_id` se apunta **después** de enviar

`broker/executor.py:234-235`:

```python
task_id = self.client.submit(payload)
checkpoint(task_id, key)
```

Entre reclamar la tarjeta y apuntar el `task_id` hay una ventana. Todo lo que
ocurre antes —`contract()`, que es red; `load_profile_skills`, que es disco;
`build_broker_request`, que lee adjuntos; y el propio `submit()`— puede colgarse
o morir. Una tarjeta huérfana **en esa ventana** no tiene ni edad que comprobar
ni tarea que cancelar.

Es la ventana más estrecha del recorrido y es la única sin red de seguridad.

### 2.3 El tablero tiene la red de seguridad, y no la usa nadie

`board.py:299 recover_zombies()` hace exactamente lo que hace falta, y **no
necesita `task_id`**: recorre lo que está en curso, compara el `claimed` de cada
tarjeta con un umbral, la devuelve a pendientes e **incrementa el intento**
—derivando a `blocked` cuando se agotan—. Está bien escrita y está probada:
`tests/test_board.py:95`, con un umbral de treinta minutos.

Se buscó en todo el repositorio quién la invoca. **Solo esa prueba.** Ni el
dispatcher, ni la API, ni un guion, ni una tarea programada.

El barrendero existe, funciona y nadie lo ha contratado.

## 3. Por qué esto era previsible

A10 §9 ya lo dejó anotado como deuda, con estas palabras:

> **No se ha probado el runner muriendo a mitad contra el sistema real.**

Y, en la misma lista:

> **El runner del PC de IA sigue en Agora 0.2.2.** Todo lo de A07 a A10 es del
> lado del tablero, así que funciona; pero la distancia entre las dos piezas ya
> es de cuatro versiones.

El escenario declarado sin probar es exactamente el que ha ocurrido, y la pieza
que falló es exactamente la que lleva cuatro versiones de retraso. El documento
acertó al señalarlo; lo que no se sabía es que el fallo deja la tarjeta
**irrecuperable**, no solo retrasada.

## 4. Qué se propone

Dos cambios, y el orden importa: el primero cierra el agujero para siempre, el
segundo lo estrecha.

1. **Contratar al barrendero.** Llamar a `board.recover_zombies()` desde el
   latido del dispatcher, con su umbral por configuración. Es lo único que
   protege del caso que de verdad pasa —el runner deja de existir—, porque no
   depende de que el runner esté vivo ni de que haya llegado a apuntar nada.
2. **Sacar la comprobación de edad de la rama del `task_id`** en
   `broker/runner.py:_recover`. Una tarjeta reclamada hace horas sin `task_id`
   no está «sin empezar»: está abandonada, y debe devolverse gastando intento.

Lo que **no** se propone: reintentar solo. Una tarjeta vuelve a pendientes con su
intento consumido, y a los tres acaba en `blocked`, que es donde una persona
decide. Reintentar sin contar convertiría un cuelgue en un bucle infinito que
además paga invocaciones al broker.

## 5. Efecto en el cliente, ya mitigado por su lado

«Gestión de Tareas IA» repetía «la IA está con ello» indefinidamente. Desde su
versión 1.43.0 avisa cuando un encargo lleva más de media hora sin señales y
ofrece cancelarlo, distinguiéndolo de «el tablero no responde» —que aquí no era
el caso: la conectividad daba `reachable` todo el rato—.

Eso es un parche en el cliente, no el arreglo: la tarjeta sigue ocupando el
tablero hasta que alguien la cancele a mano.

## 6. Cómo comprobar el arreglo, si se hace

El escenario que A10 §8 declaró como no probado contra el sistema real:

1. Crear una tarjeta y esperar a que `ai-1` la reclame.
2. **Matar el proceso del runner entre el `claim` y el `submit`** —la ventana de
   §2.2—, que es lo que ningún doble reproduce.
3. Comprobar que, pasado el umbral, la tarjeta vuelve a `pending` con
   `attempts: 1`, y que a los tres intentos acaba en `blocked`.
4. Comprobar que **no** se ha enviado nada al broker por esa tarjeta: el
   contador de invocaciones no debe moverse.

## 7. Qué se ha aplicado

Los dos cambios de §4, y **un tercero que apareció al comprobar quién llama a
qué** —el más importante de los tres—.

### 7.1 El barrendero, contratado

`Dispatcher.sweep_abandoned()` llama a `board.recover_zombies()` con un umbral
por configuración (`DEFAULT_ZOMBIE_TIMEOUT`, media hora; `None` lo desactiva) y
traduce cada rescate a un resultado del latido, `DispatchStatus.RECOVERED`.

`run_once()` lo ejecuta **antes** de repartir, no después: una tarjeta rescatada
vuelve a pendientes y puede repartirse en ese mismo latido. Al revés también
sería correcto, pero tardaría un latido de más en cada rescate.

`recover_zombies` gana un `dry_run` para que el ensayo no tenga que reimplementar
el cálculo de edad. Esa copia habría acabado divergiendo de la que decide.

### 7.2 La caducidad, fuera de la rama del `task_id`

En `broker/runner.py:_recover` la edad se mira ahora antes de preguntar por el
`task_id`. Con `task_id` sigue cancelándose la tarea en el broker
(`_cancel_zombie`); sin él no hay nada que cancelar —no llegó a enviarse— y basta
con soltar la tarjeta gastando intento (`_yield_abandoned`).

### 7.3 El barrido tenía que ir **de verdad** en la consulta del runner

Esto no estaba en §4 y era lo que habría dejado el arreglo en nada.

Se buscó quién construye un `Dispatcher` en producción. En el despliegue real
—API más runner del PC IA— hay **uno solo**: `api/service.py:work()`, el extremo
que el runner consulta en cada vuelta. Y lo llama siempre con `dry_run=True`,
porque ahí «en seco» no significa «ensayo»: significa que quien reclama es el
runner, no el tablero.

Es decir: atar el barrido a ese `dry_run` lo habría dejado informando del zombi
en cada consulta y sin rescatarlo jamás. El mismo fallo que este documento
denuncia, un piso más arriba y recién construido. Por eso `sweep_abandoned()` es
pública y `work()` la llama **con escritura**, aunque el reparto se simule.

### 7.4 Lo que se comprueba, y que las pruebas muerden

Ocho pruebas nuevas: seis en `tests/test_dispatcher_zombie_sweep.py` —incluida
la de la consulta del runner— y dos en `tests/test_recovery.py`, una para la
tarjeta vieja sin `task_id` y otra que vigila que el arreglo no se lleve por
delante el arranque normal, donde entre reclamar y enviar hay un hueco legítimo
de segundos.

Se deshizo cada arreglo por separado para ver si las pruebas se ponían rojas.
Sin el barrido en `run_once`: tres rojas. Sin la caducidad fuera de la rama: una
roja. Sin el barrido con escritura en `work()`: una roja. Ninguna prueba pasa por
casualidad.

Puertas: `pytest` 521 en verde; `ruff check` y `mypy --strict` sin nada nuevo en
lo tocado —el repositorio arrastra 47 avisos de ruff y 7 de mypy previos, en
ficheros y líneas que no son de este cambio—.

### 7.5 Versión y paquete

`pyproject.toml` y `src/agora/__init__.py`: **0.2.9 → 0.2.10**, y
`dist/agora-runner-0.2.10.zip` reconstruido.

Se comprobó que el paquete lleva el arreglo dentro y no solo el número nuevo:
instalado en un entorno limpio, `agora-ai-runner --version` dice `agora 0.2.10`
y el módulo trae `_yield_abandoned`, `sweep_abandoned` y el umbral de 30 min.

El guion de empaquetado limpia `dist/`, así que los artefactos de 0.2.9 ya no
están ahí; se reconstruyen desde git si hicieran falta.

## 8. Comprobado contra la tarjeta real

Sobre una **copia** de `C:\Agora\workspace\KANBAN`, con el código corregido:

```
en marcha antes : ['job-ca559a4b560b.md']
  job-ca559a4b560b.md: reclamada 2026-09-19T06:43:39Z attempts=0 agent=ai-1

umbral          : 0:30:00
RESCATE         : job-ca559a4b560b.md [recovered] zombie timeout:
                  returned to pending with attempt 1

en marcha ahora : []
  job-ca559a4b560b.md -> pending, attempts=1
        - dispatcher: Zombie reclaimed after exceeding 1800 seconds.
```

**Sobre una copia, y a propósito.** La tarjeta original sigue `in-progress` en el
tablero vivo: es la evidencia del hallazgo, y cancelarla o dejarla correr lo
decide una persona.

Esto prueba que el rescate funciona sobre el estado real que lo motivó. Lo que
**no** prueba es el escenario de §6.2 —matar el runner justo entre el `claim` y
el `submit`—, que sigue sin ejecutarse contra el sistema real, ni que el runner
del PC IA lleve el arreglo: allí sigue Agora 0.2.2. Desplegarlo es lo que falta.


## 9. El escenario de §6, ejecutado

A10 §9 lo declaró **no probado contra el sistema real**. Ya está ejecutado, con
las salvedades de §9.4.

Montaje, todo en procesos de verdad y fuera del tablero vivo: una API de Agora
con el código corregido sirviendo por TLS en el puerto 8752, con CA propia; los
PERFILEs reales copiados de `C:\Agora\workspace\AGENTS`; el tablero en disco; y
el runner —`AiRunner`, `BrokerExecutor`, `AgoraApiClient`: las mismas clases que
corren en el PC IA— como proceso aparte, muerto con `taskkill /F`, que no le da
ocasión de limpiar nada.

### 9.1 La tarjeta abandonada salió idéntica a la real

Muerto el runner al segundo de reclamar, la tarjeta quedó así:

```
estado    : in-progress, attempts=0
task_id   : None
bitácora  :
    - agora-api: CARD accepted from authenticated client provisional-client.
    - ai-1: Claimed CARD atomically and began admission.
    - ai-1: Remote profile selected: task-intake@1.2.0.
```

Las mismas tres líneas que `job-ca559a4b560b.md` en §1, y nada más. La
reproducción es fiel, no parecida.

### 9.2 El rescate llegó por el camino real

No se llamó a nada a mano: se hizo una consulta `GET /api/v1/work` contra la API
—lo que hace el runner en cada vuelta— y esa consulta barrió:

```
la API ofrece: ['e2e-huerfana.md']
estado   : pending
attempts : 1
    - dispatcher: Zombie reclaimed after exceeding 1800 seconds.
```

### 9.3 A los tres intentos, a manos de una persona

Repetido el abandono dos veces más:

```
vuelta 2 -> pending, attempts=2   (se sigue ofreciendo)
vuelta 3 -> blocked, attempts=3   (la API ya no la ofrece)
blocked: code=attempts_exhausted
```

No hay bucle: el rescate se agota y la tarjeta espera a que alguien decida. Esa
era la condición de §4 —nunca reintentar sin gastar intento— y se cumple.

### 9.4 Lo que este montaje **no** prueba

Tres cosas, y conviene que estén escritas:

1. **El broker real no participó.** `BrokerClient` rechaza por diseño cualquier
   host que no sea loopback —el token de administración no sale de la máquina—,
   así que desde el PC del tablero no se puede apuntar al broker del PC IA. En su
   lugar hubo un hueco en loopback que solo responde `/health/live` y
   `/api/v1/capabilities`, con **las respuestas reales** que dio el broker de
   `192.168.1.52` ese día —contrato 2.10, comprobado en vivo—, y que nunca
   contesta a `POST /api/v1/tasks`. Para este escenario basta, porque la tarjeta
   muere antes de que el broker haga nada; pero la prueba con el broker de verdad
   sigue siendo cosa del PC IA.
2. **La muerte ocurrió dentro del `submit`, no antes.** El runner llegó a lanzar
   la petición y se quedó colgado ahí. Es uno de los casos de §2.2 y el peor de
   todos: no hay `task_id` apuntado, así que no queda nada que cancelar. Es la
   variante que más cuesta rescatar, y es la que se rescató.
3. **El reloj se adelantó.** En vez de esperar media hora tres veces, se envejeció
   el campo `claimed`. Lo que se prueba aquí no es que el reloj avance.
