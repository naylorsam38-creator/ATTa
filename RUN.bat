@echo off
rem RUN: start the activated ATTa deployment (refuses if the machine or configuration changed since qualification).
rem Thin launcher: all logic is in "python -m backend.deployment run". Never edited when capabilities are added.
setlocal
cd /d "%~dp0"
if "%ATTA_VENV%"=="" set "ATTA_VENV=%~dp0.venv"
if not exist "%ATTA_VENV%\Scripts\python.exe" (echo not installed: run INSTALL.bat & exit /b 1)
set NEXT_TELEMETRY_DISABLED=1
"%ATTA_VENV%\Scripts\python.exe" -m backend.deployment run %*
exit /b %ERRORLEVEL%
