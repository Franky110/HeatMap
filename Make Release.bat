@echo off
cd /d "%~dp0"

if exist "env\Scripts\python.exe" (
    "env\Scripts\python.exe" "scripts\make_release.py"
) else (
    py -3 "scripts\make_release.py" 2>nul || python "scripts\make_release.py"
)

echo.
pause
