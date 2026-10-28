@echo off

:: Remove the lib link. Venvs are kept (like `make clean`); distclean.bat removes them too.
:: RD doesn't set ERRORLEVEL when it fails, so the removal is checked afterwards; and an `exit /b 1` inside the same
:: block as a failed RD returns 0 to `cmd /c`, so a failure is flagged and exits after it.

:: Change to the directory where this script lives
cd /d "%~dp0"

:: Delete symbolic link if it exists (RD on a junction removes the link, not the lib it points to)
set FAILED=
IF EXIST "src\amt\dtp\iscp\lib" (
    echo Removing coprocessor library...
    RD /S /Q "src\amt\dtp\iscp\lib"
    IF EXIST "src\amt\dtp\iscp\lib" ( set FAILED=1 ) ELSE ( echo Removed coprocessor library )
)
IF DEFINED FAILED (
    echo Error: Could not remove coprocessor symbolic link "src\amt\dtp\iscp\lib"
    IF "%~1"=="" pause
    exit /b 1
)

echo Clean coprocessor completed successfully
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
