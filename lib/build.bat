@ECHO off

:: Change to the directory where this script lives
cd /d "%~dp0"

::call clean.bat no_pause || (
::    echo Error: Could not clean coprocessor
::    pause
::    exit /b %ERRORLEVEL%
::)

:: Change to the directory where this script lives
cd /d "%~dp0"

echo Building lib...

::IF EXIST "dist" (
::    echo dist directory already exists
::) ELSE (
::    echo Creating dist directory...
::    mkdir dist
::    echo Created dist directory
::)


IF EXIST "dist\amt_dtp_ise_lib*.whl" (
    echo dist\amt-dtp-ise*.whl already exists
) ELSE (
    :: Convert Windows path to WSL path and run make
    FOR /F "delims=" %%P IN ('wsl wslpath -a "%~dp0"') DO SET WSL_PATH=%%P
    echo WSL_PATH=%WSL_PATH%
    echo Running make in wsl...
    wsl -e bash -c "cd '%WSL_PATH%' && make" || (
        echo Error: Could not run make in wsl
        pause
        exit /b %ERRORLEVEL%
    )
    echo Ran make in wsl
)



echo Build lib completed successfully
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)