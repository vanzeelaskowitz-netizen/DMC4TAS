@echo off
rem Restarts the game and replays the recorded route back into the battle room (about 80 seconds).
cd /d "%~dp0"
".venv\Scripts\python.exe" -B -u "tas\boot_to_room.py"
echo.
pause
