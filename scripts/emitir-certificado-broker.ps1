<#
.SYNOPSIS
    Emite (o vuelve a emitir) el certificado HTTPS del AI Broker (PC IA) con la AI Broker CA.

.DESCRIPTION
    La AI Broker CA vive en C:\Agora\pki-broker, separada de la CA de Agora (tablero y Core).
    Su clave va cifrada con una frase aleatoria guardada en el Administrador de credenciales
    de Windows (servicio "agora-broker-ca", usuario "passphrase"): nadie tiene que recordarla.
    Caducidad por defecto: 100 anos.

    Hosts: 192.168.1.52 (las apps del PC principal) y 127.0.0.1/localhost (el runner de
    Agora, que habla con el Broker por loopback en el propio PC IA).

    Deja en -Out: server.crt, server.key y ca.crt. Al PC IA van server.crt y server.key
    (uvicorn: ssl_certfile / ssl_keyfile) y ca.crt (para el runner: -BrokerHttps).
#>
param(
    [string]$Out = "C:\Agora\deploy\broker-https",
    [string[]]$BrokerHost = @("192.168.1.52", "127.0.0.1", "localhost"),
    [string]$CaDir = "C:\Agora\pki-broker",
    [int]$Days = 36500,
    [string]$VenvPython = "C:\Agora\venv\Scripts\python.exe"
)
$ErrorActionPreference = 'Stop'
$certsExe = Join-Path (Split-Path $VenvPython) 'agora-certs.exe'

if (-not $env:AGORA_CA_PASSPHRASE) {
    $frase = & $VenvPython -c "import keyring; print(keyring.get_password('agora-broker-ca', 'passphrase') or '')"
    if (-not $frase) { throw "No hay frase de la AI Broker CA en el Administrador de credenciales (agora-broker-ca)." }
    $env:AGORA_CA_PASSPHRASE = $frase.Trim()
}
try {
    $hosts = @(); foreach ($h in $BrokerHost) { $hosts += @('--host', $h) }
    & $certsExe issue --ca-dir $CaDir --out $Out --days $Days @hosts
    if ($LASTEXITCODE -ne 0) { throw "agora-certs no pudo emitir el certificado (codigo $LASTEXITCODE)" }
    Copy-Item -LiteralPath (Join-Path $CaDir 'ca.crt') -Destination $Out -Force
    Write-Host ""
    Write-Host "Listo en $Out. Al PC IA: server.crt, server.key y ca.crt (nunca ca.key)." -ForegroundColor Green
}
finally {
    $env:AGORA_CA_PASSPHRASE = $null
}
