@echo off
setlocal
cd /d "%~dp0"

echo Trip Manager setup
echo ===================
echo.

rem --- Find a Python interpreter -------------------------------------------
set "PYLAUNCHER="

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 --version >nul 2>nul
    if %errorlevel%==0 set "PYLAUNCHER=py -3"
)

if not defined PYLAUNCHER (
    where python >nul 2>nul
    if %errorlevel%==0 set "PYLAUNCHER=python"
)

if not defined PYLAUNCHER (
    echo Python was not found on this computer.
    echo.
    where winget >nul 2>nul
    if %errorlevel%==0 (
        echo Installing Python 3.12 via winget. This may take a few minutes...
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
        echo.
        echo Python has been installed. Please close this window and run
        echo setup.bat again so it can be found.
    ) else (
        echo Please install Python from https://www.python.org/downloads/
        echo During installation, make sure to check "Add python.exe to PATH".
        echo Then run this script again.
        start https://www.python.org/downloads/
    )
    echo.
    pause
    exit /b 1
)

echo Using: %PYLAUNCHER%
echo.

rem --- Create / reuse the virtual environment -------------------------------
if not exist "env\Scripts\python.exe" (
    echo Creating virtual environment in "env"...
    %PYLAUNCHER% -m venv env
    if errorlevel 1 (
        echo.
        echo Failed to create the virtual environment.
        pause
        exit /b 1
    )
) else (
    echo Reusing existing virtual environment in "env".
)

echo.
echo Installing required packages, this can take a few minutes...
"env\Scripts\python.exe" -m pip install --upgrade pip
"env\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Failed to install required packages. See the messages above.
    pause
    exit /b 1
)

echo.
echo Setup complete!
echo You can now start the program with "Trip Manager.bat".
echo.
pause
