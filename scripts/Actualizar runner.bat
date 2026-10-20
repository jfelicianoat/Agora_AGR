@echo off
rem Agora - actualiza el AI Runner de este PC IA con el paquete de esta carpeta.
rem Lee la configuracion del runner ya instalado: no hay que escribir nada.
rem Opcional: define antes AGORA_API_TOKEN (token del tablero) para ver el diagnostico completo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0actualizar-runner.ps1" %*
echo.
pause
