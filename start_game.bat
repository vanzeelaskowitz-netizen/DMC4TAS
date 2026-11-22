@echo off
rem Starts the game with write watch on its memory pools (needed for fast step-back) and attaches
rem the TAS service without taking control. If the game is already running it is not restarted;
rem the tool just attaches to it. Then play to any scene and open tas_player.bat when needed.
cd /d "%~dp0"
".venv\Scripts\python.exe" -B -u "tas\start_game.py" %*
echo.
pause
