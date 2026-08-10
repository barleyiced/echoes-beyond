@echo off
rem Double-click to stop a server running in the background.
cd /d "%~dp0"
call "%~dp0du.bat" stop
rem Brief pause so the message is readable. `ping` rather than `timeout`, which
rem fails outright when stdin is redirected.
ping -n 3 127.0.0.1 >nul 2>&1
