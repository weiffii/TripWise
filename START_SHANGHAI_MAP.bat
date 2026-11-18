@echo off
setlocal
cd /d "%~dp0"
set "TRIPWISE_ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$root = $env:TRIPWISE_ROOT; $runtime = 'C:\msys64\ucrt64\bin\python.exe'; if (-not (Test-Path -LiteralPath $runtime)) { $found = Get-Command py.exe -ErrorAction SilentlyContinue; if (-not $found) { $found = Get-Command python.exe -ErrorAction SilentlyContinue }; if (-not $found) { throw 'Python is not installed. Open artifacts/tripwise-shanghai.html instead.' }; $runtime = $found.Source }; $script = Join-Path $root 'scripts\serve_shanghai_demo.py'; Start-Process -WindowStyle Hidden -FilePath $runtime -ArgumentList (([char]34) + $script + ([char]34)) -WorkingDirectory $root"
if errorlevel 1 pause
endlocal
