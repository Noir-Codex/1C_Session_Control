@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title 1C Session Control

echo [1/4] Checking Python...
call :find_python
if not defined PYTHON_CMD (
    echo Python 3.11 or newer was not found. Installing it with winget...
    where winget >nul 2>&1
    if errorlevel 1 goto :python_error
    winget install --id Python.Python.3.13 -e --scope user --accept-package-agreements --accept-source-agreements --silent
    if errorlevel 1 goto :python_error
    call :find_python
    if not defined PYTHON_CMD goto :python_error
)

set "VENV_DIR=%CD%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

echo [2/4] Checking the virtual environment...
if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if errorlevel 1 call :remove_venv
)
if not exist "%VENV_PYTHON%" (
    echo Creating a new .venv environment...
    %PYTHON_CMD% -m venv "%VENV_DIR%"
    if errorlevel 1 goto :venv_error
)

echo [3/4] Checking dependencies...
"%VENV_PYTHON%" -c "import fastapi, jinja2, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo Installing required packages...
    "%VENV_PYTHON%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :packages_error
)
"%VENV_PYTHON%" -m pip check >nul 2>&1
if errorlevel 1 (
    "%VENV_PYTHON%" -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :packages_error
)

if not exist "config.toml" (
    if not exist "config.example.toml" goto :config_error
    copy /y "config.example.toml" "config.toml" >nul
    echo Created local config.toml.
)

set "LAUNCHER_ARGS="
if /i "%~1"=="--check" set "LAUNCHER_ARGS=--check"
echo [4/4] Starting the application...
if defined LAUNCHER_ARGS echo Diagnostic mode: the web server will not be started.
if not defined LAUNCHER_ARGS echo Press Ctrl+C to stop.
"%VENV_PYTHON%" launcher.py %LAUNCHER_ARGS%
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" (
    echo.
    echo The application exited with code %APP_EXIT%.
    pause
)
exit /b %APP_EXIT%

:find_python
set "PYTHON_CMD="
py -3.14 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYTHON_CMD=py -3.14" && exit /b 0
py -3.13 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYTHON_CMD=py -3.13" && exit /b 0
py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYTHON_CMD=py -3.12" && exit /b 0
py -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYTHON_CMD=py -3.11" && exit /b 0
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYTHON_CMD=python" && exit /b 0
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do (
    if exist "%%~fD\python.exe" "%%~fD\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PYTHON_CMD="%%~fD\python.exe""
)
exit /b 0

:remove_venv
echo The .venv environment is invalid or was copied from another PC. Recreating it.
if /i not "%VENV_DIR%"=="%CD%\.venv" goto :venv_error
rmdir /s /q "%VENV_DIR%"
exit /b 0

:python_error
echo.
echo ERROR: Python 3.11 or newer could not be found or installed.
echo Install Python from python.org and run start.bat again.
pause
exit /b 1

:venv_error
echo.
echo ERROR: The .venv environment could not be created.
pause
exit /b 2

:packages_error
echo.
echo ERROR: Dependencies could not be installed. Check the internet connection.
pause
exit /b 3

:config_error
echo.
echo ERROR: config.example.toml is missing.
pause
exit /b 4
