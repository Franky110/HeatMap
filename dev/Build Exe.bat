@echo off
cd /d "%~dp0.."

if exist "env\Scripts\python.exe" (
    "env\Scripts\python.exe" "dev\build_exe.py"
) else (
    py -3 "dev\build_exe.py" 2>nul || python "dev\build_exe.py"
)

echo.
pause
