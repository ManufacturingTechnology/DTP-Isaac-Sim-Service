@ECHO off
setlocal enabledelayedexpansion

:: Test the built wheel (with the `server` extra the API tests need), not the source tree, like `make test`. The test
:: venv (venv-windows-test; Linux uses venv-linux-test, ADR-0029) is kept between runs; distclean.bat removes it.

:: Change to the directory where this script lives
cd /d "%~dp0"

:: Build package
call "%~dp0build.bat" no_pause || (
    echo Error: Could not build lib
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

:: Create the test venv if it doesn't exist
IF NOT EXIST "venv-windows-test\Scripts\python.exe" (
    echo Creating lib test venv...
    !PYTHON_CMD! -m venv venv-windows-test || (
        echo Error: Could not create lib test venv
        IF "%~1"=="" pause
        exit /b 1
    )
)

:: Install the wheel with the `server` extra, and pytest
set WHEEL=
FOR %%i IN (dist\*.whl) DO set "WHEEL=%%i"
IF "!WHEEL!"=="" (
    echo Error: No wheel in dist
    IF "%~1"=="" pause
    exit /b 1
)
.\venv-windows-test\Scripts\python.exe -m pip install "!WHEEL![server]" pytest || (
    echo Error: Could not install !WHEEL! in the lib test venv
    IF "%~1"=="" pause
    exit /b 1
)
:: Same version number, new contents: reinstall our package itself (the test venv is kept between runs)
.\venv-windows-test\Scripts\python.exe -m pip install --force-reinstall --no-deps "!WHEEL!" || (
    echo Error: Could not reinstall !WHEEL! in the lib test venv
    IF "%~1"=="" pause
    exit /b 1
)

:: Run Tests
.\venv-windows-test\Scripts\python.exe -m pytest tests
set TEST_RESULT=!ERRORLEVEL!

:: Clean build outputs (keeps the venvs)
call "%~dp0clean.bat" no_pause

:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
exit /b !TEST_RESULT!
