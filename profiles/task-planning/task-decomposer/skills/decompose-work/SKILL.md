---
name: decompose-work
version: 1.2.0
description: Descomposicion generica en pasos verificables con esfuerzo y dependencias.
modes:
  - conversation
  - card
# El esquema y el ejemplo viven en CONTRACTS/. Aqui solo se cita: varias
# skills prometen el mismo documento y no puede haber dos formas de el.
output_contract: work-breakdown
output_contract_version: 1
---

# Procedimiento

## 1. Partir por resultado, no por tema

Cada paso tiene que **producir algo** que se pueda mirar y decir «esto ya
esta». «Estudiar el tema 3» no produce nada comprobable; «resumir el tema 3 en
una pagina» si.

Si un paso no tiene un resultado observable, no es un paso: es un rato.

## 2. Pocos pasos

El minimo que permita ejecutar el encargo y saber por donde va. Un paso que se
hace de una sentada ya no se parte; partirlo solo anade burocracia.

Como regla practica: si salen mas de ocho o nueve pasos para un encargo
normal, casi seguro se esta descomponiendo de mas.

## 3. Criterio de terminado, siempre

Es el campo que hace verificable la descomposicion. Tiene que poder
comprobarlo alguien que no hizo el trabajo. Nada de «bien hecho», «revisado a
fondo» o «suficiente».

## 4. Estimar esfuerzo, nunca fechas

`estimated_minutes` dice **cuanto cuesta**, no **cuando se hace**. Es la
informacion que el planificador del cliente necesita para colocarlo; el
planificador conoce la disponibilidad real y este procedimiento no.

Si el encargo no da informacion para estimar, `estimated_minutes` es `null`. Un
numero inventado es peor que un hueco: contamina las estadisticas de quien lo
reciba.

Estima el esfuerzo de una persona concentrada, sin interrupciones. Los
descansos y los imprevistos los pone el planificador.

## 5. Dependencias reales

Un paso depende de otro solo si **no se puede empezar** hasta que el otro este
hecho. «Queda mejor despues» no es una dependencia; es una preferencia de
orden, y esa la da la posicion en la lista.

Las dependencias citan valores de `order` y nunca forman ciclo.

## 6. Marcar lo opcional

Si el encargo sigue estando hecho sin ese paso, es opcional. Separar lo
imprescindible de lo deseable es la mitad de una buena descomposicion: permite
recortar cuando el tiempo aprieta sin discutir que se sacrifica.

## 7. Avisar de lo que no cuadra

`warnings` es para lo que quien reciba esto deberia mirar: un encargo que
parece mucho mayor de lo que dice, una dependencia externa que no controla, un
criterio de terminado que en realidad depende de que otro apruebe.

# Si el encargo pide ademas fechas u horas

Entrega la descomposicion igual y anade en `warnings` que repartir el trabajo en
dias y horas corresponde al planificador del cliente, que conoce su
disponibilidad. **Nunca rechaces el encargo entero por eso**: lo que se pedia
—los pasos— si se puede hacer, y negarse a todo deja al usuario sin nada.

Pero **tampoco lo agendes**. Ni dentro del JSON ni fuera de el: no anadas
despues del documento una lista de dias, un horario ni una propuesta de
reparto. El aviso en `warnings` es toda la respuesta que merece esa parte de la
peticion.

# Que nunca hace esta skill

- **No agenda.** Ni dias, ni horas, ni «la semana que viene».
- **No inventa entregables.** Si el encargo no menciona una herramienta, un
  formato o un responsable, no aparecen.
- **No repite el encargo como paso.** «Hacer la presentacion» no es un paso de
  «hacer la presentacion».
- **No decide por el usuario.** Lo que exige criterio humano se avisa en
  `warnings`.

# Formato de salida

Exclusivamente el JSON de `output_schema`, sin Markdown alrededor.
`contract` vale `work-breakdown` y `contract_version` vale `1`.
