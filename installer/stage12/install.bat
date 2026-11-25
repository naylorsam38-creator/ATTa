@echo off
rem ATTa Stage 12 installer (Windows).
rem   install.bat --target C:\path\to\ATTa [--dry-run ^| --uninstall ^| --recover] [--migration-number N^|auto]
setlocal
set "HERE=%~dp0"
where py >nul 2>nul
if errorlevel 1 goto trypython
py -3 "%HERE%install.py" %*
exit /b %ERRORLEVEL%
:trypython
where python >nul 2>nul
if errorlevel 1 goto nopython
python "%HERE%install.py" %*
exit /b %ERRORLEVEL%
:nopython
echo Python 3.10+ is required (https://www.python.org/downloads/) 1>&2
exit /b 5
