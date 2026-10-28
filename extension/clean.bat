@echo off

:: Remove build outputs and the dev links (lib and coprocessor are junctions in dev mode, copies in a build).
:: RD doesn't set ERRORLEVEL when it fails (e.g. a file is in use), so each removal is checked afterwards; and an
:: `exit /b 1` inside the same block as a failed RD returns 0 to `cmd /c`, so failures are flagged and exit after it.
:: RD on a junction removes only the link, never the folder it points to.

:: Change to the directory where this script lives
cd /d "%~dp0"

echo Cleaning extension...

set FAILED=
FOR %%d IN (dist exts\amt.dtp.ise\amt\dtp\ise\lib exts\amt.dtp.ise\coprocessor exts\amt.dtp.ise\docs exts\amt.dtp.ise\pip_prebundle) DO (
    IF EXIST "%%d" (
        echo Removing extension %%d...
        RD /S /Q "%%d"
        IF EXIST "%%d" ( set "FAILED=%%d" ) ELSE ( echo Removed extension %%d )
    )
)
IF DEFINED FAILED (
    echo Error: Could not remove "%FAILED%" completely: a program still has something in it open, for example an editor's file search. Close it and run this again.
    IF "%~1"=="" pause
    exit /b 1
)

echo Clean extension completed successfully
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
