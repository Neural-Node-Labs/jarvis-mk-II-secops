@echo off
setlocal enabledelayedexpansion

rem Resolve paths
set "SCRIPT_DIR=%~dp0"
set "BACKEND_DIR=%SCRIPT_DIR%..\backend"
set "VENV_DIR=%BACKEND_DIR%\.venv"
set "REQ_FILE=%BACKEND_DIR%\requirements.txt"

if not exist "%BACKEND_DIR%" (
    echo Error: Backend directory not found at %BACKEND_DIR%
    exit /b 1
)

rem Create virtual environment if missing
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating Python virtual environment...
    python -m venv "%VENV_DIR%"
)

rem Activate
call "%VENV_DIR%\Scripts\activate.bat"

rem Install dependencies
if exist "%REQ_FILE%" (
    echo Installing/updating dependencies...
    pip install -q -r "%REQ_FILE%"
) else (
    echo Warning: requirements.txt not found; skipping dependency install.
)

rem Run console, forwarding all arguments
python "%BACKEND_DIR%\main.py" &

rem Run console, forwarding all arguments
python "%BACKEND_DIR%\console.py" %*
