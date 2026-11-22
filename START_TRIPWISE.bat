@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. See README.md for setup.
  pause
  exit /b 1
)
if not defined MOSCOW_OSRM_FOOT if not defined MOSCOW_OSRM_CAR call :ensure_moscow_osrm
curl.exe -fsS --max-time 2 http://127.0.0.1:8765/api/health >nul 2>&1
if not errorlevel 1 (
  start "" "http://127.0.0.1:8765/"
  exit /b 0
)
echo Open http://127.0.0.1:8765/ after startup. Keep this window open.
".venv\Scripts\python.exe" -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
if errorlevel 1 pause
exit /b

:ensure_moscow_osrm
set "graphs=%MOSCOW_OSRM_GRAPH_DIR%"
if not defined graphs set "graphs=runtime\moscow-osrm"
if not exist "%graphs%\foot\map.osrm.mldgr" exit /b
if not exist "%graphs%\car\map.osrm.mldgr" exit /b
curl.exe -fsS --max-time 2 "http://127.0.0.1:5001/nearest/v1/driving/37.62,55.752" >nul 2>&1
if not errorlevel 1 (
  curl.exe -fsS --max-time 2 "http://127.0.0.1:5002/nearest/v1/driving/37.62,55.752" >nul 2>&1
  if not errorlevel 1 exit /b
)
where docker.exe >nul 2>&1 || exit /b
docker info --format "{{.ServerVersion}}" >nul 2>&1 || (
  echo Starting local Docker for Moscow road routing...
  docker desktop start --detach >nul 2>&1 || exit /b
)
for /l %%i in (1,1,12) do (
  docker info --format "{{.ServerVersion}}" >nul 2>&1 && goto :start_moscow_osrm
  timeout /t 2 /nobreak >nul
)
exit /b

:start_moscow_osrm
docker compose -f compose.moscow-osrm.yaml up -d --pull never --no-build --no-recreate >nul 2>&1
if errorlevel 1 echo Local Moscow routing could not start. The planner will label estimated legs.
exit /b
