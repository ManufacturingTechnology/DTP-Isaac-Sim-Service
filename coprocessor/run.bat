@echo off

:: Run the coprocessor from source in venv-windows (set up by dev.bat).

:: Change to the directory where this script lives
cd /d "%~dp0"

echo Running coprocessor...
"venv-windows\Scripts\python.exe" -m "src.amt.dtp.iscp.coprocessor" || (
    echo Error: Could not run coprocessor.
    IF "%~1"=="" pause
    exit /b 1
)
echo Ran coprocessor successfully.
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
