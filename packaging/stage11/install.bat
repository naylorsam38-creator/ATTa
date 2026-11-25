@echo off
rem Windows wrapper: runs the transactional installer. Usage: install.bat C:\path\to\ATTa [--check]
setlocal
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 "%~dp0install.py" %*
) else (
  python "%~dp0install.py" %*
)
exit /b %ERRORLEVEL%
