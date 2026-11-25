@echo off
rem Start the ATTa platform through the central service manager.
rem Capabilities register automatically: this file never needs editing when a capability is added.
setlocal
cd /d "%~dp0"
if "%ATTA_VENV%"=="" set "ATTA_VENV=%~dp0.venv"
if not exist "%ATTA_VENV%\Scripts\python.exe" (echo not installed: run INSTALL.bat & exit /b 1)
"%ATTA_VENV%\Scripts\python.exe" -m backend.service_manager start %*
exit /b %ERRORLEVEL%
