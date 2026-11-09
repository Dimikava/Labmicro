@echo off
setlocal
cd /d "%~dp0"
if not exist "mm38\Scripts\python.exe" (
    echo This launcher requires the existing mm38 virtual environment next to it.
    echo Or run: python zeiss_headless_complete_v7.py --config zeiss_camera_working.cfg
    exit /b 2
)
"mm38\Scripts\python.exe" "zeiss_headless_complete_v7.py" --config "zeiss_camera_working.cfg" %*
endlocal
