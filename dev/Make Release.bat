@echo off
cd /d "%~dp0.."

if exist "env\Scripts\python.exe" (
    "env\Scripts\python.exe" "dev\make_release.py"
) else (
    py -3 "dev\make_release.py" 2>nul || python "dev\make_release.py"
)

echo.
pause
