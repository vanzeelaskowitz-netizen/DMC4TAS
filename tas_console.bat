@echo off
rem Opens the TAS console (pause / load / step / resume). Double-click this file.
cd /d "%~dp0"
".venv\Scripts\python.exe" -B "tas\tasctl.py"
echo.
pause
