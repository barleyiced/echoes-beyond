@echo off
rem Double-click to start. This window is the server log — closing it stops the app.
rem The full path is explicit because some systems do not search the current
rem directory for batch files (NoDefaultCurrentDirectoryInExePath).
cd /d "%~dp0"
call "%~dp0du.bat" start
if errorlevel 1 pause
