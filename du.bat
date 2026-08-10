@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem ==========================================================================
rem  DU Companion launcher.
rem
rem    du            start, open the browser, keep this window as the log
rem    du bg         start hidden in the background
rem    du stop       stop whatever is serving on the port
rem    du status     report whether it is running
rem    du setup      create the venv, install deps, fetch data, build dataset
rem    du update     re-pin to the latest game data and rebuild
rem    du test       run the test suite
rem    du site       assemble the publishable site\ directory
rem    du publish    test, rebuild site\, and push it to GitHub Pages
rem
rem  Closing this window (or Ctrl+C) stops a foreground server. A background
rem  one keeps running until "du stop".
rem ==========================================================================

set "PORT=8765"
rem Where "du publish" sends the built site. It goes to the gh-pages branch of
rem the repository origin points at, and GitHub serves it from there.
rem Public hostname: https://barleyiced.github.io/echoes-beyond/
set "URL=http://127.0.0.1:%PORT%"
set "PY=.venv\Scripts\python.exe"
set "LOG=%TEMP%\du-companion.log"

set "CMD=%~1"
if "%CMD%"=="" set "CMD=start"

if /i "%CMD%"=="stop"   goto cmd_stop
if /i "%CMD%"=="status" goto cmd_status
if /i "%CMD%"=="setup"  goto cmd_setup
if /i "%CMD%"=="update" goto cmd_update
if /i "%CMD%"=="test"   goto cmd_test
if /i "%CMD%"=="site"   goto cmd_site
if /i "%CMD%"=="publish" goto cmd_publish
if /i "%CMD%"=="bg"     goto cmd_start
if /i "%CMD%"=="start"  goto cmd_start

echo Unknown command "%CMD%".
echo Usage: du [start^|bg^|stop^|status^|setup^|update^|test^|site^|publish]
exit /b 1

rem --------------------------------------------------------------------------
:cmd_start
if not exist "%PY%" goto need_setup
if not exist "data\dataset.json" goto need_setup

rem Already up? Just open the browser again.
set "SRVPID="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)"`) do set "SRVPID=%%p"
if defined SRVPID (
    echo DU Companion is already running on %URL%  ^(PID %SRVPID%^)
    start "" "%URL%"
    exit /b 0
)

if /i "%CMD%"=="bg" goto start_bg

echo Starting DU Companion on %URL%
echo Close this window or press Ctrl+C to stop it.
echo.
rem Open the browser a moment later, once uvicorn has bound the port. `ping` is
rem used as the delay because `timeout` fails when stdin is redirected.
start "" cmd /c "ping -n 4 127.0.0.1 >nul 2>&1 & start "" "%URL%""
"%PY%" -m web.app
exit /b 0

:start_bg
echo Starting DU Companion in the background...
start "DU Companion" /min "%PY%" -m web.app
powershell -NoProfile -Command "$u='%URL%/api/ocr/status'; for ($i=0; $i -lt 25; $i++) { try { $null = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 2; exit 0 } catch { Start-Sleep -Milliseconds 800 } }; exit 1"
if errorlevel 1 (
    echo.
    echo The server did not come up in time. Try "du start" to see the error.
    exit /b 1
)
start "" "%URL%"
echo Running at %URL%
echo Stop it with:  du stop
exit /b 0

rem --------------------------------------------------------------------------
:cmd_stop
set "SRVPID="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)"`) do set "SRVPID=%%p"
if not defined SRVPID (
    echo DU Companion is not running.
    exit /b 0
)
taskkill /PID %SRVPID% /T /F >nul 2>&1
if errorlevel 1 (
    echo Could not stop PID %SRVPID%.
    exit /b 1
)
echo Stopped DU Companion  ^(PID %SRVPID%^)
exit /b 0

rem --------------------------------------------------------------------------
:cmd_status
set "SRVPID="
for /f "usebackq delims=" %%p in (`powershell -NoProfile -Command "(Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)"`) do set "SRVPID=%%p"
if defined SRVPID (
    echo Running on %URL%  ^(PID %SRVPID%^)
) else (
    echo Not running.
)
exit /b 0

rem --------------------------------------------------------------------------
:cmd_setup
echo Creating the virtual environment...
where py >nul 2>&1
if errorlevel 1 (
    python -m venv .venv
) else (
    py -3 -m venv .venv
)
if not exist "%PY%" goto setup_failed

echo Installing dependencies...
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 goto setup_failed

echo Fetching game data ^(about 60 MB, one time^)...
"%PY%" -m data.fetch --update
if errorlevel 1 goto setup_failed

echo Building the dataset...
"%PY%" -m data.build
if errorlevel 1 goto setup_failed

echo Fetching Path and Element icons...
"%PY%" -m data.icons

echo.
echo Setup complete. Double-click "Start DU Companion.bat", or run: du
exit /b 0

:setup_failed
echo.
echo Setup failed. Check that Python 3.11 or newer is installed and on PATH.
exit /b 1

rem --------------------------------------------------------------------------
:cmd_update
if not exist "%PY%" goto need_setup
echo Re-pinning to the latest game data...
"%PY%" -m data.fetch --update
if errorlevel 1 exit /b 1
"%PY%" -m data.build
if errorlevel 1 (
    echo.
    echo The dataset failed verification, so nothing was overwritten.
    echo The previous dataset is still in place and the app still works.
    exit /b 1
)
"%PY%" -m data.icons
echo.
echo Updated. Confirm nothing broke:  du test
exit /b 0

rem --------------------------------------------------------------------------
:cmd_test
if not exist "%PY%" goto need_setup
"%PY%" -m pytest tests -q
exit /b %errorlevel%

rem --------------------------------------------------------------------------
:cmd_site
if not exist "%PY%" goto need_setup
"%PY%" -m data.site --site
exit /b %errorlevel%

rem --------------------------------------------------------------------------
rem  One command, deliberately. Deploying without rebuilding site\ succeeds --
rem  no error, no failing check, no CI here to catch it -- and the live site
rem  quietly serves the previous build. Keeping test, build and deploy in one
rem  ordered action makes that impossible rather than something to remember.
:cmd_publish
if not exist "%PY%" goto need_setup

echo [1/3] Tests...
"%PY%" -m pytest tests -q
if errorlevel 1 (
    echo.
    echo Tests failed, so nothing was built or published.
    exit /b 1
)

echo.
echo [2/3] Building site\ ...
"%PY%" -m data.site --site
if errorlevel 1 (
    echo.
    echo The site was not built, so nothing was published.
    exit /b 1
)

echo.
echo [3/3] Publishing to GitHub Pages...
where git >nul 2>&1
if errorlevel 1 (
    echo.
    echo git is not on PATH, so nothing was published.
    echo site\ is built and ready either way.
    exit /b 1
)
rem  Pushes site\ to the gh-pages branch as a fresh orphan commit. It refuses
rem  to push a build whose index.html has lost its robots noindex tag: GitHub
rem  Pages serves fixed headers, so that meta tag is the only thing keeping the
rem  site out of search results, and there is no CI here to notice it went.
"%PY%" -m data.publish
if errorlevel 1 (
    echo.
    echo The publish failed. CHANGELOG.md was left alone, so nothing claims to
    echo have shipped that did not.
    exit /b 1
)

rem Only now: the changelog's "Unreleased" section becomes a dated release. Done
rem after the deploy rather than during the build because "du site" builds
rem without publishing, and a record written there would claim a deploy that
rem never happened. The live site is not waiting on this — it renders its own
rem unreleased section under the build id baked into its index.html.
echo.
echo Recording the release in CHANGELOG.md...
"%PY%" -m data.release
if errorlevel 1 (
    echo.
    echo The deploy SUCCEEDED but the changelog was not stamped. Fix it by hand
    echo with:  %PY% -m data.release
    echo Leaving it means the next publish merges two releases under one heading.
)
exit /b 0

rem --------------------------------------------------------------------------
:need_setup
echo DU Companion is not set up yet in this folder.
echo Run:  du setup
exit /b 1
