@echo off
rem Opens the frame player window. Double-click this file.
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -B "tas\player.py" %*
