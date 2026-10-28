@ECHO off
setlocal enabledelayedexpansion

:: Serve the docs with the agent knowledge base, like `make dev`, with Windows' own Python, in venv-windows (`make`
:: uses venv-linux; ADR-0029). Open http://127.0.0.1:4000/ in a browser; Ctrl+C stops the server.

:: Change to the directory where this script lives
cd /d "%~dp0"

:: Read the version from extension.toml: exactly one line starting with `version =`, as read-version.sh requires
set "VERSION_FILE=..\extension\exts\amt.dtp.ise\config\extension.toml"
set VERSION_COUNT=0
FOR /F "tokens=2 delims== " %%V IN ('findstr /R /B /C:"version *=" "%VERSION_FILE%"') DO (
    set "BUILD_VERSION=%%~V"
    set /A VERSION_COUNT+=1
)
IF NOT "!VERSION_COUNT!"=="1" (
    echo Error: expected exactly one version assignment in %VERSION_FILE%, found !VERSION_COUNT!
    IF "%~1"=="" pause
    exit /b 1
)

:: Find Python (the same probe as the coprocessor's install.bat)
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
    echo Creating docs venv...
    !PYTHON_CMD! -m venv venv-windows || (
        echo Error: Could not create docs venv
        IF "%~1"=="" pause
        exit /b 1
    )
    echo Created venv
)

:: Install requirements when requirements.txt changed since the last install (a copy is kept in the venv)
fc /b requirements.txt venv-windows\requirements.txt >NUL 2>&1 || (
    echo Installing docs requirements...
    .\venv-windows\Scripts\python.exe -m pip install -Ur requirements.txt || (
        echo Error: Could not install docs requirements
        IF "%~1"=="" pause
        exit /b 1
    )
    copy /y requirements.txt venv-windows\requirements.txt >NUL
)

set "SITE_NAME=AMT DTP Isaac Sim Service (ISS) v!BUILD_VERSION!"
echo SITE_NAME: !SITE_NAME!
.\venv-windows\Scripts\mkdocs.exe serve --livereload -a 127.0.0.1:4000 -f mkdocs.dev.yml || (
    echo Error: mkdocs serve failed
    IF "%~1"=="" pause
    exit /b 1
)

:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
