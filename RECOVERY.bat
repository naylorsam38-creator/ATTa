@echo off
rem Restore the last verified, database-compatible checkpoint recorded by the build controller.
rem   RECOVERY.bat          show the plan (dry run)
rem   RECOVERY.bat --yes    stop services, stash current work, switch to a recovery branch at the checkpoint
setlocal
cd /d "%~dp0"
if "%ATTA_VENV%"=="" set "ATTA_VENV=%~dp0.venv"
set "VPY=%ATTA_VENV%\Scripts\python.exe"
"%VPY%" -m backend.verification checkpoints
if /i not "%~1"=="--yes" (
  "%VPY%" -m backend.verification recover
  echo Dry run. Re-run with --yes to restore.
  exit /b 0
)
set /p ANS=Type RESTORE to stop services and restore the checkpoint: 
if not "%ANS%"=="RESTORE" (echo cancelled & exit /b 1)
"%VPY%" -m backend.service_manager stop
"%VPY%" -m backend.verification recover --yes || exit /b 1
echo Restored. Your previous work is in "git stash list". Run INSTALL.bat then START.bat.
