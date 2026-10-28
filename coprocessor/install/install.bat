:: This script creates a python virtual environment (venv-windows) and installs the
::  coprocessor in it. The extension runs it on the coprocessor's first start (ADR-0030);
::  `no_pause` (the first argument) skips every pause, including on errors.

@echo off
setlocal enabledelayedexpansion

:: Change to the directory where this script lives
cd /d "%~dp0"

IF EXIST "amt_dtp_iscp-*.whl" (
    echo amt_dtp_iscp-*.whl exists.
) ELSE (
    echo Error: amt_dtp_iscp-*.whl does not exist
    IF "%~1"=="" pause
    exit /b 1
)

echo Detecting python executable...
set PYTHON_CMD=
for %%P in (python py python3) do (
    if "!PYTHON_CMD!"=="" (
        %%P --version >nul 2>&1 && set PYTHON_CMD=%%P
    )
)
if "!PYTHON_CMD!"=="" (
    echo Error: No Python executable found. Ensure Python is installed and on PATH.
    IF "%~1"=="" pause
    exit /b 1
)
echo Python executable detected: !PYTHON_CMD!

IF EXIST "venv-windows\Scripts\python.exe" (
    echo coprocessor venv already exists.
) ELSE (
    echo Creating coprocessor venv...
    !PYTHON_CMD! -m venv "venv-windows" || (
        echo Error: Could not create coprocessor venv
        IF "%~1"=="" pause
        exit /b 1
    )
    echo Created coprocessor venv
)

:: Workaround for windows build issue with cmake
echo Installing manifold3d in coprocessor venv...
"venv-windows\Scripts\pip.exe" install manifold3d --config-settings=cmake.args="-DMANIFOLD_PAR=NONE" || (
    echo Error: Could not install manifold3d in coprocessor venv
    IF "%~1"=="" pause
    exit /b 1
)
echo Installed manifold3d in coprocessor venv

echo Installing coprocessor in coprocessor venv...
FOR %%i IN (*.whl) DO (
    ECHO Installing %%i in coprocessor venv
    "venv-windows\Scripts\pip.exe" install "%%i" || (
        echo Error: Could not install python wheel in coprocessor venv: %%i
        IF "%~1"=="" pause
        exit /b 1
    )
    echo Installed %%i in coprocessor venv
)
echo Installed coprocessor in coprocessor venv

echo coprocessor installed successfully.
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
