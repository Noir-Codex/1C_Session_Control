@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "VENV_PYTHON=%CD%\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
    echo Creating Python virtual environment...
    py -3.14 -m venv .venv 2>nul
    if errorlevel 1 python -m venv .venv
)

if not exist "%VENV_PYTHON%" (
    echo ERROR: Python virtual environment could not be created.
    pause
    exit /b 1
)

"%VENV_PYTHON%" -c "import fastapi, jinja2, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo Installing required packages...
    "%VENV_PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Required packages could not be installed.
        pause
        exit /b 1
    )
)

"%VENV_PYTHON%" launcher.py
if errorlevel 1 pause

endlocal
