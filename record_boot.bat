@echo off
rem Starts the boot-to-room recording. Double-click this file.
cd /d "%~dp0"
".venv\Scripts\python.exe" -B "tas\record_boot.py"
echo.
pause
