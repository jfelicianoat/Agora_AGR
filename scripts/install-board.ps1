<#
.SYNOPSIS
    Instala Agora en el PC del tablero y deja accesos directos para arrancarlo.

.DESCRIPTION
    El tablero es la mitad de Agora que vive en el PC de trabajo: el KANBAN, la
    ventana de escritorio y la API que sirve tarjetas al runner del PC IA.

    Deja instalado:
      · un venv aislado en <InstallDir>\venv con agora[api,oauth,desktop]
      · "Agora Desktop.cmd"  — la ventana
      · "Agora Tablero.cmd"  — la API que atiende al PC IA
      · accesos directos en el Escritorio (salvo -NoShortcuts)

    El token del tablero NO se escribe en disco: los lanzadores lo leen del
    Administrador de credenciales (agora-board / api_token), y lo crean la
    primera vez si no existe.

.EXAMPLE
    .\install-board.ps1

.EXAMPLE
    .\install-board.ps1 -Workspace D:\MiTablero -Port 8741
#>
[CmdletBinding()]
param(
    [string]$InstallDir = 'C:\Agora',
    [string]$Workspace,
    [string]$Wheel,
    [int]$Port = 8741,
    [string]$ListenHost = '0.0.0.0',
    [string]$PythonExe = 'python',
    [switch]$NoShortcuts
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

if (-not $Workspace) { $Workspace = Join-Path $InstallDir 'workspace' }

# --- 1. Paquete ---------------------------------------------------------------

Write-Step 'Localizando el paquete de Agora'
if (-not $Wheel) {
    foreach ($folder in @($PSScriptRoot, (Join-Path $PSScriptRoot '..\dist'), (Join-Path $InstallDir 'deploy'))) {
        $candidate = Get-ChildItem -Path $folder -Filter 'agora_atomic_work-*.whl' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending | Select-Object -First 1
        if ($candidate) { $Wheel = $candidate.FullName; break }
    }
}
if (-not $Wheel -or -not (Test-Path $Wheel)) {
    throw 'No encuentro ningun .whl de Agora. Construyelo con scripts\build-runner-package.ps1 o pasa -Wheel <ruta>.'
}
Write-Ok "wheel: $Wheel"

# --- 2. Entorno ---------------------------------------------------------------

Write-Step "Creando el entorno en $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$venv = Join-Path $InstallDir 'venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
$venvPythonW = Join-Path $venv 'Scripts\pythonw.exe'
if (-not (Test-Path $venvPython)) {
    Invoke-Native -Exe $PythonExe -Arguments @('-m', 'venv', $venv) -FailureMessage "No se pudo crear el venv con $PythonExe"
}
Write-Ok (& $venvPython --version)

Write-Step 'Instalando Agora (api, oauth, escritorio)'
Invoke-Native -Exe $venvPython -Arguments @('-m', 'pip', 'install', '--upgrade', '--quiet', 'pip')
Invoke-Native -Exe $venvPython -Arguments @('-m', 'pip', 'install', '--quiet', '--force-reinstall', "$Wheel[api,oauth,desktop]") `
    -FailureMessage 'Fallo la instalacion del paquete'
Invoke-Native -Exe $venvPython -Arguments @('-m', 'pip', 'install', '--quiet', 'keyring')
$installed = & $venvPython -c "import agora; print(agora.__version__)"
Write-Ok "agora $installed"

# --- 3. Espacio de trabajo y credencial --------------------------------------

Write-Step "Preparando el tablero en $Workspace"
& $venvPython -c @"
import secrets, sys
import keyring
from pathlib import Path
from agora.application import AgoraApplication

app = AgoraApplication(Path(r'$Workspace'))
app.initialize()
print('board_id: ' + app.instance_id)

if not keyring.get_password('agora-board', 'api_token'):
    keyring.set_password('agora-board', 'api_token', secrets.token_urlsafe(32))
    print('token del tablero creado en el llavero (agora-board / api_token)')
else:
    print('token del tablero ya existia en el llavero')
"@ | ForEach-Object { Write-Ok $_ }

$agents = Join-Path $Workspace 'AGENTS'
if (-not (Test-Path $agents)) {
    New-Item -ItemType Directory -Force -Path $agents | Out-Null
    Write-Warning "No hay PROFILEs en $agents. Copia los tuyos ahi; sin ellos el tablero no despacha nada."
}

# --- 4. Lanzadores ------------------------------------------------------------

Write-Step 'Escribiendo los lanzadores'

# La ventana usa pythonw para no dejar una consola negra detras.
$desktop = Join-Path $InstallDir 'Agora Desktop.cmd'
@"
@echo off
rem Agora $installed - ventana de escritorio
start "" "$venvPythonW" -m agora.desktop.main "$Workspace"
"@ | Set-Content -Path $desktop -Encoding ASCII
Write-Ok $desktop

# La API sirve tarjetas al runner del PC IA. El token sale del llavero: no se
# escribe en disco ni aparece en la linea de ordenes de otro proceso.
#
# El trabajo va en PowerShell y el .cmd es solo el disparador para el doble
# clic. Un `for /f "usebackq"` de cmd no admite la ruta del ejecutable entre
# comillas, asi que se rompia en cuanto la instalacion vivia en una carpeta con
# espacios. PowerShell no tiene ese problema.
$certificate = Join-Path $InstallDir 'pki\server.crt'
$key = Join-Path $InstallDir 'pki\server.key'
$boardScript = Join-Path $InstallDir 'Agora Tablero.ps1'
@"
# Agora $installed - API del tablero (la que atiende al PC IA)
`$ErrorActionPreference = 'Stop'
`$token = & '$venvPython' -c "import keyring; print(keyring.get_password('agora-board','api_token') or '')"
if (-not `$token.Trim()) {
    Write-Host 'No hay token en el llavero (agora-board / api_token). Reinstala con install-board.ps1.' -ForegroundColor Red
    Read-Host 'Pulsa Intro para cerrar'; exit 1
}
if (-not (Test-Path '$certificate')) {
    Write-Host 'Falta el certificado del tablero: $certificate' -ForegroundColor Red
    Write-Host 'Crealo con: agora-certs init-ca  y  agora-certs issue'
    Read-Host 'Pulsa Intro para cerrar'; exit 1
}
`$env:AGORA_API_TOKEN = `$token.Trim()
Write-Host "Agora $installed - tablero en https://`$env:COMPUTERNAME`:$Port" -ForegroundColor Cyan
Write-Host 'Ctrl-C para pararlo.'
& '$venvPython' -m agora.api.main '$Workspace' --host $ListenHost --port $Port ``
    --cert '$certificate' --key '$key' @args
"@ | Set-Content -Path $boardScript -Encoding UTF8
Write-Ok $boardScript

$board = Join-Path $InstallDir 'Agora Tablero.cmd'
@"
@echo off
rem Agora $installed - disparador del tablero
powershell -NoProfile -ExecutionPolicy Bypass -File "$boardScript" %*
"@ | Set-Content -Path $board -Encoding ASCII
Write-Ok $board

# --- 5. Accesos directos ------------------------------------------------------

if (-not $NoShortcuts) {
    Write-Step 'Creando accesos directos en el Escritorio'
    $shell = New-Object -ComObject WScript.Shell
    $desktopFolder = [Environment]::GetFolderPath('Desktop')
    foreach ($pair in @(@{Name = 'Agora Desktop'; Target = $desktop }, @{Name = 'Agora Tablero'; Target = $board })) {
        $link = $shell.CreateShortcut((Join-Path $desktopFolder "$($pair.Name).lnk"))
        $link.TargetPath = $pair.Target
        $link.WorkingDirectory = $InstallDir
        $link.Description = "Agora $installed"
        $link.Save()
        Write-Ok "$($pair.Name).lnk"
    }
}

Write-Host "`nAgora $installed instalado." -ForegroundColor Cyan
Write-Host "   Ventana : `"$desktop`""
Write-Host "   Tablero : `"$board`""
Write-Host "   Tablero (linea de ordenes): $venvPython -m agora.api.main ..."
