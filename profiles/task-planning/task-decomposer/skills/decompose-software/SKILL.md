---
name: decompose-software
version: 1.0.0
description: Descomposicion de encargos de construir o cambiar software.
modes:
  - conversation
  - card
# El esquema y el ejemplo viven en CONTRACTS/. Aqui solo se cita: varias
# skills prometen el mismo documento y no puede haber dos formas de el.
output_contract: work-breakdown
output_contract_version: 1
---

# Cuando se aplica

Cuando el encargo es **construir, cambiar o arreglar software**: una
funcionalidad, una correccion, una migracion, un script.

Se aplica ademas del procedimiento generico, no en su lugar: todo lo que
dice `decompose-work` sigue vigente.

# Que suele faltar en un encargo de software

- **Como se comprueba.** En software el criterio de terminado casi siempre es
  una comprobacion ejecutable: una prueba que pasa, un comando que devuelve lo
  esperado, una pantalla que hace lo que dice. «Funciona» no es criterio.
- **Que se rompe.** Un cambio en algo que ya existe necesita un paso para
  comprobar que lo demas sigue funcionando. Es un paso, no un detalle.
- **Donde empieza y donde acaba.** Si el encargo no dice si incluye desplegar,
  documentar o migrar datos, avisalo en `warnings` en vez de decidirlo.

# Estructura habitual

1. entender el estado actual, si se toca algo que ya existe;
2. la comprobacion que hoy falla y manana tiene que pasar;
3. el cambio en si, partido por pieza si es grande;
4. comprobar que no se rompio lo de alrededor;
5. documentar o desplegar, **solo si el encargo lo pide**.

# Cuidado con

- **No inventes arquitectura.** Si el encargo no dice que tecnologia, que
  patron o que servicio, no aparecen. Un paso llamado «crear el microservicio»
  cuando nadie pidio un microservicio es inventarse el encargo.
- **Las pruebas no son un paso al final.** Si el encargo las menciona o el
  cambio es delicado, cada pieza lleva la suya.
- **Los pasos de investigacion son legitimos** —«reproducir el fallo» lo es—
  pero necesitan criterio de terminado igual que los demas: «fallo reproducido
  con pasos escritos».

# El limite, tambien aqui

No agenda. Esta skill no pone fechas, ni dias, ni horas, aunque el
encargo mencione plazos. Estima esfuerzo en minutos y nada mas; el
calendario y la disponibilidad son del cliente y su planificador.
