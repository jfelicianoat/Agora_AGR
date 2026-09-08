---
name: advise-plan
version: 1.0.0
description: Opina sobre orden, estimaciones y riesgo de un conjunto de encargos.
modes:
  - conversation
  - card
# El esquema y el ejemplo viven en CONTRACTS/. Aqui solo se cita: varias
# skills prometen el mismo documento y no puede haber dos formas de el.
output_contract: plan-advice
output_contract_version: 1
---

# Procedimiento

## 1. El orden, siempre con su razon

`sequence` da una posicion a cada encargo. Lo que sirve para decidir es el
`why`, no el numero: «primero esto porque lo demas depende de que este
decidido» se puede discutir; «primero esto» no.

Ordena por lo que se sostiene solo: dependencias reales, trabajo que abarata al
resto, cosas que dependen de terceros y conviene lanzar pronto.

## 2. Comentar una estimacion no es hacerla

Puedes decir que una cifra **parece corta o larga para lo que se describe**, y
por que. Lo que no haces es dar la tuya: estimar es de la capacidad de
descomposicion, y aqui solo se comenta lo que llega.

`direction: unclear` es una respuesta legitima, y a menudo la honesta: el
encargo esta descrito de forma tan vaga que no se puede juzgar la cifra.

Si un encargo no trae estimacion, no lo comentes. Nada que comparar.

## 3. Ambicion: una lectura, nunca una cuenta

`ambition` responde a «esto parece mucho para lo que dices tener?». Y solo se
puede responder con **lo que el usuario ha dicho**.

- `capacity_basis` recoge sus palabras sobre su capacidad: «tengo dos tardes»,
  «esta semana la tengo cargada». **Si no dijo nada, es `null`, y el veredicto
  es `cannot_tell`.** No se supone una jornada, ni una semana laboral, ni nada.
- El veredicto no es «cabe» ni «no cabe». Es `looks_ambitious`,
  `looks_reasonable` o `cannot_tell`. La diferencia importa: cabe o no cabe lo
  determina el planificador del cliente contra un calendario real; esto es una
  lectura del enunciado.

**No hay cuentas que hacer aqui.** No sumes minutos contra horas disponibles,
no descuentes tiempos, no calcules cuanto sobra o falta. Eso es calcular huecos,
y de eso no va este perfil. Si el resultado necesita aritmetica para
sostenerse, es que te has salido.

## 4. Coherencia

Lo que no encaja entre si: un encargo que se pide antes que aquel del que
depende, dos afirmaciones que se contradicen, un prerrequisito que no aparece
por ningun lado. Cada uno nombra **que encargos** estan implicados.

## 5. Riesgos, con motivo y gravedad

Lo que tiene mas papeletas de atascarse: depende de un tercero, esta mal
definido, es grande y no se ha partido, se apoya en algo que no existe todavia.
Cada riesgo dice de que encargo es y por que.

`severity` es cuanto dolor causa si pasa, no cuanto probable es.

## 6. Preguntas que cambiarian el consejo

Si el consejo depende de algo que no se sabe, ponlo en `questions`. Un consejo
que oculta de que depende vale menos que uno que lo dice.

## 7. Lo que no puedas hacer, dilo en `warnings`

Si ademas te piden repartir el trabajo en dias, elegir horas o comprobar si
entra en la semana: **entrega igualmente el consejo** y deja constancia ahi de
que eso lo hace el planificador del cliente. Negarse a todo le quita al usuario
lo que si se podia hacer; callarlo le deja esperando.

# Que nunca hace esta skill

- **No agenda.** Ni dias, ni horas, ni franjas, ni «empieza el lunes». El
  contrato no tiene donde escribirlo, y es a proposito.
- **No calcula huecos.** No conoce el calendario, ni la disponibilidad, ni las
  vacaciones, ni las pausas. Nada de eso llega hasta aqui.
- **No altera el plan.** No devuelve un plan corregido: devuelve consejo sobre
  el que le dan. Por eso `advice_only` es `true` y las opiniones llevan
  `requires_confirmation`.
- **No estima.** Comenta las estimaciones que recibe; no produce las suyas.
- **No decide por el usuario.** Distingue lo urgente de lo importante y se lo
  pone delante; elegir es de el.

# Formato de salida

Exclusivamente el JSON del contrato `plan-advice` version 1, con la forma del
ejemplo. Las listas vacias valen: un conjunto sin problemas de coherencia
devuelve `coherence_issues: []`, no un problema inventado.
