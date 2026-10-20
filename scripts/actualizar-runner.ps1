<#
.SYNOPSIS
    Actualiza el AI Runner del PC IA con el wheel de esta carpeta, sin volver a escribir nada.

.DESCRIPTION
    Lee la configuracion del runner.cmd ya instalado (tablero, id, PROFILEs, CA, anclaje,
    catalogo, puerto del broker, sondeo y AI OS Core), para la tarea programada si existe,
    reinstala con install-runner.ps1 y la vuelve a arrancar. Al final muestra la version y,
    si AGORA_API_TOKEN esta definido, el diagnostico completo (--check).

    Con -CoreUrl y -CoreToken ademas conecta el runner a AI OS Core: lo escribe en runner.cmd
    y, si hay tarea programada, guarda el token de Core en su runner-task.cmd. La tarea no se
    vuelve a registrar: eso exige administrador, y no hace falta porque ya llama a ese fichero.

.EXAMPLE
    .\actualizar-runner.ps1
.EXAMPLE
    .\actualizar-runner.ps1 -BrokerHttps   # el Broker ya sirve HTTPS en 127.0.0.1:8765
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Agora\runner",
    [string]$CoreUrl,
    [string]$CoreToken,
    # Broker ya en HTTPS: instala la AI Broker CA que viene en el paquete (broker-ca.crt)
    # y el runner valida con ella el certificado del Broker
    [switch]$BrokerHttps,
    # Volver a http://127.0.0.1 (quita --broker-ca-cert): p. ej. si el Broker aun no sirve HTTPS
    [switch]$BrokerHttp,
    # Otra URL del tablero (p. ej. por IP si el nombre del PC resuelve a adaptadores virtuales)
    [string]$BoardUrl
)

$ErrorActionPreference = 'Stop'
$runnerCmd = Join-Path $InstallDir 'runner.cmd'
if (-not (Test-Path $runnerCmd)) {
    throw "No hay runner instalado en $InstallDir. Para la primera instalacion usa install-runner.ps1 (ver INSTALAR.md)."
}

# runner.cmd es una sola orden partida con ^: se junta y se leen sus opciones
$texto = ((Get-Content $runnerCmd | Where-Object { $_ -notmatch '^\s*(@echo|rem)\b' }) -join ' ') -replace '\^', ' '
# Argumentos: "entre comillas" o sin espacios
$args_ = @([regex]::Matches($texto, '"([^"]*)"|(\S+)') | ForEach-Object {
    if ($_.Groups[1].Success) { $_.Groups[1].Value } else { $_.Groups[2].Value } })
function Opcion([string]$nombre) {
    $valores = @()
    for ($i = 0; $i -lt $args_.Count - 1; $i++) { if ($args_[$i] -eq "--$nombre") { $valores += $args_[$i + 1] } }
    return $valores
}
$board = $args_[[array]::IndexOf($args_, 'agora.broker.main') + 1]
$config = @{
    BoardUrl     = $board
    RunnerId     = @(Opcion 'runner-id')[0]
    Profile      = @(Opcion 'profile')
    ProfilesRoot = @(Opcion 'profiles-root')[0]
    CaCert       = @(Opcion 'ca-cert')[0]
    InstallDir   = $InstallDir
    SkipCheck    = $true
}
foreach ($par in @(@('PinSpki', 'pin-spki'), @('ModelsCatalog', 'models'), @('CoreUrl', 'core-url'),
                   @('BrokerCaCert', 'broker-ca-cert'),
                   @('BrokerPort', 'broker-port'), @('PollSeconds', 'poll-seconds'))) {
    $valor = @(Opcion $par[1])[0]
    if ($valor) { $config[$par[0]] = $valor }
}
foreach ($obligatoria in 'BoardUrl', 'RunnerId', 'ProfilesRoot', 'CaCert') {
    if (-not $config[$obligatoria]) { throw "No encuentro $obligatoria en $runnerCmd; actualiza a mano con install-runner.ps1." }
}
if (-not $config.Profile) { throw "No encuentro ningun --profile en $runnerCmd." }

# Los tokens que guarda la tarea programada (runner-task.cmd): sirven para volver a registrarla
# y para el diagnostico, sin tener que escribirlos. Solo viven en esta consola.
$tareaCmd = Join-Path $InstallDir 'runner-task.cmd'
if (Test-Path $tareaCmd) {
    foreach ($linea in Get-Content $tareaCmd) {
        if ($linea -match '^set (AGORA_API_TOKEN|AGORA_CORE_TOKEN)=(.+)$' -and -not (Get-Item "Env:$($Matches[1])" -ErrorAction SilentlyContinue)) {
            Set-Item "Env:$($Matches[1])" $Matches[2].Trim()
        }
    }
}
if ($CoreUrl) { $config.CoreUrl = $CoreUrl }
if ($BrokerHttps) {
    $caPaquete = Join-Path $PSScriptRoot 'broker-ca.crt'
    if (-not (Test-Path -LiteralPath $caPaquete)) { throw "Este paquete no trae broker-ca.crt: no se puede pasar el Broker a HTTPS." }
    $caInstalada = Join-Path $InstallDir 'broker-ca.crt'
    Copy-Item -LiteralPath $caPaquete -Destination $caInstalada -Force
    $config.BrokerCaCert = $caInstalada
}
if ($BrokerHttp) { $config.Remove('BrokerCaCert') }
if ($BoardUrl) { $config.BoardUrl = $BoardUrl }
if ($CoreToken) { $env:AGORA_CORE_TOKEN = $CoreToken }

Write-Host "`n== Configuracion leida de $runnerCmd" -ForegroundColor Cyan
$config.GetEnumerator() | Where-Object Name -notin 'SkipCheck', 'InstallDir' | Sort-Object Name |
    ForEach-Object { Write-Host ("   {0,-14} {1}" -f $_.Name, ($_.Value -join ', ')) }

$tarea = Get-ScheduledTask -TaskName "Agora AI Runner ($($config.RunnerId))" -ErrorAction SilentlyContinue
if ($tarea -and $CoreToken -and -not $env:AGORA_API_TOKEN) {
    throw "Para guardar el token de Core en la tarea hace falta el del tablero y no esta en $tareaCmd. Define AGORA_API_TOKEN y repite."
}
if ($tarea) {
    Write-Host "`n== Parando la tarea '$($tarea.TaskName)'" -ForegroundColor Cyan
    Stop-ScheduledTask -InputObject $tarea
}
# Un runner abierto a mano tiene cargado el paquete: pip no podria reemplazarlo
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*agora.broker.main*' -and $_.CommandLine -like "*$InstallDir*" } |
    ForEach-Object { Write-Host "   cerrando runner PID $($_.ProcessId)"; Stop-Process -Id $_.ProcessId -Force }

try {
    & (Join-Path $PSScriptRoot 'install-runner.ps1') @config
    if ($tarea -and $CoreToken) {
        # Mismo formato que escribe install-runner.ps1; la tarea ya ejecuta este fichero
        Write-Host "`n== Guardando el token de Core para la tarea" -ForegroundColor Cyan
        @"
@echo off
set AGORA_API_TOKEN=$env:AGORA_API_TOKEN
set AGORA_CORE_TOKEN=$env:AGORA_CORE_TOKEN
call "$runnerCmd"
"@ | Set-Content -Path $tareaCmd -Encoding ASCII
        icacls $tareaCmd /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
        Write-Host "   $tareaCmd"
    }
}
finally {
    # Pase lo que pase, el runner no se queda parado
    if ($tarea) {
        Write-Host "`n== Arrancando de nuevo la tarea" -ForegroundColor Cyan
        Start-ScheduledTask -InputObject $tarea
    }
}

Write-Host "`n== Version instalada" -ForegroundColor Cyan
& $runnerCmd --version
if ($CoreToken -and -not $tarea) {
    Write-Host "`nNo hay tarea programada: si arrancas el runner a mano, define antes AGORA_CORE_TOKEN." -ForegroundColor Yellow
}
if ($env:AGORA_API_TOKEN) {
    Write-Host "`n== Diagnostico" -ForegroundColor Cyan
    & $runnerCmd --check
} else {
    Write-Host "`nPara el diagnostico completo: define AGORA_API_TOKEN (token del tablero) y ejecuta" -ForegroundColor Yellow
    Write-Host "   `"$runnerCmd`" --check"
}
