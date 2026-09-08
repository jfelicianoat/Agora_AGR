---
name: decompose-course
version: 1.0.0
description: Descomposicion de encargos de curso, asignatura o formacion.
modes:
  - conversation
  - card
# El esquema y el ejemplo viven en CONTRACTS/. Aqui solo se cita: varias
# skills prometen el mismo documento y no puede haber dos formas de el.
output_contract: work-breakdown
output_contract_version: 1
---

# Cuando se aplica

Cuando el encargo es **seguir o impartir un curso, una asignatura o una
formacion**: hay temario, sesiones y normalmente una evaluacion.

Se aplica ademas del procedimiento generico, no en su lugar: todo lo que
dice `decompose-work` sigue vigente.

# Que suele faltar en un encargo de curso

- **La evaluacion es el resultado, no el temario.** Un curso se aprueba por lo
  que se entrega o se examina. Si hay entregas o examen, son pasos con nombre
  propio y casi siempre los ultimos de los que dependen los demas.
- **Las sesiones no son pasos.** «Asistir a la clase 4» no produce nada
  comprobable. Lo que produce resultado es lo que se hace con la clase: apuntes
  resumidos, ejercicios entregados, dudas resueltas.
- **El material se prepara antes.** Si hay que leer, conseguir o instalar algo
  para poder seguir, eso es un paso previo del que dependen los demas.

# Estructura habitual

1. preparar el material y el entorno;
2. un paso por bloque de temario con resultado propio;
3. los ejercicios o practicas que se entregan;
4. la preparacion de la evaluacion, que depende de los anteriores.

# Cuidado con

- **Un paso por sesion** hincha la lista sin anadir informacion. Agrupa por
  bloque de temario, no por dia de clase.
- **El repaso final es opcional solo si hay margen**; si la evaluacion es dura,
  no lo es. Marcalo con criterio, no por defecto.
- **Las fechas del curso no se copian aqui.** Aunque el encargo mencione que el
  examen es en junio, esta skill no pone fechas: eso va al planificador del
  cliente, que sabe que hacer con un plazo.

# El limite, tambien aqui

No agenda. Esta skill no pone fechas, ni dias, ni horas, aunque el
encargo mencione plazos. Estima esfuerzo en minutos y nada mas; el
calendario y la disponibilidad son del cliente y su planificador.
