# A06 — Contratos JSON estrictos y versionados

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A06_contratos_json_estrictos_y_versionados.md`.

## 1. Resumen

Los tres esquemas que pedía la fase —TaskBrief, TaskDecomposition y
ReviewAnalysis— **ya existían**, escritos dentro del front matter de sus SKILL
durante A02, A03 y A04. Lo que no existía era un sitio donde vivieran, una
forma de enumerarlos y una comprobación de que todos cumplen las mismas reglas.

A06 los saca a `CONTRACTS/`, uno por fichero, con nombre y versión; las SKILL
los **citan** en vez de copiarlos; y el registro comprueba las reglas de la casa
al cargarlos, una sola vez para todos.

Auditar el estado real antes de escribir nada destapó un defecto: las tres
skills especializadas de descomposición **no declaraban** el contrato que A03
documentaba que producían. Cumplían de rebote. Ahora lo declaran, y por primera
vez se han ejecutado contra un modelo real (§6), lo que cierra una deuda
anotada en A03.

## 2. Archivos

Creados:

- `src/agora/contracts.py` — `OutputContract`, `ContractRegistry`, las reglas de
  la casa.
- `profiles/task-planning/CONTRACTS/{task-brief,work-breakdown,review-analysis,plan-advice}.v1.yml`
- `tests/test_contract_registry.py` — 42 pruebas.
- `scripts/verify_a06_shared_contract.py` — verificación contra el broker real.

Modificados:

- `src/agora/skills.py` — una SKILL puede citar un contrato; el registro se
  descubre solo junto a los PROFILE.
- `src/agora/broker/request_builder.py` — el cierre del prompt lleva ahora los
  **vocabularios cerrados** (§3).
- Las siete SKILL con contrato — el esquema sale del front matter.
- `tests/test_adaptive_task_interview.py`, `tests/test_decomposition_skills.py`,
  `tests/test_output_contract.py` — leen del registro; dos pruebas que
  codificaban el defecto se invirtieron.
- `pyproject.toml`, `src/agora/__init__.py` — **0.2.1 → 0.2.2**.

## 3. Decisiones de diseño

### Un contrato es una promesa pública, y vive fuera

Mientras hubo un contrato por perfil, tenerlo en el front matter era razonable.
Dejó de serlo en cuanto cuatro skills prometieron el mismo documento: o se
duplicaba el esquema cuatro veces —cuatro sitios donde equivocarse— o solo lo
declaraba una y las otras mentían por omisión. Pasó lo segundo.

Ahora el esquema vive una vez en `CONTRACTS/<nombre>.v<N>.yml` y las skills lo
citan con `output_contract` + `output_contract_version`. La prueba de que no hay
dos formas es literal: las cuatro skills de descomposición comparten **el mismo
objeto** en memoria, no copias iguales.

### Las reglas de la casa, comprobadas al cargar

`_check_house_rules` rechaza un contrato que no las cumpla, con un mensaje que
dice qué pasa. Ninguna es un gusto; cada una evita un fallo que ya ocurrió:

| Regla | Qué evita |
|---|---|
| `additionalProperties: false` en la raíz **y en cada objeto anidado** | Que el modelo cuele un campo que nadie espera y el consumidor lo ignore en silencio |
| `contract` y `contract_version` fijados con `const` y presentes en `required` | Que quien recibe el documento tenga que adivinar qué está leyendo |
| Esos valores coinciden con los del fichero | Que el documento mienta sobre sí mismo |
| El ejemplo valida contra su propio esquema | Que el ejemplo enseñe a incumplir: el modelo lo copia |

### Una cita que no se resuelve es un error, no un contrato ausente

Si una SKILL cita un contrato que no existe, o no hay registro, se falla al
cargar. Callarse ahí significaría escribir el artefacto **sin comprobar nada**,
que es justo lo que el contrato existe para impedir. Es el mismo fallo del que
venimos: la promesa sin declarar de las skills especializadas.

### Compatibilidad hacia atrás, con pruebas

Una SKILL que traiga su `output_schema` dentro sigue funcionando igual, con
registro o sin él, y el registro no se lo pisa. Un despliegue anterior a A06 no
tiene `CONTRACTS/` y no le hace falta. Hay tres pruebas de esto.

### Los vocabularios cerrados: una regresión medida y corregida

Al sacar el esquema del front matter, el prompt dejó de llevarlo. Parecía una
mejora —A03 ya había medido que el esquema delante hace que el modelo devuelva
*el esquema*— y en parte lo era: el prompt de `review-analyzer` bajó a 9603
caracteres y quedó solo el ejemplo relleno al final.

Pero la primera verificación contra el broker falló:

```
CONTRATO INCUMPLIDO: facts[1].kind: 'in_progress' no esta entre
['completed', 'partial', 'not_done', 'blocked', 'observation']
```

Con el esquema delante el modelo escribía `partial`. Sin él, se inventó un
valor razonable que no existe. **Un ejemplo no puede enseñar un vocabulario
cerrado**: solo muestra uno de los valores.

Así que los `enum` viajan aparte, en una lista corta al final del prompt:

```
Estos campos solo admiten estos valores. No inventes otros, aunque el ejemplo
solo ensene uno:

- `facts[].kind`: completed | partial | not_done | blocked | observation
```

Es lo único del esquema que hace falta y que el ejemplo no puede mostrar. Un
contrato sin `enum` —`task-brief`— no recibe ninguna sección vacía.

## 4. Migraciones

Ninguna de datos. La migración es de ficheros: los esquemas salen de siete
SKILL y entran en cuatro contratos. Se hizo leyendo los esquemas ya cargados y
escribiéndolos tal cual, así que la forma no cambió ni un carácter — y la suite
entera, que compara esquemas campo a campo, lo confirma.

## 5. Pruebas añadidas

`tests/test_contract_registry.py`, 42 pruebas:

- **Catálogo** (4): qué produce Agora y en qué versión; cada contrato se
  explica; los tres que nombra la fase están; pedir una versión que no existe
  dice cuáles sí.
- **Reglas de la casa sobre los contratos reales** (3): raíz cerrada, el
  documento dice lo que es, el ejemplo válido.
- **Payloads válidos** (3): contrato bien formado; descripción opcional; dos
  versiones del mismo contrato conviviendo.
- **Payloads inválidos** (14): sin nombre, versión cero, versión de texto,
  esquema vacío, ejemplo vacío, campo inventado, raíz abierta, objeto anidado
  abierto, contrato que miente sobre su nombre, sobre su versión, sin versión
  dentro, versión sin `const`, ejemplo que rompe su esquema, contrato duplicado,
  YAML roto, directorio vacío.
- **Citas** (6): una cita recibe esquema y ejemplo; suelta queda sin resolver;
  sin registro es error; citar lo inexistente es error; una skill puede poner su
  propio ejemplo de un contrato compartido; ninguna SKILL guarda ya esquema
  propio.
- **Prompt** (5): el esquema ya no viaja; los vocabularios sí; todos los `enum`
  llegan; un contrato sin `enum` no añade sección; el ejemplo sigue cerrando.
- **Compatibilidad** (3): esquema dentro sigue valiendo, no se pisa, y un perfil
  sin contratos carga.
- **Modelo** (1): los cuatro perfiles con contrato exigen un modelo fijado y
  determinismo estricto (§7).

Ademas se reescribieron dos pruebas que fijaban versiones exactas de PROFILE:
clavar un numero obliga a tocarlas en cada subida, que es lo contrario de lo que
protegen. Ahora comprueban que los semver son validos, que los perfiles **no van
todos al mismo paso** y que declarar una skill saca al perfil de su 1.0.0.

Suite completa: **338 pruebas, todas en verde** (eran 292 al cerrar A05).

## 6. Resultados reales

Cuatro ejecuciones contra el broker con `ollama/local/qwen3.8:27b`.

### La regresión, y su arreglo

| Ejecución | Resultado |
|---|---|
| `review-analysis`, sin esquema y sin vocabularios | **INCUMPLIDO**: `kind: 'in_progress'` |
| `review-analysis`, con vocabularios | **CORRECTO** |

Tras el arreglo, el mismo resultado que en A04: 6 hechos, **1** dado por
completado (el índice), 0 cierres por deducción, 0 hechos inventados sobre lo
que el usuario calló, 0 propuestas que se autoconfirman, ningún rastro de
agenda. Y tres avisos, uno de ellos nuevo y correcto: *«La muestra es de una
sola semana. No se declara tendencia a partir de un único ciclo»*.

`plan-advice` también correcto tras el cambio: `looks_ambitious` con la base
citada, dos `looks_short` sin cifras propias, dos problemas de coherencia,
ninguna prescripción de agenda, ninguna aritmética de huecos.

### La deuda de A03, cerrada

Las skills `decompose-course` y `decompose-study` nunca se habían ejercido
contra un modelo real: las dos ejecuciones de A03 cayeron en `software` y
`generic`. Con dos encargos escritos para ellas:

| | `curso` | `estudio` |
|---|---|---|
| Contrato | `work-breakdown@1` | `work-breakdown@1` |
| `kind` | **`course`** | **`study`** |
| Pasos | 4 | 4 |
| Sin criterio de terminado | 0 | 0 |
| Sin estimar | 0 | 0 |
| Dependencias rotas | 0 | 0 |
| Total vs suma | 585 = 585 | 495 = 495 |
| Fechas u horas coladas | ninguna | ninguna |

`additionalProperties` aparece **0 veces** en el prompt, y los vocabularios
cerrados sí. El encargo del curso pedía además repartir las sesiones por
semanas, y el aviso lo devolvió donde toca:

> La petición incluye repartir las sesiones por semanas; eso corresponde al
> planificador del cliente, que conoce la disponibilidad real de quien imparte
> el curso.

## 7. Punta a punta: **hecha**, y lo que costo llegar

Con Agora 0.2.2 y `AGENTS\CONTRACTS\` en el PC de IA, la tarjeta
`prueba-curso-contrato.md` demostro **dos cosas a la vez**.

### Lo que funciono: el contrato rechazo un entregable corrupto

Primer intento, `ollama/gemma4:12b`. El broker lo dio por **completado**; la
validacion de Agora, no:

```
ai-1: Remote runner yielded: el entregable no cumple el contrato de salida
declarado: steps[2]: falta el campo requerido 'completion_criteria';
steps[2]: campos no declarados: completion_ Од_criteria;
steps[4]: campos no declarados: completion__criteria
```

El modelo corrompio los nombres de campo a mitad de generacion, con caracteres
cirilicos colados dentro. **Sin contrato, ese documento se habria escrito como
artefacto valido** y el cliente habria recibido pasos sin criterio de terminado.

De paso, ese mensaje es la prueba de que el despliegue funciono: para validar,
el runner tuvo que resolver la cita de la SKILL contra `CONTRACTS/`. Es decir,
el registro de A06 estaba vivo del otro lado.

### Lo que no funciono: `model_capacity: standard`

La tarjeta agoto sus tres intentos y quedo **bloqueada**:

| Intento | Modelo | Resultado |
|---|---|---|
| 1 | `ollama/gemma4:12b` | Nombres de campo corruptos; rechazado por el contrato |
| 2 | `nvidia/api/meta/llama-3.2-90b-vision-instruct` | `PROVIDER_UNAVAILABLE` |
| 3 | `ollama/gemma4:12b` | `PROMPT_ECHOED`, **no reintentable** → bloqueada |

Tres intentos, ningun resultado. Era exactamente la deuda anotada en A04 §7 y
repetida en A05 §8 y en la primera version de este documento: `task-intake` y
`task-decomposer` seguian en `standard`, con enrutado libre.

**Dejo de ser deuda teorica y se arreglo:** `task-decomposer` 1.2.0 → **1.3.0**
y `task-intake` 1.1.0 → **1.2.0**, los dos a `model_capacity: maximum`. Ahora
los cuatro perfiles con contrato fijan `qwen3.8:27b` con `determinism: strict`,
y hay una prueba que lo comprueba recorriendo los perfiles.

`model_capacity` vive en el PROFILE, y el runner relee los perfiles en cada
tarjeta: basta con copiar los dos `PROFILE.md`, sin reinstalar nada y sin
reiniciar el runner.

### Relanzada con la capacidad corregida: **cerrado**

`prueba-curso-contrato-2.md`, mismo encargo, al primer intento:

```
- 2026-09-08 20:48 UTC  ai-1: Remote profile selected: task-decomposer.
- 2026-09-08 20:51 UTC  ai-1: Effective model: ollama/local/qwen3.8:27b.
- 2026-09-08 20:51 UTC  ai-1: Determinism policy: strict.
- 2026-09-08 20:51 UTC  ai-1: Verified artifact: ...\prueba-curso-contrato-2inal.md
- 2026-09-08 20:51 UTC  ai-1: CARD closed.
```

El artefacto, validado con el mismo `enforce` del ejecutor:

| Comprobacion | Resultado |
|---|---|
| Contrato | `work-breakdown@1` |
| `kind` | **`course`** — el procedimiento especializado, por la via del tablero |
| Pasos | 4, con dependencias encadenadas 1 → 2 → 3 → 4 |
| Sin criterio de terminado | **0** |
| Dependencias a pasos inexistentes | **0** |
| Total vs suma | **660 = 660** |
| Fechas u horas coladas | **ninguna** |
| Nombres de campo corruptos | **ninguno** |

Esa ultima fila es la que cierra el circulo: es exactamente lo que `gemma4:12b`
habia roto en la tarjeta bloqueada. Y el aviso devolvio la peticion de horario
donde corresponde:

> La peticion incluye asignar semanas y horas a cada sesion. Repartir el trabajo
> en dias y horas corresponde al planificador del cliente, que conoce la
> disponibilidad real de quien imparte el curso.

La tarjeta bloqueada queda en `KANBAN/archive/` como evidencia de los tres
fallos.

## 8. Riesgos y deuda

- **El tablero de este PC sigue en 0.2.0.** Para el trabajo remoto no importa:
  empareja con `load_profiles`, que no lee SKILL. Pero si alguna vez despacha
  **localmente** una tarjeta de estos perfiles, su `DeterministicHarness` sí
  carga skills, y con código 0.2.0 una SKILL en forma de cita no carga. Por eso
  el `AGENTS\` del tablero **no se ha tocado**. Conviene actualizarlo a 0.2.2 en
  la próxima parada.
- **Los vocabularios cerrados son la única parte del esquema que viaja.** Si un
  contrato futuro depende de otra restricción que el ejemplo no muestre —un
  `pattern`, un rango— volverá a pasar lo mismo. El sitio para arreglarlo es
  `_closed_vocabularies`.
- **Los cuatro perfiles con contrato exigen ya `maximum`** (§7). Lo que queda
  sin decidir es si `maximum` es el nombre correcto para lo que se necesita:
  ahi dentro van juntas tres cosas —modelo capaz, reproducibilidad estricta y
  clasificacion confidencial— y solo la primera es la que hacia falta. Si
  alguna vez hace falta un modelo capaz **sin** determinismo estricto, el sitio
  es una capacidad nueva en `models.yml`.
- **Un fallo `PROMPT_ECHOED` no es reintentable y consume intento.** Con la
  capacidad corregida no deberia volver a ocurrir, pero una tarjeta que agota
  intentos queda bloqueada con el motivo escrito, que es el comportamiento
  correcto.
- **El catálogo no se publica por el API.** `ContractRegistry.catalogue()`
  existe y está probado, pero nadie lo expone todavía. Es material de A07.

## 9. Siguiente fase

A07 — contrato de integración cliente/Agora. El catálogo de contratos y el
vocabulario de `handles` (A05 §7) son sus dos entradas naturales.
