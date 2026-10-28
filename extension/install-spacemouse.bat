@echo off

if exist "exts\srl.spacemouse" (
	echo Already installed
	exit /b 0
)

:: A fork of NVlabs/spacemouse-extension (MIT): the upstream extension doesn't work with current Isaac Sim;
:: the fork does. Temporary: the extension's own native SpaceMouse support replaces this script.
::git clone "https://github.com/NVlabs/spacemouse-extension" "exts/srl.spacemouse"
git clone "https://github.com/m-r-mccormick/spacemouse-extension" "exts/srl.spacemouse"
if %ERRORLEVEL% neq 0 (
	echo Error: could not clone spacemouse-extension
	pause
	exit /b 1
)

echo Installed spacemouse extension successfully.
:: Pause if arg not specified
IF "%~1"=="" (
    pause
)
