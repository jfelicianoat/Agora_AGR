# A13 — Frontera explícita: Agora no es un calendario

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A13_frontera_explicita_agora_no_es_calendario.md`.

## 1. Resumen

La frontera se ha respetado en las doce fases anteriores, pero se respetaba
**escribiéndola otra vez en cada skill**. Eso funciona mientras alguien se
acuerde. A13 la escribe una sola vez y hace que el código la comprueba.

Tres entregas:

1. **`docs/ADR-001-FRONTERA-AGORA-CLIENTE.md`** — el documento de arquitectura:
   la decisión, quién hace qué, las dos distinciones finas que costó encontrar,
   los ejemplos de integración correcta e incorrecta, las consecuencias y qué
   invalidaría la decisión.
2. **Rechazo al cargar** — un contrato con un campo de planificador no carga.
   Deja de ser una propuesta que alguien tiene que vetar en revisión.
3. **29 pruebas** que defienden la frontera en los contratos, en los perfiles,
   en las skills y en la superficie del API.

## 2. Archivos

Creados:

- `docs/ADR-001-FRONTERA-AGORA-CLIENTE.md`
- `tests/test_architectural_boundary.py` — 29 pruebas.

Modificados:

- `src/agora/contracts.py` — `CALENDAR_TOKENS`, `CALENDAR_WORDS`,
  `CALENDAR_EXCEPTIONS` y la comprobación en `_check_house_rules`.

## 3. Rechazar, no recordar

La TAREA pedía «rechazar propuestas de campos temporales de planner en contratos
Agora si pertenecen al cliente». Se implementa donde de verdad rechaza: **al
cargar el contrato**.

```
ejemplo.v1.yml: raiz.pasos[].start_date es un campo del planificador del
cliente («date»). Agora ejecuta trabajo de IA; las fechas, las horas y la
disponibilidad son del cliente. Si hace falta hablar de esfuerzo, usa minutos
estimados, que no son una agenda.
```

El mensaje dice **dónde**, **por qué** y **qué usar en su lugar**. Un «no» que
no ofrece salida se termina saltando.

### Lo que se rechaza y lo que no

Se comprueban **nombres de campo**, nunca descripciones: explicar por qué no se
agenda es correcto y necesario —los cuatro contratos lo hacen—; tener dónde
escribir una fecha, no.

| Se rechaza | Pasa |
|---|---|
| `start_date`, `due_date`, `scheduled_day`, `hour_of_day` | `estimated_minutes` |
| `calendar_slot`, `work_block`, `workblock_id`, `timeblock` | `total_estimated_minutes` |
| `availability`, `deadline`, `week_number` | `duration_minutes`, `effort_minutes` |
| `pomodoro_count`, `vacation_days` | `summary`, `confidence`, `warnings` |

**El esfuerzo no es una agenda.** Decir que algo cuesta 45 minutos no dice
cuándo se hace, y es justo lo que el planificador del cliente necesita.

### Un falso positivo que encontré y arreglé

La primera versión comparaba por subcadena y rechazaba `today_matters` por
llevar `day` dentro. Eso no es una fecha, y una guardia que da falsos positivos
acaba desactivada por quien la sufre.

Ahora la comparación es **por palabras**: el nombre se parte por guiones bajos y
por cambios de caja. Y para que un guion no permita saltarse la guardia, las
palabras inequívocas (`workblock`, `calendar`, `deadline`, `pomodoro`…) se
buscan también sobre el nombre sin separadores: `work_block`, `workBlock` y
`workblock` son lo mismo. Hay una prueba de cada cosa.

## 4. Pruebas añadidas

`tests/test_architectural_boundary.py`, 29 pruebas:

- **Rechazo al cargar** (14): doce campos de planificador distintos en la raíz;
  uno escondido dentro de una lista —que es por donde se colaría de verdad, un
  paso con su fecha—; y que el rechazo diga qué usar en su lugar.
- **Lo que sí pasa** (7): los cuatro nombres de esfuerzo; campos corrientes,
  incluido `today_matters`; hablar de no agendar en las descripciones.
- **Precisión de la guardia** (3): un separador no la esquiva; una palabra
  contenida dentro de otra no dispara; el vocabulario no está vacío —una lista
  vacía pasaría todas las pruebas sin proteger nada—.
- **Lo que hay hoy** (4): ningún contrato publicado la cruza; ningún perfil
  ofrece agendar en sus `handles`; los cuatro de planificación lo declaran en
  `refuses`; **toda skill dice en su procedimiento que no agenda**.
- **La superficie pública** (2): nada de `workblock`, `pomodoro`,
  `availability`, `calendar`, `scheduler`, `vacación` ni «gestión de tareas» en
  lo que devuelven `/profiles`, `/board`, `/events` y `/health`; ninguna ruta del
  API menciona el calendario.

Suite completa: **494 pruebas, todas en verde** (eran 465 al cerrar A12).

## 5. Resultados reales

Esta fase no añade comportamiento nuevo en ejecución: añade un documento y unas
guardias. Lo que sí demuestra es que **lo construido durante doce fases cumple
la frontera**, y eso está comprobado sobre los artefactos reales de las
verificaciones anteriores:

- A04: el análisis de una revisión que pedía «reorganízame la semana» devolvió
  la petición en `warnings` sin una sola fecha.
- A05: el mismo encargo con y sin capacidad declarada dio `looks_ambitious` y
  `cannot_tell`; ninguna prescripción de agenda en ninguno de los dos.
- A06: la descomposición de un curso que pedía repartir las sesiones por semanas
  devolvió cuatro pasos con esfuerzo y ninguna fecha.
- A12: el catálogo público dejó de exponer incluso la ruta del disco.

La guardia nueva se ejecutó contra los cuatro contratos publicados al cargar el
registro: **los cuatro pasan sin cambios**. No hubo que corregir nada, que es el
resultado que se esperaba después de doce fases cuidando la línea a mano.

## 6. Compatibilidad hacia atrás

La guardia es una comprobación **nueva** sobre contratos que ya cumplían. Un
despliegue con los cuatro contratos actuales no nota nada.

Un despliegue con un contrato propio que llevara un campo de calendario dejaría
de cargar. Es deliberado: ese contrato estaba cruzando la frontera, y el error
lo dice con su sitio y su motivo.

## 7. Riesgos y deuda

- **La guardia solo mira contratos.** Un campo de calendario colado en la
  metadata de una CARD, o en la respuesta de un endpoint nuevo, no lo ve; para
  eso están las pruebas de superficie, que hay que ampliar si se añade un
  endpoint.
- **El vocabulario se mantiene a mano.** Si aparece un concepto de calendario
  con otro nombre, hay que añadirlo. No hay forma automática de saber qué es una
  fecha.
- **`start`, `end` y `when` están en la lista de palabras.** Son genéricas y
  algún día podrían dar un falso positivo legítimo. Está anotado aquí a
  propósito: si pasa, la respuesta correcta es pensar si ese campo es de verdad
  del cliente antes de quitarlas.
- **El runner del PC de IA sigue en 0.2.2.** Deuda arrastrada desde A10 §9. Nada
  de A07–A13 le afecta, pero la distancia es ya de siete versiones.

## 8. Siguiente fase

A14 — mantener AI_Broker genérico. Es la misma frontera vista desde el otro
lado: si Agora no puede saber de calendarios, el broker no puede saber ni de
calendarios ni de tareas personales.
