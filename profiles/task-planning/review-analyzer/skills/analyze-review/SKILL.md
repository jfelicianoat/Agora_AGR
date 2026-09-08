---
name: analyze-review
version: 1.0.0
description: Lee una revision y separa lo que dijo el usuario de lo que se deduce.
modes:
  - conversation
  - card
# El esquema y el ejemplo viven en CONTRACTS/. Aqui solo se cita: varias
# skills prometen el mismo documento y no puede haber dos formas de el.
output_contract: review-analysis
output_contract_version: 1
---

# Procedimiento

## 1. Separar lo dicho de lo deducido

Es la regla que ordena todo lo demas.

- **`facts`**: lo que el usuario dijo. Cada uno con `evidence`, el fragmento
  literal del que sale. Si no puedes citarlo, no es un hecho.
- **`inferences`**: lo que tu concluyes. Cada una citando **que hechos** la
  sostienen, y —si los hay— cuales la contradicen.

Un hecho sin cita es una deduccion disfrazada. No las mezcles.

## 2. Nunca des algo por completado

`kind: completed` solo cuando **el usuario lo dice**. «Ya esta», «lo termine»,
«queda hecho».

Lo que **no** vale como completado:

- que el usuario hable de un trabajo en pasado;
- que describa el resultado sin decir que lo termino;
- que mencione el paso siguiente;
- que no diga nada de una tarea que estaba prevista. **El silencio no es
  «hecho»**, y tampoco es «no hecho»: sencillamente no hay hecho.

Si crees que algo se completo pero no lo dijo, eso es una `inference`, y toda
`inference` lleva `requires_confirmation: true`. Cerrar trabajo que en realidad
sigue abierto es el error mas caro que puede cometer esta capacidad.

## 3. Causas, con la evidencia delante

En `causes` va por que paso lo que paso, **solo** si los hechos lo permiten. Si
dos explicaciones encajan igual de bien, ofrece las dos con confianza baja en
lugar de elegir una.

Con uno o dos hechos no se declara una tendencia. Si la muestra es pequena, la
`confidence` lo tiene que reflejar.

## 4. Trabajo descubierto: se propone, no se da de alta

`discovered_work` es trabajo que aparecio y no estaba previsto. Cada entrada
dice **por que emergio** y cita la evidencia.

Son propuestas. Llevan `requires_confirmation: true` porque quien decide si
existe una tarea nueva es una persona, no esta capacidad.

Si el usuario ya lo describio como una tarea suya, no es trabajo descubierto:
es un hecho.

## 5. Dependencias: lo mismo

`proposed_dependencies` recoge lo que parece bloquear a que. Se proponen con su
evidencia y su motivo; aplicarlas es del cliente.

Una dependencia real es «no puede empezar hasta que». Que algo «quede mejor
despues» no es una dependencia.

## 6. Lenguaje que describe, no que juzga

«Tres sesiones de la tarde quedaron sin hacer» es una observacion. «Te cuesta
trabajar por la tarde» es un juicio, y ademas es una deduccion.

No se atribuyen intenciones, ni pereza, ni falta de disciplina. Que algo no se
hiciera no es un fallo de nadie.

## 7. Lo que no puedas hacer, dilo en `warnings`

Si la peticion incluye algo que esta capacidad no hace —reorganizar la semana,
elegir horas—, **entrega igualmente el analisis** y deja la constancia en
`warnings`. Callarlo deja al usuario esperando algo que no va a llegar; negarse
a todo le quita tambien lo que si se podia hacer.

Ahi va tambien lo que conviene saber para leer el resultado: que la revision
apenas daba informacion, que un dato admitia dos lecturas.

Y ahi es donde va el silencio. Si la tarjeta trae el trabajo previsto y el
usuario no dice nada de una de esas tareas, **no la conviertas en un hecho**,
pero **dilo**: que estaba prevista, que la revision no la menciona y que no se
puede saber si se hizo. Eso es informacion util; darla por hecha o por no hecha
seria inventarla.

# Que nunca hace esta skill

- **No cierra tareas.** No hay ningun campo que marque algo como hecho por
  deduccion, y no lo habra.
- **No agenda.** Ni dias, ni horas, ni «deberias hacerlo manana». El calendario
  y la disponibilidad son del cliente y su planificador; esta skill no los
  conoce ni los pide.
- **No replanifica.** Puede senalar que algo se atasca; que se hace con eso lo
  decide el cliente.
- **No inventa hechos.** Si la revision no lo dice, no esta.

# Formato de salida

Exclusivamente el JSON del contrato `review-analysis` version 1, con la forma
del ejemplo. Las listas vacias son respuestas validas: una revision sin trabajo
descubierto devuelve `discovered_work: []`, no una invencion.
