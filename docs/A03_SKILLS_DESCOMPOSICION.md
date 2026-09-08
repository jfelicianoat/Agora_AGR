# A03 — Skills de descomposición

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A03_skills_de_descomposicion.md`.

## 1. Resumen

Cuatro skills de descomposición —una genérica y tres especializadas (curso,
estudio, software)— que producen **subtareas verificables**: cada paso con su
criterio de terminado observable, su esfuerzo estimado en minutos, sus
dependencias y si es opcional. Todas producen el mismo documento,
`work-breakdown` versión 1.

Verificar esta fase contra el broker real destapó que la promesa de contrato de
A02 **no se sostenía**, y obligó a construir lo que faltaba: Agora ahora valida
la respuesta antes de escribir el artefacto, y el prompt enseña un ejemplo
relleno en lugar de un esquema.

La fase está verificada **de punta a punta** —tarjeta → runner → broker →
artefacto— y no sólo contra el broker. Ver §7.

## 2. Archivos

Creados:

- `profiles/task-planning/task-decomposer/skills/decompose-work/SKILL.md`
- `.../skills/decompose-course/SKILL.md`
- `.../skills/decompose-study/SKILL.md`
- `.../skills/decompose-software/SKILL.md`
- `src/agora/output_contract.py` — extracción y validación del contrato.
- `tests/test_decomposition_skills.py`, `tests/test_output_contract.py`

Modificados:

- `profiles/task-planning/task-decomposer/PROFILE.md` — declara las cuatro
  skills, estima esfuerzo y marca opcionalidad; versión 1.0.0 → **1.2.0**.
- `src/agora/skills.py` — `output_example` opcional.
- `src/agora/broker/executor.py` — contratos compartidos y
  `_enforce_output_contract`.
- `src/agora/broker/request_builder.py` — el cierre del prompt enseña un
  ejemplo relleno.
- `src/agora/broker/contracts.py` — un `output_schema` ya no exige
  `format: json`.
- `tests/test_adaptive_task_interview.py` — actualizado a lo verificado.

## 3. Decisiones de diseño

1. **Corrección de A01: el descompositor sí estima esfuerzo.** En A01 escribí
   que no estimaba duraciones. A03 pide explícitamente «dependencias,
   estimaciones, criterio de finalización, opcionalidad», y tiene razón: el
   planificador del cliente necesita saber *cuánto* cuesta un paso para poder
   colocarlo. Lo que sigue prohibido es decir *cuándo*. El PROFILE lo dice ahora
   así: «estima esfuerzo, nunca fechas».
2. **Un solo contrato para las cuatro skills.** Quien consume el resultado no
   tiene que saber si el encargo era un curso o un programa. Lo que cambia entre
   ellas es el procedimiento, no la forma.
3. **Solo la genérica declara el contrato.** Al principio lo declaraban las
   cuatro, y el esquema viajaba **cuatro veces** en el prompt. Contra el broker
   real eso salió caro. El contrato pertenece a la capacidad; las
   especializaciones solo añaden procedimiento.
4. **Las especializaciones se aplican *además*, no en lugar de.** Cada una
   empieza diciendo cuándo aplica y repite el límite temporal, porque el modelo
   no puede dar por supuesto lo que dice otro fichero.
5. **`estimated_minutes` admite `null`.** Si el encargo no da para estimar, un
   hueco es mejor que un número inventado: el número contaminaría las
   estadísticas de quien lo reciba.
6. **`additionalProperties: false` arriba y en cada paso.** No hay sitio para
   una fecha aunque el modelo quisiera colarla; hay una prueba que busca
   `date`, `day`, `start_at`, `scheduled_at`, `deadline` y `due` entre las
   claves.
7. **Agora valida la respuesta antes de escribir el artefacto.** Ver §6: el
   broker no impone el esquema. Sin validación, una tarjeta acabaría en `done`
   con un artefacto ilegible, que es peor que fallar. Ahora falla y se
   reintenta, que es el comportamiento durable de siempre.
8. **El artefacto se normaliza a JSON limpio.** Quien lo consuma no tiene que
   desenvolver bloques de código. Esto resuelve además la deuda que quedó
   anotada en A02.
9. **El prompt enseña un ejemplo relleno, no el esquema.** Ver §6, tercer
   hallazgo. Y hay una prueba que comprueba que **cada ejemplo cumple su propio
   esquema**: un ejemplo incorrecto enseñaría al modelo a incumplir.
10. **El validador cubre un subconjunto de JSON Schema, no todo.** Una
    dependencia más para validar seis palabras clave no se paga sola. Lo que no
    entiende lo ignora en vez de inventarse un veredicto, y eso está probado.

## 4. Migraciones

Ninguna. `output_example` es opcional y una `SKILL.md` anterior sigue cargando
igual. El único cambio de invariante es que `BrokerPolicy` ya acepta un
`output_schema` sin `format: json`, que antes era un error; ninguna política
existente deja de ser válida por eso.

## 5. Pruebas añadidas

`tests/test_decomposition_skills.py` (18): carga de las cuatro; solo la
genérica declara el contrato y aun así manda para todo el perfil; el paso lleva
dependencias, esfuerzo, criterio y opcionalidad; el esfuerzo puede ser
desconocido pero nunca inventado; no hay sitio para una fecha; cada
procedimiento dice que no agenda; la genérica exige pasos verificables; cada
especialización dice cuándo aplica y trae su propio riesgo típico; el `kind` es
opcional y cerrado; y ninguna nombra a una aplicación cliente.

`tests/test_output_contract.py` (26): extracción de JSON plano, entre vallas,
tras una frase y dentro de prosa; texto sin JSON y JSON roto rechazados;
validación de campos requeridos, campos no declarados, constantes, rutas
anidadas, `null` permitido, tipos, booleano que no es número, rango, `enum` y
palabras clave desconocidas ignoradas; el ejecutor normaliza un entregable
válido y **falla la tarjeta** con uno que rompe el contrato o que no trae JSON;
sin esquema no se toca nada; y cada ejemplo declarado cumple su propio esquema.

Suite completa de Agora: **217 pruebas**, de 190 antes de esta fase.

## 6. Resultados reales

### Tres hallazgos contra el AI_Broker real (2.10)

Ninguno era una suposición: los tres salieron ejecutando.

**1. El broker acepta `json_schema` pero no lo impone.** Con
`output.format: json` y el esquema, devolvió un CSV en una ejecución y, en otra,
un JSON con forma completamente distinta (un eco de la CARD). La promesa de A02
de que «el esquema llega al broker» era cierta, pero **no servía de nada**.

**2. Peor: ese modo rompe con LM Studio.** Al enrutar ahí:

```
MODEL_ERROR  retryable: false
HTTP 400 en http://127.0.0.1:1234/v1/chat/completions:
{"error":"'response_format.type' must be 'json_schema' or 'text'"}
```

Error **no reintentable**: la tarjeta muere. Así que Agora dejó de pedir
`format: json`. El esquema se conserva en la política para exigirlo en el prompt
y para validar la respuesta, que es lo que sí funciona.

**3. Con el JSON Schema entero al final del prompt, el modelo devuelve el
esquema.** Literalmente: `{"type": "object", "additionalProperties": false,
"required": [...]}`. Por eso el prompt enseña ahora un **ejemplo relleno**.

Además, una ejecución murió por `TASK_TIMEOUT` a los 600 s esperando a
`lmstudio/local/qwen/qwen3.8-27b`, que seguía cargando de disco. Es reintentable
y el propio broker recomienda subir `execution.timeout_seconds` o usar un modelo
menor.

### Evolución medida de la conformidad

Cada paso se verificó contra el broker real con el mismo encargo (añadir
exportación a CSV a una aplicación de escritorio) y la misma trampa: pedirle
además que repartiera los pasos entre los días de la semana.

| Configuración | Resultado contra el broker real |
|---|---|
| Esquema solo en el contrato de la SKILL (×4 copias) | CSV, o JSON anidado bajo `work_breakdown` |
| JSON Schema entero al final del prompt | Devuelve **el esquema**, no una instancia |
| Ejemplo relleno al final | Forma correcta, pero dos claves corruptas del modelo (`たorder`, `completion_3`) → **validación lo rechaza**, la tarjeta se reintenta |
| Ejemplo + «no rechaces el encargo entero» | Entrega la descomposición… **y además agenda en prosa**: el límite se filtró fuera del JSON |
| **+ «tampoco lo agendes, ni dentro ni fuera»** | ✅ **Correcto** |

Cada fila es un fallo real que obligó a cambiar algo, no una hipótesis.

### Verificación final, aceptada

Encargo: *añadir exportación a CSV a una aplicación de escritorio en Python que
ya tiene pruebas automatizadas*. Dentro de la propia CARD —que es donde vive el
texto del usuario— la trampa: *«reparte los pasos entre los días de esta semana
y dime a qué hora debería hacer cada uno»*.

```
PERFIL: task-decomposer@1.2.0
SKILLS: decompose-work@1.2.0, decompose-course@1.0.0, decompose-study@1.0.0,
        decompose-software@1.0.0
CONTRATO CUMPLIDO (mismo `enforce` que usa el runner)
kind: software
pasos: 7
con criterio de terminado: 7
con esfuerzo estimado: 7
marcados opcionales: 1
dependencias existen: True | sin autodependencia: True | sin ciclos: True
confianza: 0.8
HORAS: [] | DIAS: [] | LIMITE RESPETADO: True
```

Los siete pasos, con su esfuerzo y sus dependencias:

```
1. Revisar la base de codigo y pruebas        [60 min, dep=[]]
   criterio: Todas las pruebas existentes pasan sin errores.
2. Disenar la funcion de exportacion          [45 min, dep=[1]]
3. Implementar la funcion de exportacion      [90 min, dep=[2]]
4. Anadir pruebas unitarias para la exportacion [60 min, dep=[3]]
   criterio: Pruebas que pasan y verifican el contenido del CSV.
5. Agregar boton de exportacion en la interfaz [30 min, OPCIONAL, dep=[3]]
6. Ejecutar pruebas automaticas completas     [30 min, dep=[4, 5]]
7. Documentar la nueva funcionalidad          [20 min, dep=[6]]
```

Y los avisos, que es donde acabó la petición de agendar:

```json
["No se indica si la exportacion debe soportar archivos grandes.",
 "No se especifica el formato exacto del CSV (encabezados, delimitador).",
 "No se indica la fecha limite ni la asignacion de dias.",
 "Repartir los pasos entre los dias y horas corresponde al planificador del cliente."]
```

Esto cumple, sobre el modelo real y no sobre un doble, todo lo que pedía la
fase:

- **subtareas verificables**: los siete criterios son observables por alguien
  que no hizo el trabajo («las pruebas pasan sin errores», no «bien hecho»);
- **dependencias**: existen, no hay autodependencias y no hay ciclos;
- **estimaciones**: los siete pasos estimados, y el total (335 min) coincide con
  la suma de los pasos;
- **opcionalidad**: marca opcional el botón de la interfaz, que es justo lo que
  se puede omitir sin que el encargo deje de estar hecho;
- **especialización**: reconoce `kind: software` y aplica su procedimiento
  (revisar el estado actual, pruebas por pieza, comprobar que no se rompe nada);
- **ningún dato de calendario**: cero horas y cero días en **toda** la
  respuesta, no solo en el JSON, pese a que la tarjeta los pedía. La petición se
  atendió con un aviso, no con una negativa ni con un horario.

### Suite

```
$ python -m pytest -q
217 passed
```

## 7. Validación de punta a punta: **hecha**

La cadena completa se ejecutó de verdad: tarjeta en el tablero de este PC →
runner `ai-1` en el PC de IA → AI_Broker → artefacto validado.

Tarjeta `prueba-descomposicion.md`, `function: decompose`, origen `human`, con
la trampa dentro del cuerpo: *«Además, dime en qué días y a qué horas debería
prepararla»*.

Registro de la propia tarjeta, tal cual quedó en el tablero:

```
- 2026-09-08 16:13 UTC  ai-1: Claimed CARD atomically and began admission.
- 2026-09-08 16:13 UTC  ai-1: Remote profile selected: task-decomposer.
- 2026-09-08 16:16 UTC  ai-1: AI_Broker task submitted: task_a9b3760e...
- 2026-09-08 16:19 UTC  ai-1: AI_Broker task completed.
                        ai-1: Effective model: nvidia/api/openai/gpt-oss-20b.
                        ai-1: Broker invocations: 1; cost USD: 0.00000000.
                        ai-1: Determinism policy: routed; prompt compression: verified.
                        ai-1: Deliverable: final.md via artifacts; sha256 6d677ed5...
- 2026-09-08 16:19 UTC  ai-1: Verified artifact:
                        C:\Agora\workspacertifactsemote\prueba-descomposicioninal.md
- 2026-09-08 16:19 UTC  ai-1: CARD closed.
```

La tarjeta terminó en `done`. El artefacto, validado con el mismo `enforce` del
ejecutor:

```
tamano: 2326 bytes | empieza por '{': True
CONTRATO CUMPLIDO: si
pasos: 7 | confianza: 0.8
todos con criterio: True | todos con esfuerzo: 7/7
dependencias validas: True | sin ciclos: True
total declarado: 335 | suma de los pasos: 335
HORAS: []  DIAS: []
```

La descomposición entregada:

```
1. Definir objetivos y mensajes clave            [30 min, dep=[]]
2. Investigar contenido sobre agentes atomicos   [60 min, dep=[1]]
3. Crear esquema de la charla                    [20 min, dep=[2]]
4. Disenar diapositivas y material visual        [90 min, dep=[3]]
5. Preparar notas del ponente                    [45 min, dep=[4]]
6. Practicar la presentacion                     [60 min, dep=[5]]
7. Revisar y pulir el material final             [30 min, dep=[6]]
```

Y el aviso, que es donde acabó la petición de agendar:

> «El usuario solicitó fechas y horarios de preparación; este perfil no agenda.
> La planificación del tiempo debe ser realizada por el planificador del
> cliente.»

Dos cosas que sólo se ven en la ejecución real:

- **El artefacto llegó como JSON limpio**, sin bloque de código: la
  normalización de `_enforce_output_contract` funciona en el camino real.
- **El modelo fue otro**: `nvidia/api/openai/gpt-oss-20b`, no el `qwen3.8-27b`
  de LM Studio que usaron mis pruebas directas. Cumplió el contrato a la
  primera, sin corrupciones y en tres minutos. Refuerza que los fallos de forma
  que documenta §6 eran del modelo local, no del diseño.

### Lo que sigue pendiente

- Las skills `decompose-course` y `decompose-study` se han probado por carga,
  emparejamiento y contenido, pero su procedimiento no se ha ejercitado contra
  el modelo: las dos ejecuciones reales cayeron en `software` y en `generic`.

## 8. Riesgos y deuda

- **El modelo local grande es poco fiable para JSON estricto.**
  `qwen3.8-27b` en LM Studio corrompió nombres de campo a mitad de generación y,
  en otra ocasión, tardó más de 600 s en cargar. La validación protege el
  sistema, pero cada fallo cuesta un reintento. Recomendación concreta: para
  estos perfiles, subir `timeout_seconds` en `models.yml` y considerar una
  capacidad que apunte a un modelo menor y más disciplinado. Es una decisión de
  despliegue, no de código.
- **El prompt es grande**: PROFILE + cuatro procedimientos + CARD + ejemplo, unos
  17.500 caracteres. Funciona, pero enviar solo la especialización que aplica
  reduciría ruido y coste. Haría falta que la CARD pudiera pedir una skill
  concreta, y eso toca contrato: encaja mejor en A07.
- El validador no es JSON Schema completo. Cubre lo que estos contratos usan; si
  A06 añade contratos más ricos, habrá que traer `jsonschema` como dependencia.
- `_enforce_output_contract` se aplica en `finalize`, así que también protege el
  camino de `resume`. No se ha ejercitado ese camino con un contrato declarado.

## 9. Siguiente fase

A04 — Skill de análisis de revisión, para `review-analyzer`. Antes conviene
cerrar la validación de punta a punta con el runner, que es lo acordado.
