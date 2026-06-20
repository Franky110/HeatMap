@echo off
cd /d "%~dp0"

if exist "env\Scripts\python.exe" (
    "env\Scripts\python.exe" "scripts\build_exe.py"
) else (
    py -3 "scripts\build_exe.py" 2>nul || python "scripts\build_exe.py"
)

echo.
pause
