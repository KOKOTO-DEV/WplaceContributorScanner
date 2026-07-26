@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
chcp 65001 >nul 2>nul

for %%I in (".") do set "PROJECT_ROOT=%%~fI"
set "VENV_MARKER=.venv\.wplace_venv_root"
set "RECREATE_VENV=0"
set "NEXT_IS_LANG=0"

rem Console language priority: --lang, WPCS_LANG, Windows UI culture.
for %%A in (%*) do (
  if "!NEXT_IS_LANG!"=="1" (
    set "WPCS_LANG=%%~A"
    set "NEXT_IS_LANG=0"
  ) else (
    if /I "%%~A"=="--lang" set "NEXT_IS_LANG=1"
    set "ARG=%%~A"
    if /I "!ARG:~0,7!"=="--lang=" set "WPCS_LANG=!ARG:~7!"
  )
)

if not defined WPCS_LANG (
  for /f "usebackq delims=" %%L in (`powershell.exe -NoProfile -Command "[System.Globalization.CultureInfo]::CurrentUICulture.Name" 2^>nul`) do set "WPCS_LANG=%%L"
)
if /I "!WPCS_LANG:~0,2!"=="ko" (
  set "WPCS_LANG=ko"
) else if /I "!WPCS_LANG:~0,2!"=="ja" (
  set "WPCS_LANG=ja"
) else if /I "!WPCS_LANG:~0,2!"=="zh" (
  set "WPCS_LANG=zh-CN"
) else (
  set "WPCS_LANG=en"
)

if exist ".venv\Scripts\python.exe" (
  if not exist "!VENV_MARKER!" (
    set "RECREATE_VENV=1"
  ) else (
    set /p "SAVED_VENV_ROOT="<"!VENV_MARKER!"
    if /I not "!SAVED_VENV_ROOT!"=="!PROJECT_ROOT!" set "RECREATE_VENV=1"
  )
  if "!RECREATE_VENV!"=="0" (
    ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if errorlevel 1 set "RECREATE_VENV=1"
  )
  if "!RECREATE_VENV!"=="0" (
    ".venv\Scripts\python.exe" -m pip --version >nul 2>nul
    if errorlevel 1 set "RECREATE_VENV=1"
  )
)

if "!RECREATE_VENV!"=="1" (
  call :message recreate
  rmdir /s /q ".venv"
)

where py.exe >nul 2>nul
if errorlevel 1 (
  call :message python_required
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
  call :message python_too_old
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  if exist ".venv" rmdir /s /q ".venv"
  call :message create_venv
  py -3 -m venv .venv || goto :error
)

>"!VENV_MARKER!" echo(!PROJECT_ROOT!
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt || goto :error
".venv\Scripts\python.exe" app.py --browser %*
exit /b 0

:message
set "MESSAGE_KEY=%~1"
if exist "%~dp0launcher_i18n.ps1" (
  where powershell.exe >nul 2>nul
  if not errorlevel 1 (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher_i18n.ps1" -Language "!WPCS_LANG!" -Key "!MESSAGE_KEY!"
    exit /b 0
  )
)
if /I "%~1"=="recreate" echo [setup] Recreating a copied, moved, incompatible, or incomplete .venv.
if /I "%~1"=="python_required" echo Python 3.10 or newer is required.
if /I "%~1"=="python_too_old" echo The Python selected by the py launcher is too old. Install Python 3.10 or newer.
if /I "%~1"=="create_venv" echo [setup] Creating the Python virtual environment.
if /I "%~1"=="start_failed" echo Failed to start Wplace Contributor Scanner.
exit /b 0

:error
echo.
call :message start_failed
pause
exit /b 1
