@echo off
rem Inspect activation, environment manifest, services, ports, controller and capabilities. Exit 0 = healthy.
setlocal
cd /d "%~dp0"
if "%ATTA_VENV%"=="" set "ATTA_VENV=%~dp0.venv"
"%ATTA_VENV%\Scripts\python.exe" -m backend.deployment healthcheck %*
exit /b %ERRORLEVEL%
