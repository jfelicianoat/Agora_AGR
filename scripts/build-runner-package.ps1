<#
.SYNOPSIS
    Construye el paquete que se copia al PC IA para instalar el AI Runner.

.DESCRIPTION
    Genera dist\agora-runner-<version>.zip con:
      · el wheel de Agora
      · install-runner.ps1
      · INSTALAR.md, con los pasos y lo que hay que tener a mano

    No incluye ningun secreto ni el certificado del tablero: esos se generan en
    el PC del tablero con `agora-certs` y se copian aparte.

.EXAMPLE
    .\build-runner-package.ps1
#>
[CmdletBinding()]
param(
    [string]$PythonExe = 'python'
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

# Ordenar por nombre pone '0.2.9' por delante de '0.2.11' (el 9 gana al 1) y
# elegiria el wheel viejo. Se ordena por la version que lleva el nombre.
function Get-WheelVersion {
    param([Parameter(Mandatory = $true)][System.IO.FileInfo]$File)
    $parsed = $null
    if ([version]::TryParse(($File.BaseName -split '-')[1], [ref]$parsed)) { return $parsed }
    return [version]'0.0'
}

$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
    Write-Host '== Construyendo el wheel' -ForegroundColor Cyan
    Remove-Item -Recurse -Force (Join-Path $repo 'dist') -ErrorAction SilentlyContinue
    Invoke-Native -Exe $PythonExe -Arguments @('-m', 'build', '--wheel') -FailureMessage 'Fallo la construccion del wheel'

    $wheel = Get-ChildItem (Join-Path $repo 'dist') -Filter '*.whl' |
        Sort-Object { Get-WheelVersion $_ } -Descending | Select-Object -First 1
    if (-not $wheel) { throw 'No se genero ningun wheel' }
    $version = ($wheel.BaseName -split '-')[1]

    $staging = Join-Path $repo "dist\agora-runner-$version"
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    Copy-Item $wheel.FullName $staging
    Copy-Item (Join-Path $PSScriptRoot 'install-runner.ps1') $staging
    Copy-Item (Join-Path $repo 'docs\INSTALAR_RUNNER.md') (Join-Path $staging 'INSTALAR.md')

    $zip = Join-Path $repo "dist\agora-runner-$version.zip"
    Remove-Item $zip -ErrorAction SilentlyContinue
    Compress-Archive -Path "$staging\*" -DestinationPath $zip
    Remove-Item -Recurse -Force $staging

    Write-Host "`nPaquete listo: $zip" -ForegroundColor Green
    Write-Host 'Copialo al PC IA y sigue INSTALAR.md.'
}
finally {
    Pop-Location
}
