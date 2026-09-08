# A02 — Skill `adaptive-task-interview`

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A02_skill_adaptive_task_interview.md`.

## 1. Resumen

Una skill reutilizable que entrevista de forma adaptativa y devuelve un
**contrato JSON versionado** (`task-brief`, versión 1) con lo entendido, los
supuestos declarados y solo las preguntas que de verdad cambian el encargo.

La promesa no se queda en el texto del prompt: la skill declara su esquema y ese
esquema **llega al broker** como `output.format: json` + `json_schema`, de modo
que la forma se impone y no se confía.

## 2. Archivos

Creados:

- `profiles/task-planning/task-intake/skills/adaptive-task-interview/SKILL.md`
- `tests/test_adaptive_task_interview.py`

Modificados:

- `src/agora/skills.py` — `Skill` aprende a declarar contrato de salida
  (`output_contract`, `output_contract_version`, `output_schema`).
- `src/agora/broker/executor.py` — `apply_skill_output_contract`: si una skill
  declara contrato, la política pasa a JSON con su esquema.
- `profiles/task-planning/task-intake/PROFILE.md` — declara la skill; versión
  1.0.0 → **1.1.0**.
- `tests/test_task_planning_profiles.py` — la versión ya no es la misma para
  los cuatro perfiles, y ahora se carga cada skill declarada.

## 3. Decisiones de diseño

1. **El contrato de salida vive en la skill, no en el runner.** Quien sabe qué
   forma tiene la respuesta es quien define el procedimiento. Ponerlo en
   `models.yml` lo habría atado a la capacidad del modelo, que es otra cosa.
2. **Los tres campos van juntos o ninguno.** Un esquema sin nombre ni versión no
   se puede citar después; un nombre sin esquema no se puede comprobar. Declarar
   solo una parte es un error de formato, con prueba.
3. **El contrato se repite dentro del propio JSON** (`contract: task-brief`,
   `contract_version: 1`). Quien reciba el documento sabe qué está leyendo sin
   tener que deducirlo del sitio de donde vino. Es lo que hará posible que A12
   versione sin romper a nadie.
4. **`additionalProperties: false`.** El esquema tiene siete claves y ninguna es
   una fecha. Aunque el modelo quisiera colar un campo `schedule`, el contrato
   no se lo permite. Hay una prueba que fija exactamente ese conjunto de claves.
5. **Cada afirmación viene con su respaldo.** `understood` exige `evidence` (el
   fragmento de la petición que lo sostiene) y `assumptions` exige
   `impact_if_wrong`. Es lo que convierte «no inventar» en algo comprobable en
   lugar de una buena intención.
6. **Cada pregunta exige `why_it_matters`.** Si no sabes escribir qué cambia
   según la respuesta, la pregunta sobra. Es el mecanismo que impide la
   entrevista interminable, y está dicho así en el procedimiento.
7. **Cero preguntas es una respuesta válida.** Un encargo claro no necesita
   entrevista, y el procedimiento lo dice para que el modelo no rellene por
   compromiso.
8. **La adaptación por tipo de encargo son pistas, no ramas.** El procedimiento
   describe qué suele faltar en un encargo creativo, técnico, administrativo o
   de estudio, pero no fuerza preguntas: si la petición ya lo dice, no se
   pregunta. Así la skill no se acopla a ninguna taxonomía de una UI concreta.
9. **Dos skills con contrato son un error, no una elección silenciosa.** Si dos
   declararan forma de salida, no habría manera de saber cuál manda.
10. **Compatibilidad hacia atrás por construcción.** Una skill sin contrato deja
    la política **idéntica** —se devuelve el mismo objeto, y hay una prueba que
    comprueba la identidad, no solo la igualdad—. Ninguna skill existente
    cambia de comportamiento.

## 4. Migraciones

Ninguna. Los campos nuevos de `SKILL.md` son opcionales: un fichero anterior
sigue cargando igual. El único cambio de versión es el del PROFILE
`task-intake`, que sube a 1.1.0 porque su contrato cambió al declarar la skill.

## 5. Pruebas añadidas

`tests/test_adaptive_task_interview.py` (16):

- **Carga**: la skill carga, soporta modo `card`, y el perfil la declara y la
  resuelve.
- **Contrato**: nombre y versión; el contrato se fija dentro del payload;
  el esquema exige evidencia, impacto y motivo; el conjunto de claves no deja
  sitio a un calendario; el procedimiento dice qué no hace nunca.
- **La promesa llega al broker**: un contrato declarado fuerza `format: json`;
  una skill sin contrato **no cambia nada** (identidad del objeto); dos
  contratos son un error; y la petición construida lleva el `json_schema` y el
  procedimiento dentro del prompt.
- **Formato**: contrato a medias, versión 0 o no numérica, y esquema vacío se
  rechazan al cargar.
- **Genérica**: la skill no nombra a ninguna aplicación cliente.

## 6. Resultados reales

### Pruebas automatizadas

```
$ python -m pytest tests/test_adaptive_task_interview.py -q
16 passed

$ python -m pytest -q
171 passed
```

De 155 a 171 sin regresiones. Las pruebas de A01 siguen pasando tras cambiar la
versión del perfil.

### Contra el AI_Broker real

`http://192.168.1.52:8765`, contrato 2.10. Se pidió entrevistar una petición
deliberadamente vaga —«tengo que preparar la presentación del proyecto para el
comité»— y se añadió la **trampa** de pedirle además los días y las horas.

```
PERFIL: task-intake@1.1.0
SKILL: adaptive-task-interview@1.0.0
CONTRATO DE SALIDA: task-brief v1
POLITICA: formato=json, esquema=si
CONTRATO DEL BROKER: 2.10
POST /api/v1/tasks -> 202
ESTADO FINAL: completed

JSON VALIDO: si
PROBLEMAS DE ESQUEMA: ninguno
contract: task-brief v 1
understood: 2 | todos con evidencia: True
questions: 2 | todas con motivo: True
confidence: 0.7
HORAS CONCRETAS: []
DIAS DE LA SEMANA: []
LIMITE RESPETADO: True
```

El documento devuelto:

```json
{
  "contract": "task-brief",
  "contract_version": 1,
  "summary": "Preparar la presentación del proyecto para el comité.",
  "understood": [
    {"statement": "Preparar la presentación del proyecto",
     "evidence": "preparar la presentacion del proyecto"},
    {"statement": "Destinatario: comité", "evidence": "para el comite"}
  ],
  "assumptions": [
    {"statement": "El proyecto cuenta con información base suficiente para ser presentada.",
     "impact_if_wrong": "Se requeriría una fase previa de recopilación de información."}
  ],
  "questions": [
    {"question": "¿Cuál es el objetivo principal de la presentación?",
     "why_it_matters": "Determina el tono y el contenido de las diapositivas.",
     "options": ["Informativa", "Persuasiva", "Decisoria"]},
    {"question": "¿Cuál es el tiempo máximo y formato requerido?",
     "why_it_matters": "Determina la cantidad de diapositivas y el diseño visual.",
     "options": ["Presentación corta (10 min)", "Presentación larga (30 min)",
                 "Documento de apoyo"]}
  ],
  "confidence": 0.7
}
```

Lo que confirma, sobre el modelo real y no sobre un doble:

- **no inventa**: los dos hechos que afirma citan el fragmento exacto de la
  petición del que salen;
- **declara supuestos** en vez de convertirlos en hechos, con su impacto;
- **pregunta solo lo material**: dos preguntas, cada una con qué cambia según
  la respuesta, y con opciones para que responder sea elegir;
- **confianza honesta**: 0.7 con dos preguntas abiertas, no 0.95;
- **no agenda**: cero horas y cero días en todo el documento, pese a que se le
  pidió explícitamente. La comprobación es por expresión regular sobre el JSON
  completo, no a ojo.

## 7. Validaciones manuales pendientes

- **Cadena completa por el runner.** La prueba anterior llama al broker
  directamente porque `BrokerClient` está restringido a loopback a propósito.
  Para probarla entera hay que copiar `profiles/task-planning/task-intake` al
  `AGENTS` del tablero del PC de IA, dejar una CARD con `function: understand`
  en `pending` y arrancar `agora-ai-runner`. Evidencia esperada: la tarjeta pasa
  a `done` y el artefacto es el JSON `task-brief`.
  **Si quieres que lo pruebe así, dime y paro para que lo arranques.**
- La validación de esquema del script de verificación es mínima (campos
  requeridos, `const`, tipos y claves de los objetos anidados). Es suficiente
  para lo que declara la skill, pero no es un validador JSON Schema completo. Si
  A06 formaliza más contratos, conviene traer `jsonschema` como dependencia de
  desarrollo.

## 8. Riesgos y deuda

- **El broker acepta el `json_schema`, pero no está comprobado que lo imponga.**
  El modelo devolvió JSON válido, cosa que también podría explicarse por el
  procedimiento de la skill. Distinguir ambas cosas exigiría una prueba con un
  modelo que se salte el formato a propósito, y no la tengo.
- La respuesta llegó envuelta en un bloque de código Markdown; el script lo
  desenvuelve. Conviene mirar si al cliente le llega así, porque quien consuma
  el contrato tendrá que hacer lo mismo. Es candidato a resolverse en A06 o A11.
- `apply_skill_output_contract` se aplica en `execute`, no en `resume`. Al
  reanudar no se reconstruye la petición, así que no hace falta; queda anotado
  por si algún día `resume` reenvía.
- La skill está en español y sus descriptores en inglés, igual que en A01. Sigue
  pendiente unificar criterio.

## 9. Siguiente fase

A03 — Skills de descomposición, que dan a `task-decomposer` su procedimiento y
son las que necesita F11 del lado de la aplicación.
