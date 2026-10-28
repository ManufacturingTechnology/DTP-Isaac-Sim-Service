@echo off

:: Change to the directory where this script lives
cd /d "%~dp0"

:: Clean lib for development
call ../lib/clean.bat no_pause || (
    echo Error: Could not clean lib
    pause
    exit /b %ERRORLEVEL%
)

:: Set the coprocessor up for development
call ../coprocessor/dev.bat no_pause || (
    echo Error: Could not dev coprocessor
    pause
    exit /b %ERRORLEVEL%
)

:: Change to the directory where this script lives
cd /d "%~dp0"

call "%~dp0clean.bat" no_pause || (
    echo Error: Could not clean extension
    pause
    exit /b %ERRORLEVEL%
)

:: Change to the directory where this script lives
cd /d "%~dp0"

:: Move to extension directory
cd "exts\amt.dtp.ise\" || (
    echo Error: Could not change directory to "exts\amt.dtp.ise\"
    pause
    exit /b %ERRORLEVEL%
)

:: Create virtual environment and install requirements
echo Creating extension venv...
python -m pip install -r config/requirements.txt --target ./pip_prebundle || (
    echo Error: Could not create pip_prebundle python virtual environment
    pause
    exit /b %ERRORLEVEL%
)
echo Created extension venv

:: Create symbolic link from the source (master copy) to destination library
mklink /J "amt\dtp\ise\lib" "..\..\..\lib\src\amt\dtp\ise\lib" || (
    echo Error: Could not create symbolic link "amt\dtp\ise\lib"
    pause
    exit /b %ERRORLEVEL%
)

:: Create symbolic link from the source (master copy) to destination coprocessor
mklink /J "coprocessor" "..\..\..\coprocessor\" || (
    echo Error: Could not create symbolic link "coprocessor"
    pause
    exit /b %ERRORLEVEL%
)

echo Set up extension for development successfully
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
