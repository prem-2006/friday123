@echo off
rem Stops Friday completely: the notch, the app (ports 5173 and 4310) and the services (port 8765).
echo Stopping Friday...
taskkill /IM electron.exe /T /F >nul 2>&1
for %%P in (5173 4310 8765) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /R /C:":%%P .*LISTENING"') do taskkill /PID %%I /T /F >nul 2>&1
)
echo Friday is stopped. Double-click "Start Friday" to start it again.
timeout /t 3 >nul