@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. See README.md for setup.
  pause
  exit /b 1
)
curl.exe -fsS --max-time 2 http://127.0.0.1:8765/api/health >nul 2>&1
if not errorlevel 1 (
  start "" "http://127.0.0.1:8765/"
  exit /b 0
)
echo Open http://127.0.0.1:8765/ after startup. Keep this window open.
if not defined MOSCOW_OSRM_FOOT (
  curl.exe -fsS --max-time 2 "http://127.0.0.1:5001/nearest/v1/driving/37.62,55.752" >nul 2>&1
  if not errorlevel 1 set "MOSCOW_OSRM_FOOT=http://127.0.0.1:5001/route/v1/driving"
)
if not defined MOSCOW_OSRM_CAR (
  curl.exe -fsS --max-time 2 "http://127.0.0.1:5002/nearest/v1/driving/37.62,55.752" >nul 2>&1
  if not errorlevel 1 set "MOSCOW_OSRM_CAR=http://127.0.0.1:5002/route/v1/driving"
)
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
if errorlevel 1 pause
