@echo off
cd /d "%~dp0"
if not exist "mm38\Scripts\python.exe" (
  echo Cannot find mm38\Scripts\python.exe
  pause
  exit /b 1
)
"mm38\Scripts\python.exe" zeiss_headless_complete_v6_1.py --config zeiss_only.cfg --debug
pause
