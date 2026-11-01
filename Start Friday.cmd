@echo off
rem Starts Friday: the notch, which starts the app (localhost 5173 and 4310) and the services (8765).
rem A Friday already running is stopped first, so every start runs the latest code.
rem Double-click this from File Explorer (not from inside another app), so Friday can use the microphone.
set "PATH=C:\Program Files\nodejs;%PATH%"
set "FRIDAY=%~dp0"
if "%FRIDAY:~-1%"=="\" set "FRIDAY=%FRIDAY:~0,-1%"
set "DESKTOP=%FRIDAY%\friday\desktop"
set "ELECTRON=%DESKTOP%\node_modules\electron\dist\electron.exe"
rem The notch must not hang on to this window: closing it would stop Friday.
set "ELECTRON_NO_ATTACH_CONSOLE=1"
if not exist "%FRIDAY%\data" mkdir "%FRIDAY%\data"
echo %date% %time% Start Friday.cmd ran (by %USERNAME%) >> "%FRIDAY%\data\start-friday.log"

if not exist "%ELECTRON%" (
  echo The notch is not installed. In "%DESKTOP%" run: npm install
  pause
  exit /b 1
)

rem Only Friday's own notch, not other Electron apps.
set "NOTCHES=Get-Process electron -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '%DESKTOP%\*' }"
set "WAS="
for /f %%N in ('powershell -NoProfile -Command "@(%NOTCHES%).Count"') do if not "%%N"=="0" set "WAS=1"
powershell -NoProfile -Command "%NOTCHES% | Stop-Process -Force -ErrorAction SilentlyContinue"
for %%P in (5173 4310 8765) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /R /C:":%%P .*LISTENING"') do (
    set "WAS=1"
    taskkill /PID %%I /T /F >nul 2>&1
  )
)
if defined WAS (
  echo Friday was already running: restarting it.
  rem Wait until the old notch is fully gone, or the new one would see it and quit.
  powershell -NoProfile -Command "for ($i = 0; $i -lt 20 -and @(%NOTCHES%).Count; $i++) { Start-Sleep -Milliseconds 500 }"
)

cd /d "%DESKTOP%"
start "" "%ELECTRON%" .
echo Friday is starting: the notch, then the app on localhost and the services.
echo Waiting for the notch...

rem Running = the notch is still up and the app answers on localhost:5173.
powershell -NoProfile -Command "for ($i = 0; $i -lt 90; $i++) { Start-Sleep 1; if (-not @(%NOTCHES%).Count) { exit 2 }; try { Invoke-WebRequest http://127.0.0.1:5173 -UseBasicParsing -TimeoutSec 2 | Out-Null; exit 0 } catch {} }; exit 1"
if %errorlevel%==0 (
  echo Friday is running. The notch is above the taskbar.
  timeout /t 4 >nul
  exit /b 0
)
if %errorlevel%==2 (
  echo The notch closed while starting. Its last words, from data\desktop-notch.log:
) else (
  echo The notch is up, but the app did not answer on localhost:5173 in 90 seconds. From data\desktop-app.log:
  powershell -NoProfile -Command "Get-Content '%FRIDAY%\data\desktop-app.log' -Tail 15"
  echo.
  echo From data\desktop-notch.log:
)
powershell -NoProfile -Command "Get-Content '%FRIDAY%\data\desktop-notch.log' -Tail 8 -ErrorAction SilentlyContinue"
pause
