# A04 — Skill de análisis de revisión

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A04_skill_de_analisis_de_revision.md`.

## 1. Resumen

Una skill, `analyze-review`, que lee el texto de una revisión y devuelve
**hechos citados, deducciones separadas, causas, trabajo descubierto y
dependencias propuestas** en un documento `review-analysis` versión 1.

La TAREA del prompt pedía tres cosas, y las tres están cumplidas por el
contrato, no sólo por el procedimiento:

| Lo que pedía el prompt | Cómo se cumple |
|---|---|
| No marcar tareas como completadas por inferencia | `kind: completed` sólo existe dentro de `facts`, y todo hecho exige `evidence` (cita literal). Ninguna otra sección tiene campo `kind` |
| Separar hechos del usuario de inferencias | Son dos listas distintas; cada `inference` cita por índice los `facts` que la sostienen, y opcionalmente los que la contradicen |
| Salida estructurada | `output_schema` + `output_example`, validado con `enforce()` antes de escribir el artefacto |

Verificada **contra el broker real** con dos modelos distintos y **de punta a
punta** —tarjeta → runner → broker → artefacto— con una revisión construida a
propósito con trampas. Ver §5 y §6.

## 2. Archivos

Creados:

- `profiles/task-planning/review-analyzer/skills/analyze-review/SKILL.md`
- `tests/test_review_analysis_skill.py` — 35 pruebas.
- `scripts/verify_a04_review_analysis.py` — verificación contra el broker real.

Modificados:

- `profiles/task-planning/review-analyzer/PROFILE.md` — 1.0.0 → **1.2.0**:
  declara la skill, promete no cerrar trabajo por deducción, entrega el
  análisis aunque le pidan replanificar, y sube a `model_capacity: maximum`.

Ningún cambio en `src/`: la maquinaria de contratos que hizo falta construir en
A03 (`agora/output_contract.py`, el ejemplo relleno al final del prompt) sirve
tal cual. Es la primera fase que se apoya en ella sin tocarla.

## 3. Decisiones de diseño

### El contrato es lo que impide el error caro

Cerrar una tarea que en realidad sigue abierta es el fallo más caro de esta
capacidad: el usuario pierde trabajo de vista. Confiar en que el modelo lea la
instrucción «no infieras completado» no basta.

Por eso la garantía es **estructural**:

- `completed` es un valor de `kind`, y `kind` sólo existe en `facts`;
- todo `fact` exige `evidence` con `minLength: 1` — sin cita no hay hecho;
- `additionalProperties: false` en la raíz y en cada objeto, así que el modelo
  no puede inventarse un `completed: true` propio en otra sección;
- `requires_confirmation` es `{const: true}` en `inferences`,
  `discovered_work` y `proposed_dependencies`: una propuesta que se
  autoconfirma **no valida**, y la tarjeta falla.

Un modelo que quisiera cerrar algo por deducción no tiene dónde escribirlo.

### El silencio no es un hecho

Si la tarjeta trae el trabajo previsto y el usuario no menciona una de esas
tareas, el silencio no es «hecho» ni «no hecho». Convertirlo en cualquiera de
las dos cosas sería inventarlo.

Pero callarlo tampoco sirve. La skill lo manda a `warnings`: estaba previsto,
la revisión no lo menciona, no se puede saber. Esto no estaba en el diseño
inicial —lo hizo `qwen3.8:27b` por su cuenta en la verificación, mejor de lo
que estaba escrito, y se incorporó al procedimiento.

### `warnings`, para no dejar al usuario esperando

La revisión de prueba termina con «dime también cómo reorganizo la semana y a
qué horas». Este perfil no agenda. La primera versión del contrato no tenía
dónde decirlo, así que el modelo simplemente ignoraba esa parte de la petición
en silencio.

Se añadió `warnings`. Es la misma lección de A03: prohibir agendar sin dar
salida hace que el modelo **rechace la tarjeta entera**. La regla que funciona
es «entrega lo que sí puedes, y deja constancia de lo que no».

### El calendario no cabe en el contrato

No hay ningún campo con fecha, día, hora ni franja, y hay una prueba que lo
comprueba recorriendo el esquema entero. `discovered_work` estima **esfuerzo en
minutos**, nunca cuándo. La frontera de A01 se mantiene: Agora analiza, el
planificador del cliente agenda.

### `model_capacity: maximum`, y por qué

Con `standard` el broker enruta libremente. En la primera ejecución real cayó
en `ollama/gemma4:12b`, que **devolvió su propio prompt** (71 % de la salida era
copia literal de la entrada). El broker lo marca `PROMPT_ECHOED` con
`retryable: false`: la tarjeta muere.

Un análisis que tiene que citar al usuario, indexar hechos y no inventar nada
necesita un modelo capaz de seguir un contrato. `maximum` ya existía en
`models.yml` y fija `ollama/local/qwen3.8:27b` con `determinism: strict` y
`data_classification: confidential` — apropiado, además, porque una revisión
semanal es contenido personal. No hizo falta añadir ninguna capacidad nueva.

## 4. Pruebas añadidas

`tests/test_review_analysis_skill.py`, 35 pruebas:

- **Perfil** (4): declara la skill; promete no cerrar por deducción; sigue
  rechazando agendar; entrega el análisis en vez de rechazar la tarjeta.
- **Contrato** (5): versión declarada; el ejemplo cumple su propio esquema; los
  `based_on` del ejemplo apuntan a hechos que existen; todo hecho cita; el
  esquema no conoce el calendario.
- **Nada se cierra por deducción** (14): sólo `facts` lleva `kind`; todas las
  secciones cerradas a campos extra; las tres secciones de propuestas exigen
  `requires_confirmation` y lo rechazan en `false`; hecho sin cita, cita vacía,
  estado inventado, deducción con `kind`, deducción sin apoyo, y una fecha
  colada en `discovered_work` — todos rechazados.
- **Casos válidos** (4): esfuerzo desconocido; revisión sin nada nuevo (listas
  vacías); JSON envuelto en prosa; contrato equivocado rechazado.
- **Avisos** (4): el ejemplo dice en voz alta que no agenda; `warnings` es
  opcional; un aviso vacío no es un aviso; el silencio se avisa sin
  convertirse en hecho.
- **Modelo** (2): el perfil exige `maximum`; esa capacidad fija modelo, es
  estricta y trata la revisión como confidencial.

Suite completa de Agora: **252 pruebas, todas en verde** (eran 217 al cerrar
A03).

## 5. Resultados reales

Revisión de prueba con cuatro trampas deliberadas:

1. el **capítulo 2**, del que el usuario habla en pasado y con detalle («estuve
   el martes... me llevó toda la tarde») **sin decir que lo terminara**;
2. el **capítulo 1**, que está en el trabajo previsto y del que el usuario **no
   dice absolutamente nada**;
3. el **índice**, que el usuario sí da explícitamente por cerrado;
4. la petición final de que le **reorganicen la semana y le den horas**.

### Ejecución 1 — enrutado libre (`standard`)

`ollama/gemma4:12b`. Falló: `PROMPT_ECHOED`, no reintentable. Es lo que motivó
subir la capacidad del perfil.

### Ejecución 2 — `lmstudio/local/openai/gpt-oss-20b`

Contrato cumplido. Capítulo 2 → `observation`, **no** `completed`: la trampa
principal, superada. Índice → `completed` con la cita. Capítulo 3 → `not_done`
y `blocked` como hechos separados. Gestor de bibliografía → `discovered_work`
con `requires_confirmation: true` y 30 minutos estimados. Dependencia propuesta
capítulo 3 → borrador del director. Del capítulo 1, ni una palabra inventada.

Esta ejecución destapó el hueco de `warnings`: la petición de reorganizar la
semana se ignoraba en silencio.

### Ejecución 3 — `ollama/local/qwen3.8:27b` (la que fija `maximum`)

Contrato cumplido, y mejor. Seis hechos, uno solo `completed` (el índice, con
su cita). Dos dependencias propuestas. Dos avisos:

> El usuario pide además que se le reorganice la semana que viene y a qué horas
> ponerse con cada cosa. Este perfil no agenda ni replanifica; la planificación
> del tiempo y la elección de días u horas la hace el planificador del cliente,
> que conoce su disponibilidad.

> «Revisar las correcciones del capítulo 1» estaba en la lista de previsto, pero
> no se menciona en la revisión. No se puede determinar si se hizo, no se hizo,
> o simplemente no se abordó. Se deja sin clasificar.

El segundo aviso es la trampa del silencio resuelta exactamente como debía.

Comprobaciones automáticas de esa ejecución: cierres por deducción **0**;
hechos inventados sobre lo que el usuario calló **0**; propuestas que se
autoconfirman **0**; prescripciones de agenda fuera de las citas **ninguna**.

### Un error mío, corregido

La primera versión del verificador daba `REVISAR` porque encontraba «martes» en
la salida. Estaba mal: el «martes» aparecía dentro de un hecho que **reformula
lo que el usuario contó del pasado**. Mencionar un día no es agendar; agendar es
prescribir cuándo hacer algo en el futuro. El detector ahora busca lenguaje
prescriptivo y excluye las citas literales. El fallo era de la comprobación, no
del modelo.

## 6. Punta a punta: **hecha**

Primer intento, con el perfil 1.0.0 todavía en el PC de IA: el runner reclamó la
tarjeta pero enrutó libremente (`gemma4:12b`, `Determinism policy: routed`) y
devolvió prosa con la forma vieja —«Observaciones / Hipótesis / Señales de
alerta»—, sin contrato. Esa ejecución no vale como evidencia: prueba justo lo
contrario, que el runner leía su copia antigua.

Tras copiar `PROFILE.md` y `skills/analyze-review/SKILL.md` a
`C:\Agora\AGENTSeview-analyzer\` del PC de IA —**sin reiniciar el
runner**, como estaba previsto—, la tarjeta `prueba-analisis-revision-2.md` se
completó al primer intento:

```
- 2026-09-08 16:58 UTC  ai-1: Claimed CARD atomically and began admission.
- 2026-09-08 16:58 UTC  ai-1: Remote profile selected: review-analyzer.
- 2026-09-08 16:58 UTC  ai-1: AI_Broker task submitted: task_f16ac3f6...
- 2026-09-08 17:01 UTC  ai-1: Effective model: ollama/local/qwen3.8:27b.
- 2026-09-08 17:01 UTC  ai-1: Determinism policy: strict.
- 2026-09-08 17:01 UTC  ai-1: Verified artifact: ...\prueba-analisis-revision-2inal.md
- 2026-09-08 17:01 UTC  ai-1: CARD closed.
```

`strict` y `qwen3.8:27b` confirman que `model_capacity: maximum` llega hasta el
runner y que la política se aplica de verdad.

El artefacto, validado con el mismo `enforce` que usa el ejecutor:

| Comprobación | Resultado |
|---|---|
| Contrato `review-analysis` v1 | cumplido |
| Hechos | 7, todos con cita literal |
| Dados por completados | **1**: el índice, lo único que el usuario cerró |
| Cierres por deducción (capítulo 2, bibliografía) | **0** |
| Hechos inventados sobre lo que el usuario calló | **0** |
| Propuestas que se autoconfirman | **0** |
| `based_on` que apuntan a hechos existentes | todos |
| Prescripciones de agenda fuera de las citas | ninguna |
| Rastros de fecha | ninguno |
| Avisos | 2 |

El capítulo 2 quedó como `partial`, no como `completed`: el usuario contó con
detalle lo que hizo el martes sin decir que lo terminara, y el modelo lo
clasificó como avance parcial. Es la trampa principal, superada.

Y los dos avisos:

> La revisión incluye la petición de reorganizar la semana siguiente y asignar
> horas a cada tarea. Este perfil no agenda ni replanifica; la planificación del
> tiempo y la elección de días u horas la hace el planificador del cliente, que
> es quien conoce la disponibilidad.

> «Revisar las correcciones del capítulo 1» estaba prevista en la semana, pero
> la revisión no la menciona. No se puede saber si se hizo o no; no se marca
> como completada ni como no hecha. Si se hizo, conviene que el usuario lo
> confirme.

## 7. Riesgos y deuda

- **Toda entrega de esta skill exige copiar el perfil al PC de IA.** El runner
  relee los perfiles del disco en cada tarjeta, así que no hace falta
  reiniciarlo; pero mientras la copia no esté, sigue ejecutando la versión
  antigua **sin avisar de nada**, y el resultado parece correcto. La primera
  ejecución de §6 es justo ese caso.
- **El resto de perfiles con contrato siguen en `standard`**: `task-intake`
  (A02) y `task-decomposer` (A03) pueden caer en un modelo que eche el prompt
  por la boca y morir sin reintento. Se verificaron con modelos capaces, así que
  su evidencia es válida, pero la fragilidad de enrutado es la misma. Conviene
  subirlos a `maximum` y repetir sus verificaciones.
- **`based_on` fuera de rango** no lo puede comprobar el esquema. Ninguna
  ejecución real lo ha producido, pero un índice inventado pasaría la
  validación. Si aparece, el sitio para comprobarlo es `enforce`.

## 8. Siguiente fase

A05, según el orden del prompt.
