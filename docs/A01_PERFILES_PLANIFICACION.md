# A01 — Perfiles especializados para gestión de tareas

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A01_perfiles_especializados_para_gestion_de_tareas.md`.

## 1. Resumen

Cuatro PROFILEs atómicos reutilizables —`task-intake`, `task-decomposer`,
`review-analyzer` y `planning-advisor`— con responsabilidades y límites
explícitos, y sin una sola línea de lógica de calendario.

El límite arquitectónico que exige el prompt («Agora ejecuta trabajo IA;
Gestión de Tareas es propietaria de tareas, calendario, disponibilidad y
scheduler») está escrito en tres sitios a la vez: en los `refuses` que usa el
emparejador, en el cuerpo del contrato que lee el modelo, y en pruebas que se
ponen rojas si alguien lo rompe.

## 2. Archivos

Creados:

- `profiles/task-planning/README.md`
- `profiles/task-planning/task-intake/PROFILE.md`
- `profiles/task-planning/task-decomposer/PROFILE.md`
- `profiles/task-planning/review-analyzer/PROFILE.md`
- `profiles/task-planning/planning-advisor/PROFILE.md`
- `tests/test_task_planning_profiles.py`

**Ningún archivo de código fue modificado.** La capacidad se añade con el
mecanismo que Agora ya tiene (PROFILE + SKILL + CARD), que es exactamente lo que
pedía el guardrail «no modifiques AI_Broker si el caso puede resolverse mediante
PROFILE/SKILL/CARD».

## 3. Decisiones de diseño

1. **Una función distinta por perfil** (`understand`, `decompose`, `analyze`,
   `advise`). El emparejador de Agora filtra primero por función, así que
   funciones distintas hacen imposible que una petición de descomponer acabe en
   el analizador aunque las palabras se parezcan. Hay una prueba de eso.
2. **`refuses` como barrera, no como comentario.** Los cuatro perfiles
   rechazan `schedule`; el asesor rechaza además `plan calendar`,
   `assign days`, `assign hours` y `estimate availability`. `match_card`
   descarta un perfil si el texto de la petición contiene un descriptor
   rechazado, así que una tarjeta que pida agendar **no encuentra perfil**, en
   vez de encontrar uno que improvise.
3. **El límite también va en el cuerpo del PROFILE**, porque el cuerpo es lo
   que acaba en el prompt: `build_broker_request` serializa el contrato entero.
   Si el límite solo estuviera en los metadatos, el modelo no lo vería.
4. **Ningún perfil declara skills todavía.** `load_profile_skills` falla si un
   PROFILE declara una skill cuyo `SKILL.md` no existe, así que un perfil con
   skills declaradas por adelantado sería **imposible de ejecutar**. Cada skill
   se declara en la fase que la crea (A02–A05) y ahí se sube la versión del
   perfil. Hay una prueba que fija esta regla.
5. **Vocabulario genérico.** Los contratos hablan de «encargos» y «trabajo», no
   de tareas de una aplicación concreta. Una prueba comprueba que ninguno
   menciona a Gestión de Tareas: la capacidad tiene que servir a cualquier
   cliente.
6. **`model_capacity: standard`** en los cuatro. Ninguno necesita determinismo
   estricto ni modelo exacto: son textos de apoyo revisables por un humano, no
   decisiones irreversibles. Subirlos a `maximum` costaría dinero sin ganar
   nada.
7. **Viven en `profiles/task-planning/`, no en `examples/`.** No son una demo:
   son capacidad que se copia al `AGENTS` de un tablero. El README explica cómo
   y dónde está la frontera.

## 4. Migraciones

Ninguna. Los PROFILEs son ficheros del tablero; no hay esquema que migrar ni
contrato público que versionar. No se tocó la API ni el runner, así que la
compatibilidad hacia atrás es total por construcción.

## 5. Pruebas añadidas

`tests/test_task_planning_profiles.py` (17):

- **Carga**: los cuatro cargan, cada uno con su función, todos versionados y
  con cuerpo; ninguno declara una skill que no tenga.
- **Límites**: todos rechazan `schedule`; el asesor rechaza los cinco
  descriptores temporales; ningún `handles` contiene vocabulario de calendario
  (se comprueba contra una lista: `calendar`, `workblock`, `vacation`,
  `availability`, `pause`, `deadline`, `timezone`…); el cuerpo de cada contrato
  dice explícitamente que no agenda y que quien planifica es el cliente; y una
  petición de agendar **no empareja con ninguno de los cuatro**.
- **Emparejamiento**: `check_overlaps` no encuentra ni un conflicto (cada
  descriptor resuelve a su propio dueño); cuatro peticiones realistas llegan a
  su perfil; y una petición con la función equivocada no empareja.
- **Reutilizables**: ningún contrato nombra a la aplicación cliente, y el
  README explica dónde está la frontera.

## 6. Resultados reales

### Pruebas automatizadas

```
$ python -m pytest tests/test_task_planning_profiles.py -q
17 passed

$ python -m pytest -q
155 passed
```

La suite completa de Agora pasa de 138 a 155 pruebas sin regresiones.

### Contra el AI_Broker real

El prompt permite doubles «cuando no haya Broker real». Lo hay, así que se probó
contra él: `http://192.168.1.52:8765`, contrato **2.10**, autenticando con
`X-Admin-Token`.

Se construyó la petición con el propio `build_broker_request` a partir del
PROFILE `planning-advisor` y una CARD real, y se le añadió una **pregunta
trampa**: además de pedir el orden, se le exigió expresamente *«asígnalos a días
y horas concretos de mi semana»*.

```
PERFILES CARGADOS: ['planning-advisor', 'review-analyzer', 'task-decomposer', 'task-intake']
CONTRATO DEL BROKER: 2.10
  status: 202
  task_id: task_6fe6191db8834c719368c682ccdfd864 | estado: queued
ESTADO FINAL: completed
respuesta guardada, longitud: 1718
HORAS CONCRETAS: []
DIAS DE LA SEMANA: []
LIMITE RESPETADO (ni horas ni dias): True
tiene orden recomendado: True
tiene riesgos: True
tiene preguntas: True
```

La comprobación no es a ojo: se buscó con expresión regular cualquier hora
(`\d{1,2}[:.]\d{2}`) y cualquier nombre de día de la semana en la respuesta
completa. **Cero de ambos.** El modelo entregó orden razonado, riesgos y
preguntas que cambiarían el consejo —incluida «¿cuánto tiempo estimas dedicar
cada día?», que devuelve la pregunta al usuario en vez de responderla— y no
agendó nada, pese a que se le pidió explícitamente.

Esto verifica los dos criterios de aceptación centrales: la capacidad funciona
contra el broker real, y no introduce lógica de planificación temporal.

## 7. Validaciones manuales pendientes

- **Ejecución de punta a punta por el runner.** La prueba anterior llama al
  broker directamente porque `BrokerClient` está restringido a loopback a
  propósito (el runner vive junto al broker, en el PC de IA). Para probar la
  cadena completa hay que copiar `profiles/task-planning/*` al `AGENTS` del
  tablero del PC de IA, dejar una CARD con `function: advise` en `pending` y
  arrancar `agora-ai-runner`. Evidencia esperada: la tarjeta pasa a
  `in-progress` y luego a `done` con el artefacto escrito en su destino.
  **Si quieres que lo pruebe así, dime y paro para que arranques el runner en
  el PC de IA.**
- Los otros tres perfiles se probaron por emparejamiento y carga, pero solo
  `planning-advisor` se ejecutó contra el modelo real. Los demás tienen su
  ejecución real en sus fases de skill (A02–A04), donde hay algo concreto que
  pedirles.

## 8. Riesgos y deuda

- **Los `refuses` dependen de las palabras de la petición.** `match_card` busca
  el descriptor como texto dentro del `request`. Una petición que pida agendar
  con otras palabras («ponme esto el jueves») no dispararía el rechazo por
  emparejamiento; la defensa entonces es el cuerpo del contrato, que sí lo dice,
  y así se comportó el modelo real. Es defensa en profundidad, no una única
  barrera.
- **Los perfiles están en inglés en los descriptores y en español en el cuerpo.**
  Los descriptores son la clave de emparejamiento y el resto de Agora los usa en
  inglés; el cuerpo es lo que lee el modelo y el usuario es hispanohablante.
  Funciona, pero conviene decidir un criterio único si aparecen más perfiles.
- Sin skills, el perfil se apoya solo en su contrato. Es suficiente —la prueba
  real lo demuestra— pero las respuestas serán más consistentes cuando A02–A05
  aporten el procedimiento.

## 9. Siguiente fase

A02 — Skill `adaptive-task-interview`, que es la que da a `task-intake` el
procedimiento para preguntar, y la que necesita F10 (entrevista adaptativa) del
lado de la aplicación.
