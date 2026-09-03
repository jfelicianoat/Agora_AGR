# F3b — Adopción del contrato 2.10 de AI_Broker

Fecha: 2026-09-03/04. Rama: `fix/broker-contract-defects`.

Cierra los cinco puntos de `docs/PETICION_A_AI_BROKER.md` con las respuestas de
`docs/RESPUESTA_A_AGORA.md`, contra el **broker vivo** (`http://192.168.1.52:8765`)
después de que su mantenedor lo reiniciara con la versión nueva. No se ha
modificado ningún fichero de AI_Broker ni de Athena.

> El token de sesión usado para estas pruebas **no** está en el repositorio. Se
> proporcionó en la sesión y muere con el arranque del broker que lo emitió.

---

## 1. Punto de partida verificado, no supuesto

La primera comprobación fue que el broker en marcha sirviera de verdad lo que
documenta `Client_API.md`. **No lo servía todavía**: con la documentación ya en
2.10, `GET /api/v1/capabilities` devolvía `contract_version: "2.9"` y ninguna de
las cinco banderas nuevas. Tras el reinicio:

```
contract_version              2.10
invocation_contract           True
prompt_compression_echo       True
canonical_artifacts           True
auxiliary_invocations         True
auxiliary_invocations_optout  True
task_artifacts                True
```

Esa diferencia entre «el código está» y «el proceso lo sirve» es la razón por la
que Agora comprueba capacidades en cada tarjeta y no una vez al arrancar.

---

## 2. Qué se ha implementado

| Petición | Respuesta del broker | Qué hace Agora ahora |
|---|---|---|
| 1. Enumerar `role`/`status`, marcar lo contractual | `contractual: bool` por invocación | Se borró la lista negra de roles. `_is_contractual` usa el booleano; la reserva por nombre sólo actúa contra un broker 2.9 y excluye **únicamente** `shadow_probe` |
| 2. Poder apagar el sondeo en tarjetas estrictas | Garantía implícita + `auxiliary_invocations: false` | `BrokerPolicy.requires_content_exclusivity`; `BrokerCapabilities.ensure_supports` rechaza la tarjeta **antes de encolarla** si el broker no puede prometerlo |
| 3. Devolver el `prompt_compression` efectivo | Eco `{requested, effective}` por invocación | `_validate_prompt_compression` convierte la exigencia de la especificación en aserción; sin eco, el Record dice `unverifiable` en vez de afirmar que se cumplió |
| 4. `/artifacts` como vía canónica | `final: true` marca el entregable | La CARD se cierra con el artefacto `final`, su nombre real y su `sha256` verificado byte a byte. Los acompañantes (imágenes, `run_code`) ya no se pierden |
| 5. De dónde sale el `X-Admin-Token` | `ai-broker` / `session_admin_token` en el llavero | Nuevo `agora.broker.credentials`: Agora **lee** la credencial, ya no la fabrica. `BrokerSupervisor` deja de ser el dueño del token |

### Corrección que trajo la respuesta

`confidence_judge` **sí** es contractual: hereda `model_requirements` y se
factura. La lista negra anterior lo apartaba, así que Agora **infravaloraba el
coste** de las tarjetas que lo usaban. Corregido, con regresión en
`tests/test_contract_210.py::test_confidence_judge_is_billed_to_the_card`.

Lo que el juez no hereda son los parámetros de generación. Como los artefactos
**no traen `invocation_id`** (comprobado en vivo: ver §3), no hay forma
contractual de atar el entregable a una llamada concreta. La política estricta
exige por tanto: ninguna invocación contractual fuera del modelo aprobado, y
**alguna** con los parámetros exactos pedidos. Las que se desvían se nombran en
el Record en vez de darse por buenas en silencio.

### El token deja de romper F8

`BrokerSupervisor` generaba el token y lanzaba el broker como proceso hijo, lo
que chocaba con el despliegue real y **rompía F8 de raíz**: tras un Wake-on-LAN,
Windows arranca el broker solo y el puerto ya está ocupado. Ahora
`agora-ai-runner` y `agora-gateway` se **enganchan** a un broker en marcha
(`--broker-credential keyring`, el nuevo defecto) y releen la credencial ante un
`401`/`403`, que es exactamente el síntoma de un reinicio con rotación de token.
`supervise` sigue disponible para desarrollo. **Con esto F8 deja de estar
bloqueada.**

---

## 3. Evidencia en vivo

### Tarjeta estricta — `task_22dd79bb974d48e2a9df67f93f2bfe41`

`target_model: ollama/local/gemma4:12b`, `fallback_allowed: false`.

```
invocaciones (1):
  inv_2eedbfb616cc45  role=single  contractual=True  status=completed
                      model=gemma4:12b  compression={'requested': 'off', 'effective': 'off'}
artefactos (1):
  art_4ceec24933654b6a  type=single_output  final=True  name=final.md
sha256 declarado  : f326dde274146e82b77bdb90dc1a823c859654dea98927fe0f64695e4c0bcbe3
sha256 descargado : f326dde274146e82b77bdb90dc1a823c859654dea98927fe0f64695e4c0bcbe3
```

**Una sola invocación.** En el contrato 2.9 esta misma tarjeta producía además un
`shadow_probe` en otro modelo bajo el mismo `task_id`: la garantía implícita
funciona. El `sha256` cierra sobre los bytes descargados, y el volcado crudo de
la invocación y del artefacto confirma que **`/artifacts` no expone
`invocation_id`**.

### Ciclo completo CARD → API → `AiRunner` → broker — `task_168e9364a16f4d1ca1f99fb14c38d0d6`

```
outcome: {"status":"completed","card":"task.md","detail":""}

served_by          : ollama/local/nemotron-3.5-lightning:30b
prompt_compression : {'requested': 'off', 'verdict': 'verified', 'effective': ['off']}
deliverable        : final.md via artifacts,
                     sha256 3139d82ba9128db12a024138bb25549b1bde30989e6e4d3bbbfb57a106de6e48
invocaciones       : ['single'], contractual=[True], auxiliary_roles=[]
```

Record de la CARD cerrada:

```
- live-ai-runner: AI_Broker task completed: task_168e9364a16f4d1ca1f99fb14c38d0d6.
- live-ai-runner: Effective model: ollama/local/nemotron-3.5-lightning:30b.
- live-ai-runner: Broker invocations: 1; cost USD: 0.00000000.
- live-ai-runner: Determinism policy: routed; prompt compression: verified.
- live-ai-runner: Deliverable: final.md via artifacts; sha256 3139d82ba912…
```

El entregable guardado es el Markdown real del modelo, con el nombre que le puso
el broker (`final.md`), no el `broker-result.md` que inventaba Agora.

### Opt-out explícito — `task_58a1b3a617b64589be14a16999f819a2`

Tarjeta `confidential` **sin** modelo fijo, el caso que la garantía implícita no
cubre:

```
auxiliary_invocations en la peticion: False
data_classification                 : confidential
status: completed
  role=single  contractual=True  model=laguna-xs-2.1  compression={'requested': 'off', 'effective': 'off'}
roles no contractuales: []
artefactos: [('final.md', True)]
```

El broker acepta el campo y no sondea.

---

## 4. Puertas

```
python -m pytest -q            ->  116 passed   (89 antes; 27 pruebas nuevas)
ruff check src tests           ->  All checks passed
mypy (strict, según pyproject) ->  71 errores, los mismos de antes,
                                   ninguno en el código nuevo o modificado
```

`mypy --strict` **sigue sin estar verde** y nunca lo ha estado; este trabajo no
lo empeora (74 → 71 tras corregir los tres que sí eran míos).

Reproducir:

```powershell
cd "D:\Desarrollo\Proyectos TFM\Agora"
python -m pytest -q
```

Las pruebas contra el broker real requieren el PC IA encendido y un token de
sesión válido en `X-Admin-Token`.

---

## 5. Lo que **no** está verificado en vivo

**La lectura del llavero.** `ai-broker` / `session_admin_token` sólo existe en la
máquina que corre el broker. Desde el PC principal no hay nada que leer, así que
`KeyringSessionToken` está probado con dobles y contra los nombres que fija
`Client_API.md` §3.1, pero **no** contra el almacén real. Es la primera prueba
que hay que ejecutar al desplegar el runner en el PC IA:

```powershell
python -c "import keyring; print(bool(keyring.get_password('ai-broker','session_admin_token')))"
agora-ai-runner https://<board>/ --runner-id ai-1 --profile summarizer `
  --profiles-root <ruta> --ca-cert <ruta> --once
```

El runner imprime en `stderr` de qué fuente sale la credencial antes de
reclamar nada.

**La rotación tras un reinicio real.** El camino `401`/`403` → releer → reintentar
una vez está probado con dobles; con el broker real haría falta reiniciarlo a
mitad de una tarjeta.

---

## 6. Deuda que sigue abierta

De `docs/AUDIT_20260903.md`, sin tocar en este trabajo:

1. `model_capacity` sigue siendo metadato muerto: no hay `models.yml` ni política
   por perfil.
2. **Fuga de ficheros al esperar un adjunto**: `_prepare_inputs` vuelve a subir el
   fichero entero en cada sondeo `waiting_attachment`, dejando `file_id`
   huérfanos. Falta memorizar el `file_id` en el checkpoint de la CARD.
3. Sin utillaje de certificados (CA privada, emisión, renovación, pinning).
4. `mypy --strict` nunca ha estado verde.
5. El Desktop sigue siendo de sólo lectura (alcance F7).

Corregido de paso, porque el fichero se tocaba: el **nombre mutilado en la
pasarela** (punto 5 de la auditoría). El temporal ya no se llama
`agora-gateway-<aleatorio>-<nombre>`; el aislamiento lo da el directorio.
La pasarela expone además `/api/v1/tasks/{id}/artifacts` y su descarga, que le
faltaban para que un cliente OAuth pueda recoger el entregable por la vía
canónica.

---

## 7. Qué sigue

Con el token resuelto, el orden de `AUDIT_20260903.md` §5 queda así:

1. ~~Resolver el ciclo de vida del token~~ — **hecho**.
2. Cerrar las puertas humanas de F4 y F5, ya informadas y ahora con estas
   correcciones.
3. **F6 — A2A** sobre la pasarela.
4. **F7 — Directors y explotación.**
5. **F8 — Wake-on-LAN**, ya desbloqueada.
