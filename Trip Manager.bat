@echo off
cd /d "%~dp0"

if exist "env\Scripts\pythonw.exe" (
    start "" "env\Scripts\pythonw.exe" "scripts\trip_manager.py"
) else if exist "env\Scripts\python.exe" (
    start "" "env\Scripts\python.exe" "scripts\trip_manager.py"
) else (
    echo Setup has not been run yet.
    echo Please run setup.bat first.
    pause
)
