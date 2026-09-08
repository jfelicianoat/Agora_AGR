---
name: decompose-study
version: 1.0.0
description: Descomposicion de encargos de estudio, lectura o investigacion.
modes:
  - conversation
  - card
# El esquema y el ejemplo viven en CONTRACTS/. Aqui solo se cita: varias
# skills prometen el mismo documento y no puede haber dos formas de el.
output_contract: work-breakdown
output_contract_version: 1
---

# Cuando se aplica

Cuando el encargo es **estudiar, leer o investigar** para saber algo, sin un
entregable evidente: preparar una oposicion, ponerse al dia en un tema, revisar
bibliografia.

Se aplica ademas del procedimiento generico, no en su lugar: todo lo que
dice `decompose-work` sigue vigente.

# Que suele faltar en un encargo de estudio

- **Para que se estudia.** No es lo mismo leer para poder opinar que leer para
  poder examinarse. Cambia la profundidad y cambia el criterio de terminado. Si
  el encargo no lo dice, avisalo en `warnings`.
- **Como se sabe que ya se sabe.** Es el punto dificil de estos encargos.
  Criterios que si funcionan: «puedo explicarlo sin mirar», «he resuelto diez
  ejercicios del tipo X», «tengo un resumen de una pagina». Criterios que no:
  «lo he leido», «lo entiendo».
- **El material concreto.** Si no se sabe que se va a leer, conseguir la
  bibliografia es el primer paso y todo depende de el.

# Estructura habitual

1. delimitar el alcance: que entra y que no;
2. conseguir el material;
3. un paso por bloque de contenido, cada uno con su prueba de que se ha
   asimilado;
4. una comprobacion final del conjunto.

# Cuidado con

- **«Leer el libro» no es un paso.** Leer no produce nada comprobable. Lo que
  produce resultado es el resumen, el esquema o los ejercicios.
- **El estudio se subestima siempre.** Si el encargo no da datos para estimar,
  es mejor `estimated_minutes: null` y un aviso que un numero optimista.
- **Repasar es un paso, no un adorno.** Si el objetivo es retener, el repaso
  entra en la lista y depende del bloque que repasa.

# El limite, tambien aqui

No agenda. Esta skill no pone fechas, ni dias, ni horas, aunque el
encargo mencione plazos. Estima esfuerzo en minutos y nada mas; el
calendario y la disponibilidad son del cliente y su planificador.
