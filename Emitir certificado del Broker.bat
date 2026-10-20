@echo off
rem Emite server.crt/server.key del AI Broker (PC IA) con la CA de Agora. Pide la frase de la CA.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\emitir-certificado-broker.ps1" %*
pause
