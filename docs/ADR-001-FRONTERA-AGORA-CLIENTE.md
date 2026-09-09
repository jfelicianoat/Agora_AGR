# ADR-001 — Agora ejecuta trabajo de IA; el calendario es del cliente

**Estado**: aceptado
**Fecha**: 2026-09-09
**Contexto de entrega**: fase A13 de la serie de integración con Gestión de
Tareas IA.

## Decisión

> **Agora ejecuta trabajo de IA. El cliente es dueño de sus tareas, su
> calendario, su disponibilidad y su planificador.**

Ningún contrato, perfil, skill ni endpoint de Agora contiene una fecha, una
hora, una franja, un bloque de trabajo ni una disponibilidad. Agora no sabe
cuándo trabaja el usuario, y no debe saberlo.

## Por qué existe este documento

La frontera no se cruza de golpe. Se cruza poco a poco, y siempre con una buena
razón local:

- «El descompositor ya sabe cuánto cuesta cada paso, sería trivial que
  devolviera también qué día hacerlo.»
- «El analizador de revisiones ve que el usuario no avanza los martes; podría
  proponerle mover el trabajo.»
- «El asesor podría decir directamente si el trabajo cabe en la semana.»

Cada una parece útil. Aceptadas todas, Agora es medio calendario, hay dos
sistemas decidiendo fechas y ninguno de los dos es fiable. Y el usuario no
sabría cuál manda.

Durante las fases A02 a A05 esta presión apareció **cada vez**. Los modelos, si
se les da un encargo con fechas dentro, agendan; es lo natural. La frontera se
sostuvo, pero se sostuvo a base de escribirla otra vez en cada skill. Este
documento la escribe una sola vez, y A13 la convierte en algo que el código
comprueba.

## Qué hace cada lado

| | Agora | Gestión de Tareas (o cualquier cliente) |
|---|---|---|
| Entender un encargo mal definido | **sí** | no |
| Partirlo en pasos verificables con su esfuerzo | **sí** | no |
| Leer una revisión y separar hechos de deducciones | **sí** | no |
| Opinar sobre orden y riesgo | **sí** | no |
| Saber qué días y horas trabaja el usuario | no | **sí** |
| Decidir cuándo se hace cada cosa | no | **sí** |
| Calcular si algo cabe en la semana | no | **sí** |
| Mover trabajo cuando algo se retrasa | no | **sí** |
| Guardar las tareas y su estado real | no | **sí** |

La regla para decidir un caso nuevo: **¿hace falta conocer el calendario del
usuario para responder?** Si sí, es del cliente. Siempre.

### Dos distinciones finas que hubo que escribir

Salieron en A05 y merecen quedarse:

- **Comentar una estimación no es hacerla.** Agora puede decir que treinta
  minutos parecen pocos para veinte diapositivas con gráficos propios, y por
  qué. Dar la cifra corregida es de la capacidad de descomposición, no del
  asesor.
- **Leer una ambición no es calcular un hueco.** Agora puede decir que un
  conjunto parece mucho *para lo que el propio usuario dice tener*, citando sus
  palabras. Lo que no puede es hacer la cuenta: eso exige un calendario real.
  Por eso el veredicto es `looks_ambitious` o `cannot_tell`, **nunca «no
  cabe»**.

El esfuerzo en minutos **sí** cruza la frontera hacia el cliente, y está bien:
decir que algo cuesta 45 minutos no dice cuándo se hace. Es justo lo que el
planificador del cliente necesita para hacer su trabajo.

## Cómo se hace cumplir

No basta con acordarlo. A13 lo comprueba en tres sitios:

1. **Al cargar un contrato** (`agora/contracts.py`). Un contrato con un campo
   `start_date`, `work_block`, `deadline`, `availability`, `pomodoro`… **no
   carga**. El error dice qué pasa y qué usar en su lugar. La comparación es por
   palabras completas y sin separadores: `work_block`, `workBlock` y
   `workblock` son lo mismo, y `today_matters` no es una fecha.
2. **Sobre los perfiles y las skills reales** (`tests/test_architectural_boundary.py`).
   Ningún perfil ofrece agendar en sus `handles`; los cuatro de planificación lo
   declaran en `refuses`; toda skill dice en su procedimiento que no agenda.
3. **Sobre la superficie pública del API.** Se recorre lo que devuelven
   `/profiles`, `/board`, `/events` y `/health` buscando `workblock`,
   `pomodoro`, `availability`, `calendar`, `scheduler`, `vacación` y «gestión de
   tareas»; y se comprueba que ninguna ruta del API menciona el calendario.

Si alguien propone un campo temporal en un contrato, la propuesta se cae sola al
cargarlo. Eso es lo que pedía la fase: **rechazar**, no recordar.

## Integración correcta: dos ejemplos

### Bien — el cliente pregunta, y planifica él

```
Cliente                          Agora                    Cliente
───────────────────────────────────────────────────────────────────────
"break down work: preparar   →   work-breakdown@1     →   El planificador
 la defensa del TFM"             4 pasos                  local reparte
                                 con esfuerzo en           esos 300 min
                                 minutos y                 en los huecos
                                 dependencias              reales del
                                                           usuario
```

La CARD:

```json
{
  "filename": "defensa.md",
  "function": "decompose",
  "request": "break down work: preparar la defensa del TFM",
  "body": "Presentación, ensayo y preguntas previsibles del tribunal.",
  "external_reference": { "system": "gestion-tareas", "id": "tarea-4821" }
}
```

El artefacto trae `estimated_minutes` por paso y `depends_on`. **No trae ni una
fecha.** El cliente, que sí conoce la disponibilidad del usuario, decide cuándo.

### Mal — pedirle a Agora que agende

```json
{
  "request": "break down work: preparar la defensa",
  "body": "Repárteme la semana: qué día y a qué hora me pongo con cada cosa."
}
```

Esto se manda igual —los usuarios escriben así— y Agora responde bien: entrega
la descomposición y deja constancia en `warnings`:

> La petición incluye asignar semanas y horas a cada sesión. Repartir el trabajo
> en días y horas corresponde al planificador del cliente, que conoce la
> disponibilidad real.

Es una respuesta real, de la verificación de A06 §7.

**Lo que no se hace es rechazar la tarjeta entera.** Se aprendió en A03: prohibir
agendar sin dar salida hacía que el modelo contestara «lo siento, no puedo
proporcionarte un horario» y no entregara nada. La regla que funciona es
*entrega lo que sí puedes y deja escrito lo que no*.

### Mal — proponer el campo «solo por comodidad»

> «`work-breakdown` podría llevar un `suggested_start_date` opcional, para que
> el cliente no tenga que calcularlo.»

No. El cliente no tiene que calcularlo: **es lo único que él sabe hacer y Agora
no**. Un campo opcional con una fecha sugerida sería una fecha sin calendario
detrás, y en cuanto un cliente la creyera, tendríamos dos planificadores. Hoy
esa propuesta ni siquiera llega a revisión: el contrato no carga.

## Consecuencias

**A favor.** Agora sigue siendo reutilizable por cualquier cliente, tenga
calendario o no. El planificador del cliente sigue siendo local, determinista y
reproducible, sin depender de un modelo. Y AI_Broker permanece genérico: nunca
ve un concepto de calendario ni de tarea personal.

**En contra.** El cliente tiene más trabajo: recibe esfuerzo y dependencias, y
tiene que repartirlos él. Y hay respuestas que al usuario le pueden parecer
incompletas —«te he dicho que me organices la semana»— hasta que el cliente
convierte la descomposición en un plan.

Se acepta ese coste. La alternativa es peor: dos sistemas decidiendo fechas.

## Qué invalidaría esta decisión

Que un cliente sin planificador propio quisiera usar Agora y necesitara que
alguien agendara por él. Ese caso no existe hoy, y la salida correcta no sería
meter el calendario en Agora: sería un **perfil de planificación separado**, con
su propio contrato, al que el cliente le pasara explícitamente su
disponibilidad. Agora seguiría sin saber nada por su cuenta.

## Referencias

- `docs/A01_PERFILES_PLANIFICACION.md` — dónde se trazó la frontera.
- `docs/A05_PLANNING_ADVISOR.md` §3 — las dos distinciones finas.
- `docs/A07_CONTRATO_INTEGRACION_CLIENTE.md` — el contrato de integración.
- `docs/A13_FRONTERA.md` — cómo se comprueba.
- `src/agora/contracts.py` — `CALENDAR_TOKENS`, `CALENDAR_WORDS`.
- `tests/test_architectural_boundary.py`.
