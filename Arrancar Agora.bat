@echo off
setlocal
rem Agora - arranca el tablero (la API que atiende al PC IA) y su ventana KANBAN.
rem Usa la instalacion de C:\Agora (scripts\install-board.ps1). El tablero se abre
rem en su propia consola, que debe quedarse abierta mientras trabaje el PC IA.

set "AGORA_DIR=C:\Agora"
if not exist "%AGORA_DIR%\Agora Tablero.cmd" (
    echo No encuentro Agora instalado en %AGORA_DIR%.
    echo Instalalo con:  powershell -ExecutionPolicy Bypass -File scripts\install-board.ps1
    pause
    exit /b 1
)

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 8741 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if errorlevel 1 (
    start "Agora - tablero" "%AGORA_DIR%\Agora Tablero.cmd"
    rem Margen para que la API escuche antes de abrir la ventana que la consulta
    timeout /t 4 /nobreak >nul
) else (
    echo El tablero ya esta en marcha en el puerto 8741.
)

call "%AGORA_DIR%\Agora Desktop.cmd"
exit /b 0
