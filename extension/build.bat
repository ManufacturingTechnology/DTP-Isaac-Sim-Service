:: This script runs the extension build in the default wsl environment, so Windows and Linux share one build process.
:: The build creates no coprocessor venv: the extension creates it on the coprocessor's first start (ADR-0030).

@echo off
setlocal enabledelayedexpansion

:: Change to the directory where this script lives
cd /d "%~dp0"

echo Running make in wsl...
wsl bash -c "cd '$(wslpath '%~dp0')' && make"
if !ERRORLEVEL! NEQ 0 (
    echo Error: Could not run make in wsl
    IF "%~1"=="" pause
    exit /b 1
)
echo Ran make in wsl

echo Built extension successfully
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
