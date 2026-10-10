@echo off
rem Starts Kestrel on Windows: double-click this file, or run start.cmd in a terminal.
setlocal
cd /d "%~dp0"

where uv >nul 2>nul || (echo Kestrel needs uv. Install it from https://docs.astral.sh/uv/ and run this again. & pause & exit /b 1)
where node >nul 2>nul || (echo Kestrel needs Node.js 20 or newer. Install it from https://nodejs.org and run this again. & pause & exit /b 1)

echo Preparing the backend...
pushd backend
call uv sync || (popd & pause & exit /b 1)
popd

if not exist frontend\node_modules (
  echo Installing the dashboard, first run only...
  pushd frontend
  call npm.cmd install || (popd & pause & exit /b 1)
  popd
)

rem The dashboard runs as an optimised production build (no developer tools or overlays).
echo Building the dashboard...
pushd frontend
set NEXT_TELEMETRY_DISABLED=1
call npm.cmd run build || (popd & pause & exit /b 1)
popd

rem npm.cmd instead of npm, so PowerShell's script execution policy never gets in the way.
start "Kestrel backend" /D backend cmd /k uv run kestrel
start "Kestrel dashboard" /D frontend cmd /k npm.cmd start

echo Opening http://localhost:3000 ...
timeout /t 5 /nobreak >nul
start "" http://localhost:3000
echo Kestrel is running in two new windows. Close them to stop it.
