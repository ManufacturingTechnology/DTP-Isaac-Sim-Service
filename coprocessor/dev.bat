@echo off
setlocal enabledelayedexpansion

:: Set the coprocessor up for development on Windows: the lib link and venv-windows (Linux: `make dev`, venv-linux;
:: ADR-0029). The venv is kept and reinstalled only when pyproject.toml changes (a copy is kept in the venv).

:: Change to the directory where this script lives
cd /d "%~dp0"

call "%~dp0clean.bat" no_pause || (
    echo Error: Could not clean coprocessor
    IF "%~1"=="" pause
    exit /b 1
)

:: Change to the directory where this script lives
cd /d "%~dp0"

:: Create symbolic link from the source (master copy) to destination cosim client python file
IF NOT EXIST "src\amt\dtp\iscp\lib" (
    echo Creating symbolic link...
    mklink /J "src\amt\dtp\iscp\lib" "..\lib\src\amt\dtp\ise\lib" || (
        echo Error: Could not create symbolic link "src\amt\dtp\iscp\lib"
        IF "%~1"=="" pause
        exit /b 1
    )
    echo Created symbolic link
)

:: Find Python (the same probe as install\install.bat)
set PYTHON_CMD=
FOR %%P IN (python py python3) DO (
    IF "!PYTHON_CMD!"=="" (
        %%P --version >NUL 2>&1 && set PYTHON_CMD=%%P
    )
)
IF "!PYTHON_CMD!"=="" (
    echo Error: No Python executable found. Ensure Python is installed and on PATH.
    IF "%~1"=="" pause
    exit /b 1
)

:: Create the virtual environment if it doesn't exist
IF NOT EXIST "venv-windows\Scripts\python.exe" (
    echo Creating coprocessor venv...
    !PYTHON_CMD! -m venv venv-windows || (
        IF EXIST "venv-windows" RD /S /Q "venv-windows"
        echo Error: Could not create coprocessor venv
        IF "%~1"=="" pause
        exit /b 1
    )
    echo Created venv
)

:: Install requirements when pyproject.toml changed since the last install
fc /b pyproject.toml venv-windows\pyproject.toml >NUL 2>&1 || (
    echo Installing coprocessor requirements...
    REM Workaround for windows, doesn't build otherwise
    .\venv-windows\Scripts\pip.exe install manifold3d --config-settings=cmake.args="-DMANIFOLD_PAR=NONE" || (
        echo Error: Could not install manifold3d in coprocessor venv
        IF "%~1"=="" pause
        exit /b 1
    )
    .\venv-windows\Scripts\pip.exe install -e ".[dev]" || (
        echo Error: Could not install coprocessor venv requirements
        IF "%~1"=="" pause
        exit /b 1
    )
    copy /y pyproject.toml venv-windows\pyproject.toml >NUL
    echo Installed coprocessor requirements
)

echo Set up coprocessor for development successfully
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
