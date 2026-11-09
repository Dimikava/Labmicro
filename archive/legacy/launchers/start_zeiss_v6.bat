@echo off
setlocal
cd /d "%~dp0"
if not exist "mm38\Scripts\python.exe" (
  echo ERROR: mm38\Scripts\python.exe missing. Extract v6 into your existing microGod folder.
  pause
  exit /b 1
)
"mm38\Scripts\python.exe" "zeiss_headless_complete_v6.py" %*
echo.
pause
endlocal
