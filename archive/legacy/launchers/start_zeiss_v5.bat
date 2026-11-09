@echo off
setlocal
cd /d "%~dp0"
if not exist "mm38\Scripts\python.exe" (
  echo ERROR: Cannot find mm38\Scripts\python.exe in this folder.
  echo Place this BAT next to your v5 script and mm38 environment.
  pause
  exit /b 1
)
"mm38\Scripts\python.exe" "zeiss_headless_complete_v5.py" %*
if errorlevel 1 pause
endlocal
