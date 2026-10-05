@echo off
rem Build ZonalStats.exe (one-file Windows GUI) with PyInstaller.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3.12 -m venv .venv || python -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

rem OneDrive sets the read-only attribute on synced folders, which makes
rem PyInstaller's own --clean fail with "Access is denied" on build\. Clear the
rem attribute and drop the cache ourselves instead.
if exist "build" (
    echo Clearing previous build cache...
    attrib -R "build" /S /D >nul 2>&1
    rmdir /s /q "build"
)
if exist "dist" attrib -R "dist" /S /D >nul 2>&1

echo Building executable...
".venv\Scripts\pyinstaller.exe" ZonalStats.spec --noconfirm
if errorlevel 1 (
    echo.
    echo BUILD FAILED - see the messages above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo Done. Your program is at:  dist\ZonalStats.exe
echo Keep sensors.json and indices.json next to the .exe if you
echo want to edit sensors/indices without rebuilding.
echo ============================================================
pause
