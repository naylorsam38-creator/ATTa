@echo off
rem ATTa platform installer (Windows). Linux/macOS: install.sh
rem   INSTALL.bat [--with-ai] [--with-browser] [--no-node] [--with-desktop] [--with-mobile]
rem Installs only locked, approved dependencies. Never downloads models; never starts paid infrastructure.
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "ROOT=%~dp0"
if "%ATTA_VENV%"=="" set "ATTA_VENV=%ROOT%.venv"
set WITH_AI=0& set WITH_BROWSER=0& set WITH_NODE=1& set WITH_DESKTOP=0& set WITH_MOBILE=0
:args
if "%~1"=="" goto argsdone
if /i "%~1"=="--with-ai" set WITH_AI=1
if /i "%~1"=="--with-browser" set WITH_BROWSER=1
if /i "%~1"=="--no-node" set WITH_NODE=0
if /i "%~1"=="--with-desktop" set WITH_DESKTOP=1
if /i "%~1"=="--with-mobile" set WITH_MOBILE=1
shift
goto args
:argsdone
set "PY="
for %%V in (3.13 3.12) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if (3,12)<=sys.version_info[:2]<(3,14) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [install] ERROR: Python 3.12 or 3.13 is required: https://www.python.org/downloads/windows/
  exit /b 1
)
where git >nul 2>&1 || (echo [install] ERROR: git is required & exit /b 1)
echo [install] python: %PY%
set "LOCK=%ROOT%configuration\locks\backend-core.lock.txt"
if "%WITH_AI%%WITH_BROWSER%" NEQ "00" set "LOCK=%ROOT%configuration\locks\backend-full.lock.txt"
if not exist "%ATTA_VENV%\Scripts\python.exe" (
  echo [install] creating virtual environment %ATTA_VENV%
  %PY% -m venv "%ATTA_VENV%" || exit /b 1
)
set "VPY=%ATTA_VENV%\Scripts\python.exe"
"%VPY%" -m pip install --disable-pip-version-check -q --upgrade "pip>=25" >nul || exit /b 1
echo [install] installing locked backend dependencies
"%VPY%" -m pip install --disable-pip-version-check -q --require-hashes --no-deps -r "%LOCK%" || exit /b 1
"%VPY%" -m pip check || exit /b 1
for /f "delims=" %%S in ('"%VPY%" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"') do set "SITE=%%S"
> "%SITE%\atta_platform.pth" echo %ROOT:~0,-1%
if "%WITH_NODE%"=="1" (
  where node >nul 2>&1
  if errorlevel 1 (
    echo [install] WARNING: Node.js 20+ not found - web/desktop clients skipped
  ) else (
    if "%WITH_DESKTOP%"=="1" (set "ELECTRON_SKIP_BINARY_DOWNLOAD=") else (set "ELECTRON_SKIP_BINARY_DOWNLOAD=1")
    echo [install] installing locked Node workspaces
    call npm ci --no-audit --no-fund --loglevel=error || exit /b 1
    if "%WITH_MOBILE%"=="1" (pushd apps\mobile & call npm ci --no-audit --no-fund --loglevel=error & popd)
  )
)
where redis-server >nul 2>&1
if errorlevel 1 (
  echo [install] NOTE: redis-server is not available natively on Windows. Use Docker Desktop:
  echo [install]       docker compose -f infrastructure\docker\compose.yaml up -d postgres redis
  echo [install]       and add  ATTA__SERVICES__REDIS__MODE=external  and  ATTA__REDIS__PORT=6380  to configuration\local.env
)
if not exist "configuration\local.env" (
  copy /y "configuration\local.env.example" "configuration\local.env" >nul
  echo [install] created configuration\local.env - set ATTA_DATABASE_PASSWORD and ATTA_MODEL_PATHS ^(e.g. D:\models^) before START
)
if not exist runtime mkdir runtime
"%VPY%" -m backend.service_manager validate || exit /b 1
echo [install] INSTALL COMPLETE
endlocal
