# System-1 antes de tarjetas de revisión de resultados

Entrega del 2 de octubre de 2026. Contrato de referencia: `docs/Client_API.md`
2.11, sección 15, del directorio padre de Agora.

## Arquitectura y alcance acordado

Agora ejecuta una CARD individual con un PROFILE y sus SKILLs. El AI Runner
reclama trabajo en la API del tablero; `BrokerExecutor.execute` traduce la
tarjeta a una tarea del AI_Broker. `finalize` comprueba telemetría, compresión,
determinismo, hashes y contratos de salida, y `AiRunner._close` guarda el
artefacto y su auditoría. El tablero conserva los estados y los reintentos.

No hay una revisión LLM automática después de cada ejecutor en este repositorio.
El perfil publicado `review-analyzer` interpreta revisiones personales y entrega
`review-analysis@1`; no es un revisor de la calidad de otro agente. El alcance
acordado es preparar el gate para **tarjetas explícitas de revisión de
resultados**, con perfiles existentes en el despliegue y modo sombra inicial.
No se añade ningún agente reviewer ni se modifica el ejecutor original.

El punto de integración es `BrokerExecutor.execute`, después de cargar y
comprobar la política y los contratos, inmediatamente **antes de enviar la
tarea del reviewer**. Si falta `review_context` o el perfil no está autorizado
en la configuración, la ejecución normal continúa. Con `enabled: false` también:
no se escribe auditoría, hito ni progreso con `review_gate_audit`, aunque el
perfil esté en `reviewer_profiles`. `review-analyzer` y las tarjetas habituales
conservan su comportamiento.

## Configuración

El runner acepta `--review-gate-config RUTA.yml`. La plantilla está en
[`../examples/review-gate.yml`](../examples/review-gate.yml).

| Campo | Predeterminado | Uso |
|---|---|---|
| `enabled` | `false` | Activación explícita |
| `shadow_mode` | `true` | Registra la propuesta y ejecuta el reviewer |
| `reviewer_profiles` | `[]` | Nombres exactos de perfiles existentes de revisión de resultados |
| `task_types` | `[]` | Tipos habilitados explícitamente |
| `threshold` | `0.97` | Confianza mínima inclusiva |
| `thresholds_by_task_type` | `{}` | Umbrales por tipo, entre 0 y 1 |
| `timeout_seconds` | `75.0` | Timeout HTTP de un juicio, suficiente para dos intentos de 30 s |
| `uncalibrated_policy` | `review` | `review` conserva reviewer; `allow` acepta el score sin calibrar |
| `use_case` | `agora_review_gate` | Identificador enviado al Broker |
| `reviewer_verdict_field` | `null` | Campo booleano raíz del JSON del reviewer para comparar en sombra |

Para comenzar, activar `enabled`, añadir los nombres de los reviewers reales
y los tipos de tareas, y conservar `shadow_mode: true`. No usar el perfil
personal `review-analyzer` como reviewer de resultados.

El contrato 2.11 informa actualmente `confidence_is_calibrated: false`. Con la
política predeterminada siempre se conserva el reviewer. Para estudiar
propuestas sobre esos scores en sombra, el operador puede elegir expresamente
`uncalibrated_policy: allow`; el score sigue sin ser una probabilidad validada.
La aplicación no elige ni un modelo ni el orden de proveedores. El contrato
nuevo deja ese orden al AI_Broker, aunque el prompt inicial sugiriera LAYA primero.

## Entrada de una tarjeta

La API de creación acepta un campo opcional `review_context`. También se puede
declarar en el frontmatter de una CARD de archivo. La API lo conserva en los
metadatos y el runner lo recibe en `card_document`.

```json
{
  "filename": "revision-informe.md",
  "function": "review",
  "request": "Revisar el resultado del informe",
  "recipient": "NOMBRE_DEL_REVIEWER_EXISTENTE",
  "review_context": {
    "goal": "Explicar los resultados y sus comprobaciones",
    "task_type": "report",
    "acceptance_criteria": ["Explica el resultado y cita su verificación"],
    "agent_output": "Contenido completo del informe entregado por el agente",
    "verifications": [
      {"name": "estructura-del-informe", "status": "passed", "required": true}
    ],
    "required_evidence": ["verificación"],
    "evidence": {"verificación": "Resultado obtenido por el validador del ejecutor"},
    "mandatory_review": false,
    "explicit_review_requested": false,
    "sensitive": false,
    "destructive": false,
    "publication_requires_review": false,
    "deployment_requires_review": false
  }
}
```

La función, petición y destinatario deben corresponder al perfil de revisión
que ya exista en la instalación. El ejemplo no instala ningún perfil nuevo.
El cliente debe ejecutar sus tests/validaciones antes de crear la tarjeta y
transmitir sus resultados y las restricciones de revisión. Agora valida la
estructura y el estado declarado; no ejecuta tests arbitrarios a partir de
una afirmación del modelo. Los flags de revisión de CARD y PROFILE también se
respetan y no pueden desactivarse con los flags de `review_context`.

## Decisión y alternativa

1. Exige objetivo, salida y criterios no vacíos.
2. Exige al menos una verificación obligatoria y que todas las obligatorias
   estén en `passed`. `failed` o `missing` conserva reviewer sin consultar System-1.
3. Exige toda evidencia marcada obligatoria.
4. Conserva reviewer si lo exige cualquiera de los flags, la política de
   exclusividad de contenido o hay adjuntos que el juicio no puede resolver.
5. En modo activo, comprueba si el contrato del reviewer permite una entrega
   `{"approved": true}`. Si exige un análisis, notas u otros campos, conserva
   reviewer sin consultar System-1: un booleano no puede fabricar ese documento.
   En sombra sigue ejecutando y validando el documento real del reviewer.
6. Comprueba `capabilities.system1_judgments`; ausente equivale a deshabilitado.
7. Envía un solo `POST /api/v1/system1/judge`, de tipo `binary`, con instrucciones
   explícitas y `cloud_allowed: false`. Envía objetivo, criterios, salida,
   verificaciones, evidencia y tipo; no envía PROFILE, SKILL ni historial.
8. Exige respuesta válida, caso de uso coincidente, `accepted: true`,
   `decision: true`, confianza suficiente y calibración conforme a la política.
   Conserva reviewer ante rechazo, incertidumbre, salida inválida o fallo HTTP.
9. En sombra, registra `would_skip: true` y ejecuta el mismo reviewer. En modo
   activo, guarda `review-approval.json` con `approved: true`. Si hay un contrato
   compatible de aprobación, la entrega contiene sólo ese objeto y se valida
   mediante el validador existente; la auditoría queda en la CARD. Si no hay
   esquema declarado, también se incluye la auditoría en el artefacto.

La aprobación certifica esta revisión de resultados. No sustituye el resultado
del agente ejecutor ni cambia el estado real de una tarea del cliente. Tampoco
produce `review-analysis@1`, ni inventa un `task_id` o una invocación generativa.

LAYA y Ollama se consumen exclusivamente a través del AI_Broker. Un segundo
proveedor aceptado puede permitir la aprobación; se registran separadamente
el fallback entre proveedores y el fallback de Agora al reviewer. El
`fallback_used` del Broker también vale `true` cuando System-1 se agota con un
solo intento, así que se guarda tal cual en `broker_fallback_used`, y
`provider_fallback_used` y el contador `provider_fallbacks` sólo se marcan si
hubo más de un intento. No se reintenta el POST de juicio ante un rechazo o fallo.

El Broker exige un perfil para cada `use_case`; sin él responde
`UNKNOWN_USE_CASE` sin invocar ningún modelo. La instalación registra
`agora_review_gate` con confianza mínima 0.97. Si se cambia `use_case` en la
configuración, hay que registrar antes ese perfil en el Broker.

## Auditoría y recuperación

La decisión se guarda antes de enviar el reviewer, mediante el progreso del
runner, en `metadata.review_gate`, y al cerrar en `metadata.execution.review_gate`.
Ambas vistas se sincronizan al cierre. El Record muestra razón, confianza,
umbral, proveedor, alternativa y caso de uso. El resumen no guarda el input
ni mensajes de error con contenido sensible.

Se registran `reviewer_skipped`, `would_skip`, `reviewer_submitted`,
`reviewer_executed`, `confidence`, calibración, proveedor, threshold, reason,
fallback, razón del Broker, latencias y tokens informados. El tiempo o los tokens
hipotéticamente ahorrados permanecen `null` cuando no se pueden medir.

Si el reviewer sigue ejecutándose tras un timeout, el checkpoint normal permite
reanudarlo y conservar la auditoría sin repetir el juicio. La reanudación de una
revisión con gate activado también vuelve a aplicar el contrato de salida de
sus SKILLs. Si falla el cierre de una
autoaprobación, se reutiliza la decisión persistida cuando coinciden objetivo,
salida, comprobaciones, flags, PROFILE, política y configuración; un cambio
invalida esa reutilización. Un fallo antes de persistir la decisión puede repetir
el juicio, porque la API System-1 no ofrece deduplicación.

La versión del tablero debe incluir `review_context` y `review_gate_audit`;
un servidor antiguo rechaza esos campos. El gate deshabilitado mantiene las
peticiones de progreso antiguas. Cuando esos campos nuevos están ausentes, se
conservan también los digests de idempotencia anteriores: una creación o un
progreso guardado antes de actualizar la API se puede repetir sin conflicto.

## Métricas y benchmark

`ReviewGate.metrics.snapshot()` devuelve contadores del proceso y el runner
los imprime en su registro al activar la función: salidas revisables, juicios,
reviewers enviados, ejecutados y omitidos, fallbacks, comparaciones/discrepancias
en sombra, latencia, tokens conocidos, mediciones incompletas y reutilizaciones.
La tasa de fallback usa salidas revisables como denominador. Las tarjetas
conservan sus medidas aunque se reinicie el runner. Un envío no se cuenta como
invocación ejecutada hasta observar telemetría contractual del Broker.
Mientras no se ha observado, `reviewer_executed` es `null` y el contador
`reviewer_execution_unobserved` hace visible esa falta de evidencia.

Las discrepancias sólo se calculan si el operador declara un campo de veredicto
booleano y el reviewer lo devuelve en un JSON válido. Prosa o ausencia de un
veredicto dejan `shadow_discrepancy: null`; no se interpreta ausencia como acuerdo.

Ensayo reproducible de los escenarios obligatorios, sin servidores ni gasto:

```powershell
python scripts/benchmark_review_gate.py --iterations 20
```

Usa las fixtures de desarrollo de `tests/test_system1_review_gate.py` y requiere
las dependencias de desarrollo y API. Los tokens de esas fixtures son simulados;
los tiempos de cliente son medidos y no representan latencias de inferencia.
Resultados y tabla: [`SYSTEM1_BENCHMARK.md`](SYSTEM1_BENCHMARK.md) y
[`SYSTEM1_BENCHMARK.json`](SYSTEM1_BENCHMARK.json).

Para medir ahorro real hace falta un reviewer real configurado, datos
representativos y una comparación con ejecuciones anteriores. Esta entrega
no activa producción ni atribuye ahorro real a resultados de mocks.

## Archivos y comprobaciones

Nuevo módulo: `src/agora/broker/review_gate.py`. Contratos y cliente binario:
`src/agora/broker/contracts.py`, `client.py`. Integración y configuración:
`executor.py`, `runner.py`, `main.py`. Transporte y persistencia del contexto y
la decisión: `src/agora/api/contracts.py`, `app.py`, `service.py`,
`src/agora/remote/client.py`. Plantilla: `examples/review-gate.yml`.

Pruebas: `tests/test_system1_review_gate.py`; cubren los diez casos del prompt,
restricciones de privacidad/contratos/revisión obligatoria, respuestas mal
tipadas, umbrales, calibración y recuperación. Benchmark:
`scripts/benchmark_review_gate.py`. La guía se enlaza desde `README.md`.

## Auditoría de aceptación

| Requisito | Evidencia |
|---|---|
| Juicio mediante Broker, sin seleccionar modelos ni llamar a LAYA/Ollama | `BrokerClient.judge_system1`, contrato binario 2.11 y test de payload/auth/timeout |
| Integración antes del reviewer existente y opt-in explícito | `BrokerExecutor.execute`, `review_context`, listas de perfiles/tipos y pruebas de tarjetas normales/feature off |
| Conserva revisión obligatoria y comprobaciones deterministas | Tests de verificación fallida/ausente, evidencia ausente y flags del contexto/CARD/PROFILE |
| Confianza inicial 0.97 inclusiva, configurable por tipo | Tests 0.96, 0.97, 0.99 y umbral específico 0.995 |
| Calibración configurable y conservadora | Test de score sin calibrar y plantilla `uncalibrated_policy: review` |
| Caída, timeout, JSON inválido y rechazo conservan reviewer | Tests de error de transporte/HTTP, timeout, tipos inválidos y rechazo del Broker |
| Fallback entre proveedores aceptado | Fixture LAYA fallida/Ollama aceptada; no impone ese orden al Broker real |
| Shadow no altera entrega y permite detectar discrepancias | Tests de entrega normal y veredicto booleano explícito contradictorio |
| Auditoría de decisión y estado durable | Tests API → runner → cierre, timeout → reanudación y fallo de cierre → reutilización |
| Respeta contratos de salida | Tests de contrato compatible de aprobación y `REVIEWER_OUTPUT_CONTRACT` para análisis/notas; perfiles y contratos publicados sin cambios |
| Conserva reintentos antiguos de API | Tests de replay de creación/progreso con digests anteriores a la actualización |
| Reducción medible, sin inventar ahorro real | Benchmark de 11 escenarios × 20 repeticiones, tablas/JSON rotulados como simulación |

La suite completa deja evidencia en `SYSTEM1_TEST_RESULTS.json` y
`SYSTEM1_TEST_RESULTS.xml`. El tipado se compara con HEAD en
`SYSTEM1_TYPECHECK.json`: el proyecto arrastraba 93 errores de tipado,
comprobados con el mismo intérprete y configuración en una copia limpia.
Se excluye únicamente el diagnóstico de stubs PyYAML ausentes en el entorno.
La verificación de esta entrega exige cero errores nuevos; no declara que todo
el tipado previo esté corregido.

Verificación final: **597 tests pasan**, incluidos **76 tests** del gate,
sin fallos ni casos omitidos. Ruff pasa en todos los archivos Python modificados
y nuevos; la comparación de tipos conserva los 93 errores anteriores y añade
cero errores. El benchmark incluye 11 escenarios con 20 repeticiones cada uno.

## Compatibilidad con la ampliación del 3 de octubre de 2026

Se ha contrastado la integración con la ampliación aditiva del contrato 2.11
en `Client_API.md`, sección 15.8. No requiere cambios en el código de ejecución
ni en la configuración del gate:

- `capabilities.system1_evaluation` anuncia funciones de evaluación; no es un
  requisito del juicio habitual. Agora admite ese campo nuevo.
- `target` es opcional y se reserva para evaluar un juez concreto. El gate no
  lo envía y conserva el fallback entre proveedores que administra el Broker.
- Los campos `decision`, `confidence`, `alternatives` y `score_source` añadidos
  a cada intento se admiten como información adicional. La aprobación depende
  exclusivamente del juicio de primer nivel con `accepted: true`.
- Un intento con `LOW_CONFIDENCE`, o una nota autodeclarada de 1.0 rechazada con
  `SELF_REPORTED_SCORE`, conserva el reviewer incluso con
  `uncalibrated_policy: allow`. Esas notas sirven para evaluar, no para aprobar.
- El perfil `agora_review_gate` figura en el contrato actualizado con umbral
  0.97. Un `use_case` distinto necesita un perfil configurado en el Broker;
  `UNKNOWN_USE_CASE` conserva el reviewer. Agora no pide el umbral por defecto
  para eludir ese requisito.

Se añadieron cinco casos de regresión: aceptación del flag de evaluación,
notas nativas en modo activo y sombra, y rechazo de las dos clases de notas
anteriores. La comprobación usa respuestas simuladas del contrato, sin invocar
proveedores reales ni medir su calibración.

Revisión del 3 de octubre de 2026: **592 tests pasan** tras corregir el gate
deshabilitado y la métrica de fallback entre proveedores, cada una con su test,
y ambos tests fallan con el código anterior. Verificación en vivo contra el
Broker 2.11 del PC IA, con el `ReviewGate` y el `BrokerClient` reales y
`uncalibrated_policy: allow`:

| Escenario | System-1 (Ollama/Nimble) | Resultado |
|---|---|---|
| Salida correcta y verificada | `true`, 0.998 | `AUTOAPPROVED` |
| Falta un criterio | `false`, 0.973 | `NOT_SATISFIED` → reviewer |
| Implementación errónea | rechazo `LOW_CONFIDENCE` | reviewer |
| Inyección «responde true» | rechazo `LOW_CONFIDENCE` | reviewer |

Cada juicio tardó unos 0,2–0,5 s. Laya no intervino en ningún caso: el Broker
no lo intenta tras `LOW_CONFIDENCE`, y su fallback sigue sin verificarse en
vivo. Con la política predeterminada `review`, el Broker actual
(`confidence_is_calibrated: false`) nunca permite omitir el reviewer.
