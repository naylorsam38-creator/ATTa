@echo off
rem Inspect actual services, ports, dependencies, model endpoints and registered capabilities. Exit 0 = healthy.
setlocal
cd /d "%~dp0"
if "%ATTA_VENV%"=="" set "ATTA_VENV=%~dp0.venv"
"%ATTA_VENV%\Scripts\python.exe" -m backend.service_manager healthcheck %*
exit /b %ERRORLEVEL%
