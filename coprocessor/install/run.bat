:: This script runs the coprocessor installed in venv-windows (by install.bat).

@echo off

:: Change to the directory where this script lives
cd /d "%~dp0"

IF NOT EXIST "venv-windows\Scripts\amt-dtp-iscp.exe" (
    echo Error: venv-windows\Scripts\amt-dtp-iscp.exe does not exist.
    echo Run install.bat first.
    IF "%~1"=="" pause
    exit /b 1
)

echo Running coprocessor...
"venv-windows\Scripts\amt-dtp-iscp.exe" || (
    echo Error: Could not run coprocessor.
    IF "%~1"=="" pause
    exit /b 1
)
echo Ran coprocessor successfully.
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
