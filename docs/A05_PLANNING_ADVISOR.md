# A05 — Planning advisor sin calendario

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A05_planning_advisor_no_calendar.md`.

## 1. Resumen

Una skill, `advise-plan`, que opina sobre un conjunto de encargos: **en qué
orden**, **qué estimaciones no encajan**, **qué no se sostiene entre sí**, **qué
puede atascarse** y **si el conjunto parece ambicioso** — sin tocar un
calendario y sin hacer una sola cuenta.

La TAREA del prompt son tres líneas, y las tres son fronteras:

| Lo que pedía el prompt | Cómo se cumple |
|---|---|
| Puede comentar estimaciones o coherencia | `estimate_comments` con `direction: looks_short / looks_long / unclear`, y `coherence_issues` nombrando los encargos implicados |
| No calcula huecos | `ambition.verdict` sólo admite `looks_ambitious`, `looks_reasonable` o `cannot_tell` — no existe «cabe». Y sin `capacity_basis` declarada por el usuario, el veredicto tiene que ser `cannot_tell` |
| No altera planes | `advice_only: {const: true}` en la raíz, `requires_confirmation` en los juicios, y **ningún campo donde devolver un plan corregido** |

Verificada contra el broker real con **dos variantes de la misma tarjeta** que
se diferencian en una sola frase (§6), y **de punta a punta** —tarjeta → runner
→ broker → artefacto— con Agora 0.2.1 desplegado en el PC de IA (§7).

## 2. Archivos

Creados:

- `profiles/task-planning/planning-advisor/skills/advise-plan/SKILL.md`
- `tests/test_plan_advice_skill.py` — 35 pruebas.
- `scripts/verify_a05_plan_advice.py` — verificación contra el broker real.

Modificados:

- `profiles/task-planning/planning-advisor/PROFILE.md` — 1.0.0 → **1.1.0**.
- `src/agora/broker/contracts.py` — `BrokerPolicy.max_output_tokens` (por
  defecto 4000, el valor anterior).
- `src/agora/broker/request_builder.py` — usa el de la política en vez de una
  constante.
- `src/agora/broker/executor.py` — `CONTRACT_OUTPUT_TOKENS = 8000`; un contrato
  de salida declarado sube el tope.
- `tests/test_output_contract.py` — 5 pruebas del tope.
- `pyproject.toml`, `src/agora/__init__.py` — **0.2.0 → 0.2.1**.

## 3. Decisiones de diseño

### Dos líneas finas, y hay que decirlas

A01 dejó escrito que este perfil «nunca dice cuánto va a durar algo» ni «afirma
que algo cabe o no cabe». A05 pide que **sí** pueda comentar estimaciones y
detectar ambición. No es una contradicción, pero la distinción es fina y estaba
sin escribir, así que ahora está en el PROFILE con esas palabras:

- **Comentar una estimacion no es hacerla.** Puede decir que 30 minutos parece
  poco para veinte diapositivas con gráficos propios, y por qué. Dar una cifra
  propia es de la capacidad de descomposición. Por eso `estimate_comments` tiene
  una **dirección**, no un número, y hay una prueba que comprueba que ningún
  campo de esa sección contiene `minutes`.
- **Leer una ambición no es calcular un hueco.** Puede decir que el conjunto
  parece mucho *para lo que el propio usuario dice tener*, citando sus palabras
  en `capacity_basis`. Lo que no puede es hacer la cuenta: eso exige un
  calendario real, y ese lo tiene el planificador del cliente.

### `cannot_tell` es lo que impide inventar la capacidad

Es la pieza que sostiene «no calcula huecos». Si el encargo no dice de cuánto
tiempo dispone el usuario, `capacity_basis` es `null` y el veredicto es
`cannot_tell`. No se supone una jornada, ni una semana laboral, ni nada.

Sin esa regla, «parece ambicioso» acabaría apoyándose en una capacidad
imaginada, que es calcular un hueco con datos inventados: lo peor de las dos
cosas. La verificación lo prueba con dos tarjetas idénticas salvo esa frase
(§6).

Nótese que **una fecha objetivo no es una capacidad**. El modelo lo distinguió
solo: «sólo indica fechas objetivo (jueves, viernes)... sin una base de
capacidad declarada no se puede juzgar».

### No hay dónde devolver un plan corregido

`advice_only` es `{const: true}` y obligatorio: el documento se declara consejo.
No existe ningún campo que devuelva el plan rehecho, y hay una prueba que
comprueba que colar un `revised_plan` lo rechaza. Lo que sale de aquí es
material para decidir, no una decisión.

### El tope de salida estaba clavado en 4000, y truncaba

`request_builder.py` pedía `max_output_tokens: 4000` como constante. La primera
ejecución real de A05 salió cortada **a media cadena**: JSON válido hasta el
corte, inválido después, `tokens_output: 4000` exactos en las dos variantes.

Un documento con contrato tiene secciones fijas y es estructuralmente más largo
que la prosa equivalente, así que el tope pasa a la política
(`BrokerPolicy.max_output_tokens`, con el mismo 4000 por defecto) y declarar un
contrato de salida lo sube a 8000. Lo que no declara contrato se comporta
exactamente igual que antes, y hay una prueba de eso. Si alguien ya había subido
el tope a propósito, no se le baja.

### `model_capacity: maximum`

Por lo mismo que en A04: con enrutado libre la tarjeta puede caer en un modelo
que devuelva el prompt y morir sin reintento.

## 4. Migraciones

Ninguna. Agora no tiene esquema que migrar; el cambio de `BrokerPolicy` es
compatible hacia atrás por defecto y hay prueba de ello.

## 5. Pruebas añadidas

`tests/test_plan_advice_skill.py`, 35 pruebas, y 5 más en
`tests/test_output_contract.py`:

- **Perfil** (5): declara la skill; promete no calcular huecos; sigue rechazando
  el calendario; escribe las dos líneas finas; entrega el consejo en vez de
  rechazar la tarjeta.
- **Contrato** (3): versión declarada; el ejemplo cumple su esquema; todas las
  secciones cerradas a campos extra.
- **Ni calendario ni huecos** (6): el esquema no contiene `date`, `day`, `hour`,
  `slot`, `schedule`, `deadline`, `block`, `availab` ni `calendar`; el veredicto
  no admite «cabe»; un veredicto inventado se rechaza; aritmética colada en
  `ambition` se rechaza; sin capacidad declarada el documento válido es
  `cannot_tell`; el procedimiento lo dice también con palabras.
- **No altera el plan** (6): `advice_only` obligatorio y `const: true`; un
  documento que se declara vinculante se rechaza; los juicios exigen
  `requires_confirmation` y lo rechazan en `false`; un `revised_plan` colado se
  rechaza.
- **Comentar no es estimar** (3): la dirección es un enum, no hay ningún campo
  con `minutes`, una estimación propia se rechaza, y `unclear` es válido.
- **Lo demás** (7): toda posición lleva su razón y sin ella se rechaza;
  `severity` acotada; el ejemplo avisa de que no agenda; listas vacías válidas;
  contrato equivocado rechazado; JSON envuelto en prosa aceptado.
- **Broker** (3): el perfil exige `maximum`; la política lleva el esquema pero
  no el modo json; el prompt cierra con el ejemplo relleno.
- **Tope de salida** (5): sin contrato, 4000 y la política intacta; con
  contrato, 8000; un tope mayor puesto a mano no se baja; la petición pide lo
  que diga la política; un tope de cero es un error.

Suite completa de Agora: **292 pruebas, todas en verde** (eran 252 al cerrar
A04).

## 6. Resultados reales

La tarjeta de prueba trae cuatro encargos y varias trampas: dos estimaciones
claramente cortas (veinte diapositivas con gráficos propios en 30 minutos, 40
ejercicios en 25), una dependencia de un tercero que no responde, un orden que
el propio usuario se contradice («ensayar el jueves, diapositivas listas el
viernes») y una petición final de que le repartan la semana en días y horas.

Y se lanzó **dos veces, con una sola frase de diferencia**:

| | `con-capacidad` | `sin-capacidad` |
|---|---|---|
| Frase extra | «Esta semana solo tengo el sábado por la mañana» | — |
| `ambition.verdict` | **`looks_ambitious`** | **`cannot_tell`** |
| `ambition.capacity_basis` | `"solo tengo el sabado por la manana"` | `null` |

Ése es el resultado que importa: la ambición sólo aparece cuando el usuario ha
declarado su capacidad, y desaparece cuando no. El propio modelo lo razonó:

> Cuatro encargos, dos de los cuales parecen infraestimados, frente a una única
> mañana de sábado. **Es una lectura de lo que el usuario dice tener, no un
> cálculo de su calendario.**

> El usuario no declara cuántas horas tiene libres ni cómo está la semana; sólo
> indica fechas objetivo (jueves, viernes) y pide que se le reparta la semana.
> Sin una base de capacidad declarada no se puede juzgar.

En lo demás las dos coinciden, que es lo esperable:

- **Orden**: tutor → diapositivas → ensayo → ejercicios. El tutor primero
  porque todo depende de él y lleva una semana sin contestar; los ejercicios al
  final porque no bloquean nada.
- **Estimaciones**: las dos cortas detectadas, `looks_short`, **sin dar ninguna
  cifra propia**.
- **Coherencia**: «Ensayar el jueves requiere material que no estará listo hasta
  el viernes» — la contradicción que el usuario se había puesto a sí mismo.
- **Riesgos**: el tutor en `high`, con motivo.
- **Aviso**: «Se pide además repartir el trabajo en días y horas concretos. Este
  perfil no agenda: eso lo hace el planificador del cliente».
- **Prescripciones de agenda: ninguna. Aritmética de huecos: ninguna.**

### Otro error mío, del mismo tipo que en A04

La primera versión del verificador daba `REVISAR` porque encontraba «jueves» y
«viernes» en la salida. Estaba mal, y por la misma razón que en A04: esas
palabras aparecían en el problema de coherencia, **citando las fechas que había
puesto el usuario** para señalar que se contradicen. Eso no es agendar; es
exactamente el trabajo que A05 pide.

Agendar es **prescribir cuándo** hacer algo. El detector busca ahora eso
(«ponte», «dedica», «empieza el», una hora concreta) y no un día suelto.

Conviene ser honesto sobre el alcance de esa comprobación: es una heurística
sobre texto libre. **La garantía de verdad es estructural** — el contrato no
tiene ningún campo donde escribir una fecha, y hay una prueba que recorre el
esquema entero para comprobarlo.

## 7. Punta a punta: **hecha**

Con Agora **0.2.1** instalado en el PC de IA y los PROFILE al día, la tarjeta
`prueba-consejo-plan.md` se completó al primer intento:

```
- 2026-09-08 17:52 UTC  ai-1: Claimed CARD atomically and began admission.
- 2026-09-08 17:52 UTC  ai-1: Remote profile selected: planning-advisor.
- 2026-09-08 17:52 UTC  ai-1: AI_Broker task submitted: task_6e32ea0d...
- 2026-09-08 17:56 UTC  ai-1: Effective model: ollama/local/qwen3.8:27b.
- 2026-09-08 17:56 UTC  ai-1: Determinism policy: strict.
- 2026-09-08 17:56 UTC  ai-1: Verified artifact: ...\prueba-consejo-planinal.md
- 2026-09-08 17:56 UTC  ai-1: CARD closed.
```

El artefacto: **4682 caracteres, sin truncar**, contrato cumplido. La cifra
importa —el corte de §6 se produjo a los 4637— así que este artefacto es en sí
mismo la evidencia de que el arreglo del tope viajó hasta el runner.

| Comprobación | Resultado |
|---|---|
| Contrato `plan-advice` v1 | cumplido |
| `advice_only` | `true` |
| Orden | tutor → diapositivas → ensayo → ejercicios, con razón en las cuatro |
| Posiciones sin razón | **0** |
| `ambition` | `looks_ambitious`, base `"solo tengo el sabado por la manana"` |
| Comentarios a estimaciones | 2, ambos `looks_short` |
| Campos numéricos propios en esos comentarios | **ninguno** |
| Coherencia | detecta la contradicción jueves/viernes |
| Riesgos | tutor `high`, diapositivas `medium`, ejercicios `low` |
| Juicios que se autoconfirman | **0** |
| Prescripciones de agenda | **ninguna** |
| Aritmética de huecos | **ninguna** |

Y de nuevo el razonamiento del propio modelo sobre la línea que no cruza:

> Cuatro encargos, dos de los cuales parecen infraestimados, frente a una sola
> mañana declarada. **Es una lectura de lo que el propio usuario cuenta, no un
> cálculo de su calendario.**

### El vocabulario de `handles` importa, y me costó una vuelta

La primera tarjeta se quedó **veinte minutos en `pending` sin que nadie la
reclamara**, con el runner conectado y vivo. No era el runner: el tablero no
llegaba a emparejarla. Su `request` decía `advise on plan`, y los `handles` que
declara el perfil son `advise on priorities`, `advise on risk` y
`advise on sequencing`. `match_card` devolvía `no compatible handle matched`.

El emparejamiento es **por handle, no por `function`**. Un cliente que use una
frase que el PROFILE no declara no recibe un error: la tarjeta simplemente se
queda quieta. Conviene tenerlo presente al escribir el contrato de integración
del cliente (A07): la lista de `handles` es vocabulario público.

No se tocó el perfil para acomodar mi tarjeta; se corrigió la tarjeta.

## 8. Riesgos y deuda

- **El tablero de este PC sigue con Agora 0.2.0.** No construye peticiones al
  broker, así que el tope no le afecta, pero board y runner quedan en versiones
  distintas. Actualizarlo exige pararlo, y estaba en uso.
- **8000 tokens es un margen medido, no un límite razonado.** El corte real
  ocurrió en 4000 con un documento que necesitaba algo más de la mitad de 8000.
  Un encargo con doce ítems podría volver a rozarlo; entonces el sitio correcto
  sería declararlo por capacidad en `models.yml`.
- ~~`task-intake` (A02) y `task-decomposer` (A03) siguen en `standard`~~ —
  **cerrado en A06 §7**, después de que una tarjeta agotara sus tres intentos
  con tres modelos distintos y quedara bloqueada sin producir nada.
- **Una tarjeta con un `request` fuera del vocabulario de `handles` se queda
  quieta en `pending` sin decir por qué.** No es un fallo de esta fase, pero se
  topó aquí y es material para A07.
- **`sequence` no comprueba que las posiciones sean 1..n sin huecos ni
  repetidas.** Ninguna ejecución real lo ha fallado, pero el esquema no lo
  impide. Si aparece, el sitio es `enforce`.

## 9. Siguiente fase

A06 — contratos JSON estrictos y versionados. Es, en buena medida, la
formalización de lo que A03, A04 y A05 han ido construyendo por necesidad:
conviene empezarla releyendo `src/agora/output_contract.py`.
