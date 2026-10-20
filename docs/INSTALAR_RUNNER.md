# Instalar el AI Runner de Agora en el PC IA

El runner es la única pieza de Agora que vive en el PC IA. Se **engancha** a un
AI_Broker que ya está en marcha y lee su credencial del Administrador de
credenciales de Windows. No lanza el broker ni fabrica su token: de eso depende
que siga funcionando tras un Wake-on-LAN, cuando Windows arranca el broker por
su cuenta y el puerto 8765 ya está ocupado.

```
   PC del tablero (192.168.1.50)              PC IA (192.168.1.52)
   ┌────────────────────────────┐             ┌────────────────────────────┐
   │  KANBAN + agora-api (TLS)  │◀── HTTPS ───│  agora-ai-runner           │
   │  agora-desktop             │   Bearer    │        │ loopback          │
   │  agora-certs (CA privada)  │             │        ▼ X-Admin-Token     │
   └────────────────────────────┘             │  AI_Broker :8765           │
                                              └────────────────────────────┘
```

El token de administración del broker **nunca cruza la red**: el runner lo lee
del llavero local. Lo que cruza es el token del tablero, en sentido contrario.

---

## Antes de empezar

En el **PC IA** hace falta:

- **Python 3.11 o superior** en el `PATH` (`python --version`).
- **AI_Broker en marcha** en `127.0.0.1:8765`, con contrato **2.10**.
- En su `broker_config.yaml`, la línea que publica el token de sesión:

  ```yaml
  server:
    publish_session_token: keyring
  ```

  Se escribe **al arrancar**, así que hay que reiniciar el broker después de
  añadirla. Sin esto no hay credencial local que leer y el runner no arranca.

Del **PC del tablero** hay que llevarse tres cosas:

| Qué | De dónde sale |
|---|---|
| `ca.crt` | `agora-certs init-ca` (ver abajo) |
| El token del tablero | El que sirve `agora-api` en `AGORA_API_TOKEN` |
| El anclaje SPKI (opcional) | `agora-certs show server.crt` |

---

## Paso 0 — En el PC del tablero: certificados

`agora-api` sirve sólo por HTTPS, y el runner verifica contra una CA privada.
Si aún no existe:

```powershell
$env:AGORA_CA_PASSPHRASE = "<una frase larga, mínimo 12 caracteres>"

agora-certs init-ca --ca-dir C:\Agora\pki
# El certificado del tablero DEBE cubrir la IP o el nombre por el que el
# runner llegará: si el runner usa https://192.168.1.50:8741, esa IP va aquí.
agora-certs issue --ca-dir C:\Agora\pki --out C:\Agora\pki `
                  --host 192.168.1.50 --host tablero.local
agora-certs show C:\Agora\pki\server.crt
```

La última orden imprime el `--pin-spki` que puede pasarse al runner. La clave
privada de la CA **no sale de esta máquina**; al PC IA sólo viaja `ca.crt`.

Arranca el tablero:

```powershell
$env:AGORA_API_TOKEN = "<token del tablero>"
agora-api C:\Agora\workspace --host 0.0.0.0 --port 8741 `
          --cert C:\Agora\pki\server.crt --key C:\Agora\pki\server.key
```

Para renovar antes de que caduque, conservando el anclaje:

```powershell
agora-certs renew --ca-dir C:\Agora\pki --out C:\Agora\pki `
                  --host 192.168.1.50 --host tablero.local --reuse-key
```

---

## Paso 1 — En el PC IA: instalar

Copia el zip, descomprímelo y ejecuta:

```powershell
cd <carpeta descomprimida>
.\install-runner.ps1 `
    -BoardUrl https://192.168.1.50:8741 `
    -RunnerId ai-1 `
    -Profile summarizer `
    -ProfilesRoot C:\Agora\AGENTS `
    -CaCert C:\Agora\ca.crt `
    -PinSpki "<lo que imprimió agora-certs show>"
```

Crea un venv aislado en `%LOCALAPPDATA%\Agora\runner`, instala el paquete y
escribe `runner.cmd` con esa configuración. No guarda ningún secreto.

Los PROFILE de `-ProfilesRoot` deben ser **los mismos** que los del tablero: el
runner compara la `function` del PROFILE local con la de la CARD y rechaza la
tarjeta si difieren. Si hay un `models.yml` en esa carpeta, se carga solo.

---

## Paso 2 — Comprobar antes de dejarlo solo

```powershell
$env:AGORA_API_TOKEN = "<token del tablero>"
%LOCALAPPDATA%\Agora\runner\runner.cmd --check
```

Comprueba, en orden, la credencial del broker, el contrato que sirve, el acceso
TLS al tablero, los PROFILE y el catálogo. Salida esperada:

```
[OK  ] agora: version 0.2.0
[OK  ] credencial del broker: leída de keyring:ai-broker/session_admin_token
[OK  ] AI_Broker en loopback: contrato 2.10, con las garantías de 2.10
[OK  ] tablero de Agora: tablero accesible por TLS: {...}
[OK  ] PROFILEs: 1 PROFILE en C:\Agora\AGENTS; este runner sirve summarizer
[OK  ] catálogo de capacidades: C:\Agora\AGENTS\models.yml (maximum, minimum, standard)

Todo listo: el runner puede reclamar tarjetas.
```

Qué significa cada fallo:

| Falla | Casi siempre es |
|---|---|
| credencial del broker | falta `publish_session_token: keyring`, o el broker no se ha reiniciado desde que se añadió |
| AI_Broker en loopback | el broker no está arrancado, o escucha en otro puerto (`--broker-port`) |
| «SIN canonical_artifacts…» | el broker corre una versión anterior a 2.10; las tarjetas estrictas se rechazarán |
| tablero de Agora | el certificado no cubre la IP que usa `-BoardUrl`, o `ca.crt` no es la CA que lo firmó |
| PROFILEs | `-ProfilesRoot` apunta a otra carpeta, o falta el PROFILE que sirve este runner |

---

## Paso 3 — Ejecutar

Una vuelta, para ver una tarjeta de principio a fin:

```powershell
%LOCALAPPDATA%\Agora\runner\runner.cmd --once
```

En bucle, hasta Ctrl-C:

```powershell
%LOCALAPPDATA%\Agora\runner\runner.cmd
```

Cada vuelta imprime una línea JSON con el resultado: `completed`, `idle`,
`waiting_attachment`, `broker_running`, `contract_unsupported`…

---

## Paso 4 — Arrancar con la máquina (necesario para F8)

```powershell
$env:AGORA_API_TOKEN = "<token del tablero>"
.\install-runner.ps1 -BoardUrl ... -RegisterScheduledTask
```

Registra una tarea que arranca **dos minutos después** del inicio de sesión de
Windows. El retraso es deliberado: tras un Wake-on-LAN, el broker tarda en
levantar y en publicar su token de sesión, y arrancar antes sólo produce `403`
en el log. Si aun así llega antes, el runner relee el llavero y reintenta.

El token del tablero queda en `runner-task.cmd`, con la ACL restringida al
usuario que corre la tarea. Si prefieres no dejarlo en disco, no uses
`-RegisterScheduledTask` y arranca el runner a mano.

---

## AI OS Core (opcional)

Con AI OS Core el runner pregunta a Core antes de reclamar cada tarjeta (política
`agora:broker`) y cada tarea se ve en vivo en el monitor de Core (`<core>/monitor`),
continuando el flujo de la app que dejó la tarjeta (p. ej. Gestión Tareas IA → Agora →
Broker). Si Core no contesta, el runner no reclama nada.

1. En el PC del tablero, Core tiene que escuchar en la red (no sólo en `127.0.0.1`) y,
   por eso mismo, con tokens: `AI_OS_API_TOKEN` (director) y en `AI_OS_API_TOKENS` uno
   para el runner, `agora=<token>`. Arráncalo con `--host 0.0.0.0` y abre el puerto 8000
   en el firewall de Windows sólo para la red local.
2. En el PC IA, instala con `-CoreUrl` y deja el token en `AGORA_CORE_TOKEN`:

   ```powershell
   $env:AGORA_API_TOKEN  = "<token del tablero>"
   $env:AGORA_CORE_TOKEN = "<token agora de Core>"
   .\install-runner.ps1 -BoardUrl https://192.168.1.50:8741 -RunnerId ai-1 `
       -Profile summarizer -ProfilesRoot C:\Agora\AGENTS -CaCert C:\Agora\ca.crt `
       -CoreUrl http://192.168.1.50:8000 -RegisterScheduledTask
   ```

   Con `-RegisterScheduledTask`, `AGORA_CORE_TOKEN` queda también en `runner-task.cmd`
   (con la misma ACL que el token del tablero).

---

## Actualizar

**Siempre igual, dos pasos:** copia al PC IA el fichero
**`Actualizar runner Agora <versión>.bat`** (sale en `dist\` al construir el paquete) y haz
doble clic. Lleva el paquete dentro: no hay que descomprimir nada. Si la tarea programada
guarda los tokens, también hace el `--check` sin preguntar.

Equivale a descomprimir el zip y hacer doble clic en **`Actualizar runner.bat`**. Lee la configuración del `runner.cmd` que ya tienes
(tablero, id, PROFILEs, CA, anclaje, catálogo, AI OS Core…), para la tarea programada si
existe, reinstala con el wheel nuevo, la vuelve a arrancar y te enseña la versión. Si antes
defines `AGORA_API_TOKEN` (token del tablero) en esa consola, también pasa `--check`.

A mano, lo mismo:

Vuelve a ejecutar `install-runner.ps1` con el wheel nuevo: reinstala sobre el
mismo venv y reescribe `runner.cmd`. La tarea programada no hace falta
registrarla otra vez.

```powershell
%LOCALAPPDATA%\Agora\runner\runner.cmd --version
```
