@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo Build standalone WBD_Marker_QC_Repair.exe
echo ============================================================

echo This build must be run on a Windows computer.

set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    where python >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo Python was not found. Install 64-bit Python 3.10 or newer first.
    pause
    exit /b 1
)

if not exist ".buildvenv\Scripts\python.exe" (
    echo Creating build environment...
    %PYTHON_CMD% -m venv .buildvenv
    if errorlevel 1 goto :error
)

call ".buildvenv\Scripts\activate.bat"
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -r requirements-build.txt
if errorlevel 1 goto :error

python -m PyInstaller --noconfirm --clean WBD_Marker_QC_Repair.spec
if errorlevel 1 goto :error

echo.
echo Build complete:
echo   %CD%\dist\WBD_Marker_QC_Repair.exe
echo.
pause
exit /b 0

:error
echo.
echo Build failed. Review the messages above.
pause
exit /b 1
