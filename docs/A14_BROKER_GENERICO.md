# A14 — Mantener AI_Broker genérico

Entrega del prompt
`Gestion Tareas IA/docs/Cambios en la app/gestion_tareas_ia_prompts/03_agora/A14_mantener_ai_broker_generico.md`.

## 1. Resumen

Trece fases han añadido cuatro capacidades a Agora: entrevistar un encargo mal
definido, partirlo en pasos verificables, leer una revisión separando hechos de
deducciones, y aconsejar sobre orden y riesgo.

**Al AI_Broker no se le ha cambiado nada.** Ni un tipo de operación nuevo, ni un
campo, ni un modo. Las cuatro viajan por la misma petición genérica de chat que
Agora ya construía, y esta fase lo demuestra en vez de afirmarlo.

Evidencia contra el broker real: las cuatro enviadas, las cuatro completadas,
las cuatro cumpliendo su contrato (§4).

## 2. Archivos

Creados:

- `tests/test_broker_stays_generic.py` — 15 pruebas.
- `scripts/verify_a14_broker_generic.py` — las cuatro contra el broker real.

Sin cambios en `src/`. Que esta fase no necesite tocar código es, precisamente,
su resultado.

## 3. Qué se comprueba

### Un solo tipo de operación

Las cuatro capacidades son `inference_kind: "chat"`. Hay una prueba que lo
comprueba capacidad por capacidad, con el comentario que explica por qué existe:
si alguien añade un día `inference_kind: "decompose"`, esa prueba lo caza antes
de que llegue a una petición de cambio al broker.

### Una sola forma de sobre

Las cuatro peticiones tienen exactamente los mismos campos de primer nivel, la
misma sección `content`, y la misma sección `output`. Se normalizan las tres
cosas que legítimamente cambian —el prompt, la etiqueta del perfil y el
`request_id`— y **lo que queda es idéntico**, byte a byte, entre las cuatro.

### La capacidad entera cabe en el prompt

PROFILE, SKILLs, CARD y la exigencia del contrato viajan **dentro del texto**.
No hay ningún campo del broker que Agora esté usando para expresar algo que el
broker tendría que entender.

### El broker no aprende el vocabulario del cliente

El sobre no contiene `calendar`, `workblock`, `pomodoro`, `availability`, ni los
nombres de los contratos (`work-breakdown`, `review-analysis`, `task-brief`,
`plan-advice`).

Lo de los contratos es deliberado y merece decirse: **son un acuerdo entre Agora
y su cliente**. Si viajaran en el sobre, el broker acabaría teniendo opiniones
sobre ellos —enrutando por contrato, midiendo por contrato— y dejaría de ser
genérico sin que nadie hubiera cambiado su código.

Lo que **sí** viaja es el nombre del perfil, en `metadata.profile` y en
`request_id`. Es procedencia, no significado: al broker le da igual que
`task-decomposer` descomponga, igual que a un servidor de correo le da igual de
qué habla un mensaje. Tiene su propia prueba de que se trata como etiqueta
opaca.

### `output` no pide nada especial

`{"format": "markdown", "language": "es"}` y nada más. Ni modo JSON ni
`json_schema`. Es la lección de A03, y la razón por la que el contrato lo hace
cumplir Agora sobre el texto que llega, en vez de pedírselo al broker.

## 4. Resultados reales

`scripts/verify_a14_broker_generic.py`, contra el AI_Broker real:

```
=== 1. La misma forma de peticion para las cuatro capacidades ===
  [ok] un solo tipo de operacion — {'chat'}
  [ok] una sola forma de sobre — 1
  [ok] una sola seccion de salida — {"format": "markdown", "language": "es"}
    lo unico que cambia es el prompt: {'task-intake': 7299,
      'task-decomposer': 15207, 'review-analyzer': 9854, 'planning-advisor': 10559}

=== 2. El broker acepta las cuatro sin nada especial ===
  [ok] task-intake: aceptada — task_7836df8bcf6d437aa14
  [ok] task-decomposer: aceptada — task_158428709ad846019e8
  [ok] review-analyzer: aceptada — task_6d2ef9d961b248c8a01
  [ok] planning-advisor: aceptada — task_d81dc88a88d040aea30

=== 3. Y las cuatro producen su contrato ===
  [ok] task-intake: cumple su contrato — task-brief@1
  [ok] task-decomposer: cumple su contrato — work-breakdown@1
  [ok] review-analyzer: cumple su contrato — review-analysis@1
  [ok] planning-advisor: cumple su contrato — plan-advice@1

=== 4. El broker no ha aprendido nada del cliente ===
  [ok] las cuatro: el sobre es generico — []

VEREDICTO A14: CORRECTO
```

Los cuatro tamaños de prompt son la evidencia más clara de la fase: **lo único
que distingue una capacidad de otra es cuánto texto lleva dentro**. El sobre es
el mismo.

## 5. La única carencia contractual encontrada en trece fases

La TAREA decía «solo proponer cambios al Broker si aparece una carencia
contractual demostrable». Apareció una, en A03, y **no requirió cambiar el
broker**. Queda aquí documentada porque es el caso que la fase pedía evaluar.

**El hallazgo.** El contrato 2.10 acepta `output.format: json` con un
`json_schema`, pero **no lo impone**: en una ejecución real el modelo devolvió un
CSV, y en otra un JSON con otra forma. Peor: al enrutar a LM Studio, el proveedor
rechaza la petición con `'response_format.type' must be 'json_schema' or 'text'`,
HTTP 400, y el broker lo marca **no reintentable**, así que la tarjeta muere.

**Lo que se habría podido pedir.** «Que el broker imponga el `json_schema`.»

**Por qué no se pidió.** Porque imponer un esquema de salida no es trabajo de un
enrutador de modelos: exigiría que el broker validara documentos, decidiera qué
hacer con los que no cumplen y conociera la forma de lo que sus clientes
esperan. Es exactamente el tipo de conocimiento de cliente que lo dejaría de
hacer genérico.

**Lo que se hizo en su lugar**, todo del lado de Agora:

- un **ejemplo relleno** al final del prompt, que es lo que el modelo copia
  (poner el esquema entero hacía que devolviera *el esquema*);
- los **vocabularios cerrados** —solo los `enum`— para lo que un ejemplo no
  puede enseñar (A06);
- **validar la respuesta** con `agora/output_contract.py` antes de escribir el
  artefacto.

Y funcionó mejor que lo que se habría pedido. En A06 un modelo corrompió los
nombres de campo a mitad de generación con caracteres cirílicos; el broker dio la
tarea por **completada** y fue la validación de Agora la que lo rechazó. Un
esquema impuesto en el broker habría dependido del mismo proveedor que estaba
fallando.

**Conclusión: cero cambios propuestos al AI_Broker.**

## 6. Pruebas añadidas

`tests/test_broker_stays_generic.py`, 15 pruebas:

- **Un solo tipo de operación** (5): las cuatro son `chat`; ninguna inventó uno;
  las cuatro peticiones tienen la misma forma; la sección `content` también;
  normalizando prompt, perfil e id, **lo demás es idéntico**.
- **`output` sin nada especial** (1): markdown, sin `json_schema`.
- **El broker no aprende el dominio** (4): el sobre no lleva conceptos de
  cliente; los nombres de contrato tampoco; la metadata dice `agora-atomic`; el
  perfil es una etiqueta opaca.
- **Todo cabe en el prompt** (3): PROFILE, SKILLs, CARD y contrato van en el
  texto; el contrato se pide con palabras, no con un campo; una capacidad sin
  contrato manda el mismo sobre.
- **Ningún campo inventado** (2): el conjunto de claves está dentro de lo que
  define el contrato del broker; **cualquier** perfil del repositorio produce una
  petición de chat, no solo los cuatro de la serie.

Suite completa: **509 pruebas, todas en verde** (eran 494 al cerrar A13).

## 7. Limitaciones declaradas

Ninguna relevante. La TAREA admitía dobles («pruebas end-to-end Agora→Broker con
doubles/servidor de prueba»), pero el broker real estaba disponible y se usó:
las cuatro capacidades se enviaron y completaron contra él. Los dobles siguen
cubriendo lo que el broker real no puede reproducir a demanda —fallos, timeouts,
zombies— en `tests/test_broker.py` y `tests/test_recovery.py`.

## 8. Riesgos y deuda

- **La guardia es sobre lo que Agora construye, no sobre lo que el broker
  acepta.** Si el broker añadiera un tipo de operación específico por su cuenta,
  estas pruebas no lo verían. Lo que garantizan es que Agora no se lo pide.
- **El runner del PC de IA sigue en Agora 0.2.2**, siete versiones por detrás
  del tablero. Nada de A07–A14 le afecta —son todos cambios del lado del
  tablero— pero conviene igualarlo. Es la única deuda de despliegue abierta de
  toda la serie.
- **Las tarjetas de muestra `a11-fallada.md` y `a11-parada.md`** siguen en
  `blocked` en el tablero real, a propósito, como evidencia de A11.

## 9. La serie, cerrada

A14 es la última de las catorce fases de Agora. Las cuatro capacidades están
construidas, contratadas, versionadas, verificadas contra el broker real y
cerradas de punta a punta por el tablero, con la frontera arquitectónica escrita
en un ADR y comprobada por el código.

Lo siguiente son las fases de la aplicación cliente (F10–F22) y de su interfaz
(UI01–UI07).
