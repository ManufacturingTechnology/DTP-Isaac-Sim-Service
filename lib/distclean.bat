@ECHO off

:: Everything clean.bat removes, plus every venv: both OSes' (ADR-0029) and the pre-ADR names, like `make distclean`.
:: RD doesn't set ERRORLEVEL when it fails (e.g. a file is in use), so each removal is checked afterwards; and an
:: `exit /b 1` inside the same block as a failed RD returns 0 to `cmd /c`, so failures are flagged and exit after it.

:: Change to the directory where this script lives
cd /d "%~dp0"

call "%~dp0clean.bat" no_pause || (
    echo Error: Could not clean lib
    IF "%~1"=="" pause
    exit /b 1
)

set FAILED=
FOR %%v IN (venv-windows venv-windows-test venv-linux venv-linux-test venv venv-test) DO (
    IF EXIST "%%v" (
        echo Removing lib %%v...
        RD /S /Q "%%v"
        IF EXIST "%%v" ( set "FAILED=%%v" ) ELSE ( echo Removed lib %%v )
    )
)
IF DEFINED FAILED (
    echo Error: Could not remove "%FAILED%" completely: a program still has something in it open, for example an editor's file search or a Python process. Close it and run this again.
    IF "%~1"=="" pause
    exit /b 1
)

echo Distclean lib completed successfully
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
