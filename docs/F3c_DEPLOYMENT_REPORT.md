# F3c — Deuda cerrada y despliegue del runner en el PC IA

Fecha: 2026-09-04. Rama: `fix/broker-contract-defects`. Versión: **0.2.0**.

Cierra la deuda que quedaba abierta en `docs/AUDIT_20260903.md` y entrega el
paquete instalable del AI Runner. Todo lo que se afirma aquí se ha ejecutado:
la instalación se ensayó **desde el zip**, contra el AI_Broker vivo
(`192.168.1.52:8765`, contrato 2.10) y contra un tablero real por HTTPS.

---

## 1. Deuda cerrada

| Deuda (auditoría §4) | Estado | Cómo |
|---|---|---|
| 1. `model_capacity` era metadato muerto | **Cerrada** | `models.yml` traduce la capacidad del PROFILE a política; el runner la aplica por tarjeta |
| 2. «Fuga de ficheros» al esperar un adjunto | **Cerrada, y el diagnóstico corregido** | Ver §2 |
| 3. Artefactos de tarea ignorados | Cerrada en F3b | `/artifacts` con `final: true` |
| 4. Sin utillaje de certificados | **Cerrada** | `agora-certs`: CA privada, emisión, renovación y anclaje SPKI |
| 5. Nombre mutilado en la pasarela | Cerrada en F3b | |
| 6. `mypy --strict` nunca verde | **Cerrada** | 0 errores en 72 ficheros, por primera vez |
| 7. Desktop de sólo lectura | Abierta | Es alcance F7 |

### La fuga de ficheros no era una fuga

La auditoría afirmaba que cada sondeo `waiting_attachment` volvía a subir el
fichero «dejando `file_id` huérfanos indefinidamente». **Es falso, y se
comprobó subiendo los mismos bytes tres veces al broker real:**

```
subida 1: file_id=file_a8618f9665d84523b0255c029abeb19b created=True
subida 2: file_id=file_a8618f9665d84523b0255c029abeb19b created=False
subida 3: file_id=file_a8618f9665d84523b0255c029abeb19b created=False
```

El broker **deduplica por SHA-256** (`Client_API.md` §7) y devuelve el mismo
identificador. No hay huérfanos. El coste real era reenviar los bytes en cada
vuelta, y eso sí se ha corregido: `AiRunner` memoriza el `file_id` por digest
mientras la tarjeta está en curso. Un reinicio del runner cuesta una subida
más, no un fichero perdido — por eso no hace falta llevarlo al checkpoint de la
CARD, como pedía la auditoría.

### `model_capacity` deja de ser decorativo

`examples/f0-demo/AGENTS/models.yml` declara `minimum`, `standard` y `maximum`.
Un PROFILE con `model_capacity: maximum` produce política **estricta** sobre un
modelo exacto y contenido confidencial. Verificado en vivo
(`task_…`, perfil `auditor`):

```
policy       : strict   <-- lo puso models.yml, no el runner
served_by    : ollama/qwen3.8:27b
determinism  : {"verified_by": ["inv_4f3006b2…"], "deviations": []}
aux permitido: False
auxiliares   : []
```

Una capacidad que el catálogo no declara es un **error**, no una caída al
defecto: el PROFILE está pidiendo algo que este tablero no sabe servir.

### PKI interna

`agora-certs init-ca | issue | renew | show`. La clave de la CA se guarda
**cifrada** (frase de paso por entorno, nunca en la línea de órdenes); la del
servidor en claro, porque la lee `uvicorn` sin nadie delante y se reemplaza en
un minuto. `renew --reuse-key` conserva el anclaje SPKI; sin la bandera, rota.

---

## 2. Defectos encontrados **al ejecutar**, no al leer

Los cinco salieron del ensayo de instalación. Ninguno lo habría visto una
revisión de código.

### D5 — El certificado emitido no completaba el handshake

`[SSL: CERTIFICATE_VERIFY_FAILED] Missing Authority Key Identifier`. La cadena
era criptográficamente correcta, pero OpenSSL 3 exige el enlace *Authority Key
Identifier* en el certificado hoja. Añadidos AKI y SKI.
Regresión: `test_issued_certificate_completes_a_real_tls_handshake`, que hace un
handshake de verdad en vez de comprobar la estructura.

### D6 — El extra `runner` no declaraba lo que el runner importa

`ModuleNotFoundError: No module named 'pydantic'`. Los contratos del broker y
de la API son modelos de pydantic, que llegaba de rebote vía FastAPI.
Regresión: `test_the_runner_extra_declares_what_the_runner_imports`.

### D7 — Importar los contratos arrastraba el servidor entero

`ModuleNotFoundError: No module named 'fastapi'`, ya con pydantic instalado.
`agora/api/__init__.py` importaba `create_api` de forma ansiosa, así que el
runner —que sólo necesita `agora.api.contracts`— exigía FastAPI y uvicorn. La
exportación es ahora perezosa (PEP 562).
Regresión: `test_importing_the_wire_contracts_does_not_drag_in_the_board_server`.

### D8 — El anclaje SPKI se caía en la primera llamada

`TypeError: _SSLSocket.getpeercert() takes no keyword arguments`. El objeto que
expone httpx no es un `SSLSocket`: no acepta `binary_form=` por nombre.

### D9 — Dos tableros colisionaban en el broker (grave)

```
{"status":"failed","card":"task.md","detail":"AI_Broker 409: IDEMPOTENCY_CONFLICT"}
```

La clave de idempotencia se derivaba de `(fichero, intentos, perfil)`. **Dos
tableros distintos con una tarjeta del mismo nombre producen la misma clave**, y
el segundo no puede ejecutar nada: ni el de ensayo contra el de producción, ni
dos usuarios del mismo broker. El tablero tiene ahora identidad propia
(`.agora-instance`, creada al inicializar), viaja en el `WorkItem` y entra en la
clave.
Regresión: `test_two_boards_do_not_collide_on_the_brokers_idempotency_key`.

Además, el paquete no publicaba marcador **`py.typed`** (PEP 561), así que
`mypy --strict` veía a `agora` como una librería sin tipos desde sus propias
pruebas y no comprobaba nada de lo importado. Añadido.

---

## 3. El paquete del runner

```powershell
.\scripts\build-runner-package.ps1      # -> dist\agora-runner-0.2.0.zip
```

El zip lleva el wheel, `install-runner.ps1` e `INSTALAR.md`. Ningún secreto y
ningún certificado: esos se generan en el PC del tablero con `agora-certs`.

El instalador crea un venv aislado, instala `agora[runner]`, comprueba el
llavero, escribe `runner.cmd` y —con `-RegisterScheduledTask`— registra una
tarea que arranca **dos minutos después** del inicio de Windows. El retraso es
deliberado: tras un Wake-on-LAN el broker tarda en levantar y en publicar su
token, y arrancar antes sólo produce `403` en el log.

### `--check`: el diagnóstico antes de dejarlo solo

```
[OK  ] agora: version 0.2.0
[OK  ] credencial del broker: leída de keyring:ai-broker/session_admin_token
[OK  ] AI_Broker en loopback: contrato 2.10, con las garantías de 2.10
[OK  ] tablero de Agora: tablero accesible por TLS: {'status': 'ok', ...}
[OK  ] PROFILEs: 2 PROFILE en …\AGENTS; este runner sirve summarizer
[OK  ] catálogo de capacidades: …\models.yml (maximum, minimum, standard)

Todo listo: el runner puede reclamar tarjetas.
```

Un runner que arranca con la máquina falla de formas que nadie ve. Esto dice en
qué paso se rompe, en vez de dejar un `403` sin explicación.

---

## 4. Cómo se ensayó

No se pudo usar el PC IA como servidor, así que se reprodujo su **topología**
en el PC principal sin tocar el código:

- un reenviador TCP hace que `127.0.0.1:8765` sea el broker real del PC IA, de
  modo que `BrokerClient` sigue cumpliendo su restricción de loopback;
- el token de sesión se escribió en el Administrador de credenciales de Windows
  bajo `ai-broker` / `session_admin_token`, **los nombres del contrato**, y se
  borró al terminar;
- el tablero se levantó con `agora-api` sobre un certificado emitido por
  `agora-certs`, y el runner lo verificó contra la CA privada con anclaje SPKI.

Sobre eso se instaló el runner **desde el zip** y se ejecutaron tres tarjetas de
principio a fin: `standard`, `maximum` (estricta) y una tercera desde la
instalación limpia. Las tres cerraron en `done` con el artefacto real del
modelo y su `sha256` verificado.

```
{"status":"completed","card":"final.md","detail":""}
done: auditoria.md, final.md, task.md
```

Con esto, **la lectura del llavero deja de ser el hueco que declaraba F3b**: se
ha ejercitado contra el almacén real de Windows, con los nombres del contrato.
Lo que sigue sin probarse es que **el broker escriba** esa entrada al arrancar:
eso sólo se ve en el PC IA, y es la primera comprobación del despliegue.

---

## 5. Puertas

```
python -m pytest -q            ->  138 passed   (116 antes; 22 pruebas nuevas)
ruff check src tests           ->  All checks passed
mypy (strict, según pyproject) ->  Success: no issues found in 72 source files
```

**`mypy --strict` está verde por primera vez en el proyecto** (71 errores
históricos → 0). El cambio de fondo fue publicar `py.typed`; el resto son
anotaciones y comprobaciones reales en lugar de `cast`.

---

## 6. Lo que queda

1. **Ejecutar `INSTALAR_RUNNER.md` en el PC IA.** Es el único paso que no se
   puede ensayar desde aquí: que el broker publique el token al arrancar.
2. Cerrar las puertas humanas de **F4** y **F5**.
3. **F6 — A2A**, sobre la pasarela.
4. **F7 — Directors y explotación** (incluye el Desktop de escritura).
5. **F8 — Wake-on-LAN**, ya desbloqueada y con la tarea programada preparada.
