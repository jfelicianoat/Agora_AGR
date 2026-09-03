<#
.SYNOPSIS
    Instala el AI Runner de Agora en el PC IA, junto al AI_Broker.

.DESCRIPTION
    El runner se ENGANCHA a un AI_Broker que ya está en marcha y lee su
    credencial de sesión del Administrador de credenciales de Windows
    (ai-broker / session_admin_token). No lanza el broker ni fabrica su token:
    de eso depende que funcione tras un Wake-on-LAN, cuando Windows arranca el
    broker por su cuenta y el puerto ya está ocupado.

    Deja instalado:
      · un venv aislado en <InstallDir>\venv con el paquete agora
      · <InstallDir>\runner.cmd, que arranca el runner con esta configuración
      · opcionalmente, una tarea programada que lo arranca con la máquina

.EXAMPLE
    .\install-runner.ps1 -BoardUrl https://192.168.1.50:8741 `
                         -RunnerId ai-1 -Profile summarizer `
                         -ProfilesRoot C:\Agora\AGENTS -CaCert C:\Agora\ca.crt

.NOTES
    El token del tablero (AGORA_API_TOKEN) no se guarda en disco por este
    script: se pide al instalar y se registra en el llavero del usuario, o se
    pone en la tarea programada como variable de entorno del proceso.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BoardUrl,
    [Parameter(Mandatory = $true)][string]$RunnerId,
    [Parameter(Mandatory = $true)][string[]]$Profile,
    [Parameter(Mandatory = $true)][string]$ProfilesRoot,
    [Parameter(Mandatory = $true)][string]$CaCert,
    [string]$InstallDir = "$env:LOCALAPPDATA\Agora\runner",
    [string]$Wheel,
    [string]$PinSpki,
    [string]$ModelsCatalog,
    [int]$BrokerPort = 8765,
    [double]$PollSeconds = 15,
    [string]$PythonExe = "python",
    [switch]$RegisterScheduledTask,
    [switch]$SkipCheck
)

$ErrorActionPreference = 'Stop'

# PowerShell 5.1 envuelve el stderr de un ejecutable nativo en ErrorRecord y pone
# $? a $false aunque el proceso salga con 0. Con ErrorActionPreference = Stop eso
# aborta instalaciones correctas, así que los nativos se invocan aquí y se juzgan
# por su código de salida, que es lo único fiable.
function Invoke-Native {
    param([Parameter(Mandatory = $true)][string]$Exe, [string[]]$Arguments, [string]$FailureMessage)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object { Write-Host "   $_" }
        if ($LASTEXITCODE -ne 0) {
            throw $(if ($FailureMessage) { $FailureMessage } else { "$Exe salio con codigo $LASTEXITCODE" })
        }
    }
    finally { $ErrorActionPreference = $previous }
}

function Write-Step($message) { Write-Host "`n== $message" -ForegroundColor Cyan }
function Write-Ok($message) { Write-Host "   $message" -ForegroundColor Green }

# --- 1. Localizar el paquete -------------------------------------------------

Write-Step 'Localizando el paquete de Agora'
if (-not $Wheel) {
    $candidate = Get-ChildItem -Path $PSScriptRoot -Filter 'agora_atomic_work-*.whl' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -First 1
    if (-not $candidate) {
        $candidate = Get-ChildItem -Path (Join-Path $PSScriptRoot '..\dist') -Filter 'agora_atomic_work-*.whl' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending | Select-Object -First 1
    }
    if (-not $candidate) {
        throw "No encuentro ningun .whl de Agora. Pasa -Wheel <ruta> o copia el wheel junto a este script."
    }
    $Wheel = $candidate.FullName
}
if (-not (Test-Path $Wheel)) { throw "No existe el wheel: $Wheel" }
Write-Ok "wheel: $Wheel"

foreach ($required in @($ProfilesRoot, $CaCert)) {
    if (-not (Test-Path $required)) { throw "No existe la ruta: $required" }
}

# --- 2. Entorno aislado ------------------------------------------------------

Write-Step "Creando el entorno en $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$venv = Join-Path $InstallDir 'venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Invoke-Native -Exe $PythonExe -Arguments @('-m', 'venv', $venv) -FailureMessage "No se pudo crear el venv con $PythonExe"
}
Write-Ok (& $venvPython --version)

Write-Step 'Instalando Agora y sus dependencias de runner'
Invoke-Native -Exe $venvPython -Arguments @('-m', 'pip', 'install', '--upgrade', '--quiet', 'pip')
Invoke-Native -Exe $venvPython -Arguments @('-m', 'pip', 'install', '--quiet', '--force-reinstall', "$Wheel[runner]") -FailureMessage 'Fallo la instalacion del paquete'
$installed = & $venvPython -c "import agora; print(agora.__version__)"
Write-Ok "agora $installed"

# --- 3. Credencial del broker ------------------------------------------------

Write-Step 'Comprobando la credencial de sesion del AI_Broker'
$tokenPresent = & $venvPython -c @"
import keyring
value = keyring.get_password('ai-broker', 'session_admin_token')
print('yes' if value else 'no')
"@
if ($tokenPresent.Trim() -ne 'yes') {
    Write-Warning @"
El llavero no tiene ai-broker / session_admin_token.
El broker solo la publica con 'server.publish_session_token: keyring' en su
broker_config.yaml, y la escribe AL ARRANCAR. Revisa la configuracion y
reinicia el broker; luego vuelve a ejecutar este script o lanza:
    $InstallDir\runner.cmd --check
"@
} else {
    Write-Ok 'ai-broker / session_admin_token presente'
}

# --- 4. Lanzador -------------------------------------------------------------

Write-Step 'Escribiendo el lanzador'
$profileArgs = ($Profile | ForEach-Object { "--profile `"$_`"" }) -join ' '
$optional = ''
if ($PinSpki) { $optional += " --pin-spki `"$PinSpki`"" }
if ($ModelsCatalog) { $optional += " --models `"$ModelsCatalog`"" }

function Get-RunnerArgs {
    param([string[]]$Extra = @())
    $arguments = @('-m', 'agora.broker.main', $BoardUrl, '--runner-id', $RunnerId)
    foreach ($name in $Profile) { $arguments += @('--profile', $name) }
    $arguments += @(
        '--profiles-root', $ProfilesRoot,
        '--ca-cert', $CaCert,
        '--broker-credential', 'keyring',
        '--broker-port', "$BrokerPort",
        '--poll-seconds', "$PollSeconds"
    )
    if ($PinSpki) { $arguments += @('--pin-spki', $PinSpki) }
    if ($ModelsCatalog) { $arguments += @('--models', $ModelsCatalog) }
    return $arguments + $Extra
}

$launcher = Join-Path $InstallDir 'runner.cmd'
@"
@echo off
rem Generado por install-runner.ps1 - Agora $installed
rem El token del tablero llega por AGORA_API_TOKEN; nunca se escribe aqui.
"$venvPython" -m agora.broker.main "$BoardUrl" ^
  --runner-id "$RunnerId" $profileArgs ^
  --profiles-root "$ProfilesRoot" ^
  --ca-cert "$CaCert" ^
  --broker-credential keyring ^
  --broker-port $BrokerPort ^
  --poll-seconds $PollSeconds$optional %*
"@ | Set-Content -Path $launcher -Encoding ASCII
Write-Ok $launcher

# --- 5. Arranque con la maquina (F8) ----------------------------------------

if ($RegisterScheduledTask) {
    Write-Step 'Registrando la tarea programada'
    if (-not $env:AGORA_API_TOKEN) {
        throw 'Define AGORA_API_TOKEN antes de registrar la tarea: la tarea lo necesita para autenticarse contra el tablero.'
    }
    $taskName = "Agora AI Runner ($RunnerId)"
    $wrapper = Join-Path $InstallDir 'runner-task.cmd'
    @"
@echo off
set AGORA_API_TOKEN=$env:AGORA_API_TOKEN
call "$launcher"
"@ | Set-Content -Path $wrapper -Encoding ASCII
    # El fichero lleva un secreto: que solo lo lea quien corre la tarea.
    icacls $wrapper /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

    $action = New-ScheduledTaskAction -Execute $wrapper
    # Retraso deliberado: tras un Wake-on-LAN, el broker tarda en levantar y en
    # publicar su token de sesion. Arrancar antes solo produce 403 en el log.
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $trigger.Delay = 'PT2M'
    $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) `
        -StartWhenAvailable -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
        -Settings $settings -RunLevel Limited -Force | Out-Null
    Write-Ok "tarea '$taskName' registrada (arranque + 2 min)"
}

# --- 6. Diagnostico ----------------------------------------------------------

if (-not $SkipCheck) {
    Write-Step 'Diagnostico'
    if (-not $env:AGORA_API_TOKEN) {
        Write-Warning 'Sin AGORA_API_TOKEN no se puede comprobar el tablero. Definelo y ejecuta: runner.cmd --check'
    } else {
        Invoke-Native -Exe $venvPython -Arguments (Get-RunnerArgs -Extra @('--check')) `
            -FailureMessage 'El diagnostico encontro problemas: revisalos antes de dejar el runner solo.'
    }
}

Write-Host "`nInstalado. Para arrancarlo a mano:" -ForegroundColor Cyan
Write-Host "   set AGORA_API_TOKEN=<token del tablero>"
Write-Host "   $launcher --once"
