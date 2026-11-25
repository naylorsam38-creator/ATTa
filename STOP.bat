@echo off
rem Stop every service the service manager started (reverse dependency order, graceful then forced).
setlocal
cd /d "%~dp0"
if "%ATTA_VENV%"=="" set "ATTA_VENV=%~dp0.venv"
"%ATTA_VENV%\Scripts\python.exe" -m backend.service_manager stop %*
exit /b %ERRORLEVEL%
