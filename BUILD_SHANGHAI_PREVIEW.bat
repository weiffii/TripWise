@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (
  py -3 scripts\build_shanghai_demo.py --fetch-base
  goto :check
)
where python >nul 2>&1
if not errorlevel 1 (
  python scripts\build_shanghai_demo.py --fetch-base
  goto :check
)
if exist "C:\msys64\ucrt64\bin\python.exe" (
  "C:\msys64\ucrt64\bin\python.exe" scripts\build_shanghai_demo.py --fetch-base
  goto :check
)
echo Python 3 not found. Install Python 3 or run scripts\build_shanghai_demo.py manually.
pause
exit /b 1
:check
if errorlevel 1 (
  echo.
  echo Build failed. Verify Python and GitHub connectivity.
  pause
  exit /b 1
)
start "" "artifacts\tripwise-shanghai.html"
