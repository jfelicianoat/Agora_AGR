---
name: adaptive-task-interview
version: 1.0.0
description: Entrevista adaptativa que acota un encargo sin inventar nada.
modes:
  - conversation
  - card
# El esquema y el ejemplo viven en CONTRACTS/. Aqui solo se cita: varias
# skills prometen el mismo documento y no puede haber dos formas de el.
output_contract: task-brief
output_contract_version: 1
---

# Procedimiento

## 1. Leer y separar

Lee la peticion entera antes de escribir nada y separa tres cosas:

- lo que **dice** (va a `understood`, cada una con el fragmento que la sostiene);
- lo que **hay que suponer** para seguir (va a `assumptions`);
- lo que **falta decidir** (va a `questions`).

Si algo no esta en la peticion, no esta. No se completa con lo habitual, ni con
lo razonable, ni con lo que suele querer la gente.

## 2. Preguntar solo lo material

Una pregunta es material si **la respuesta cambia el encargo**. Si el encargo
sale igual conteste lo que conteste, la pregunta sobra.

Por eso cada pregunta lleva `why_it_matters`: si no sabes escribir que cambia,
esa pregunta no va.

Tres o cuatro preguntas es mucho. Cero es perfectamente valido: un encargo claro
no necesita entrevista.

Cuando la respuesta este entre pocas alternativas, ofrecelas en `options`. Es
mas facil elegir que redactar.

## 3. Declarar los supuestos, no esconderlos

Un supuesto que no se dice se convierte en un hecho falso tres pasos despues.
Cada supuesto va con `impact_if_wrong`: que se rompe si resulta que no era
cierto.

Si un supuesto es demasiado arriesgado para asumirlo, no es un supuesto: es una
pregunta.

## 4. Adaptarse al tipo de encargo

El procedimiento es el mismo para cualquier encargo, pero lo que falta cambia:

- **encargo creativo o de redaccion**: suele faltar el destinatario, la
  extension y el tono;
- **encargo tecnico**: suele faltar el criterio de terminado y con que se
  comprueba;
- **encargo administrativo**: suele faltar quien tiene que firmar o recibir, y
  que documento hace falta;
- **encargo de estudio o lectura**: suele faltar para que se lee, que es lo que
  hay que sacar.

Son pistas de donde mirar, no preguntas obligatorias. Si la peticion ya lo dice,
no se pregunta.

## 5. Confianza honesta

`confidence` es cuanto has entendido el encargo, no cuanto te gusta la
respuesta. Un encargo con cuatro preguntas abiertas no puede tener 0.9.

# Que nunca hace esta skill

- **No descompone.** Los pasos son de otra capacidad.
- **No estima tiempo.** Ni minutos, ni horas, ni «un par de dias».
- **No agenda.** No propone cuando hacer nada. El calendario, la disponibilidad
  y el planificador son del cliente, y esta skill no los conoce ni los pide.
- **No decide por el usuario.** Lo que requiere criterio humano sale como
  pregunta, no como conclusion.

# Formato de salida

Exclusivamente el JSON que declara `output_schema`, sin Markdown alrededor, sin
comentarios y sin texto antes ni despues. `contract` vale `task-brief` y
`contract_version` vale `1`: si algun dia cambia la forma, cambia el numero, y
quien lo lea sabra que esta leyendo.
