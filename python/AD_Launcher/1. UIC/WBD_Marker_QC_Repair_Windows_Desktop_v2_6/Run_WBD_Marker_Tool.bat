@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo The local Python environment has not been installed yet.
    echo Run Setup_and_Run.bat first.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "wbd_marker_windows_gui.pyw"
exit /b 0
