@ECHO off

:: Remove the built site (like `make clean`). The venv is kept; distclean.bat removes it too.
:: RD doesn't set ERRORLEVEL when it fails, so the removal is checked afterwards; and an `exit /b 1` inside the same
:: block as a failed RD returns 0 to `cmd /c`, so a failure is flagged and exits after it.

:: Change to the directory where this script lives
cd /d "%~dp0"

set FAILED=
IF EXIST "dist" (
    echo Removing docs dist...
    RD /S /Q "dist"
    IF EXIST "dist" ( set FAILED=1 ) ELSE ( echo Removed docs dist )
)
IF DEFINED FAILED (
    echo Error: Could not remove "dist" completely: a program still has something in it open. Close it and run this again.
    IF "%~1"=="" pause
    exit /b 1
)

echo Clean docs completed successfully
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
