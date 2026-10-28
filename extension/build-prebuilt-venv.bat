:: EXPERIMENTAL, not used for releases (ADR-0030). Builds the extension, then installs the coprocessor's venv-windows
::  into the build folder and runs the makefile's package-prebuilt-venv target, which is meant to package the
::  extension with that venv pre-built. Kept so the idea can be revisited; the normal build (build.bat, `make`) never
::  creates a coprocessor venv.
::
:: Not usable as is:
::  - the zip is made by `make` before the venv is installed, and package-prebuilt-venv's zip steps are commented out,
::    so no artifact contains the venv;
::  - a venv records the absolute path of the Python that created it, so a venv built here may point at a Python that
::    doesn't exist on the user's machine (Python's install location varies, especially on Windows);
::  - shipping the venv ships every package the coprocessor depends on with the extension.

@echo off
setlocal enabledelayedexpansion

:: Change to the directory where this script lives
cd /d "%~dp0"

call "%~dp0build.bat" no_pause || (
    echo Error: Could not build extension
    IF "%~1"=="" pause
    exit /b 1
)

:: Change to the directory where this script lives
cd /d "%~dp0"

echo Installing coprocessor venv for windows...
call "exts\amt.dtp.ise\coprocessor\install.bat" no_pause || (
    echo Error: Could not install coprocessor venv for windows.
    IF "%~1"=="" pause
    exit /b 1
)
echo Installed coprocessor venv for windows

:: Change to the directory where this script lives
cd /d "%~dp0"

echo Running make package-prebuilt-venv in wsl...
wsl bash -c "cd '$(wslpath '%~dp0')' && make package-prebuilt-venv"
if !ERRORLEVEL! NEQ 0 (
    echo Error: Could not run make package-prebuilt-venv in wsl
    IF "%~1"=="" pause
    exit /b 1
)
echo Ran make package-prebuilt-venv in wsl

echo Built extension with a pre-built coprocessor venv (experimental)
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
