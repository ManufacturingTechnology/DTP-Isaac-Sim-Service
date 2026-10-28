@ECHO off

:: Remove build outputs. Venvs are kept (like `make clean`); distclean.bat removes them too.
:: RD doesn't set ERRORLEVEL when it fails (e.g. a file is in use), so each removal is checked afterwards; and an
:: `exit /b 1` inside the same block as a failed RD returns 0 to `cmd /c`, so failures are flagged and exit after it.

:: Change to the directory where this script lives
cd /d "%~dp0"

echo Cleaning lib...

set FAILED=
FOR %%d IN (dist .pytest_cache) DO (
    IF EXIST "%%d" (
        echo Removing lib %%d...
        RD /S /Q "%%d"
        IF EXIST "%%d" ( set "FAILED=%%d" ) ELSE ( echo Removed lib %%d )
    )
)
:: Remove egg-infos (directories, so FOR /D)
FOR /D %%d IN (src\*.egg-info) DO (
    echo Removing lib %%d...
    RD /S /Q "%%d"
    IF EXIST "%%d" ( set "FAILED=%%d" ) ELSE ( echo Removed lib %%d )
)
IF DEFINED FAILED (
    echo Error: Could not remove "%FAILED%" completely: a program still has something in it open, for example an editor's file search. Close it and run this again.
    IF "%~1"=="" pause
    exit /b 1
)

echo Clean lib completed successfully
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
