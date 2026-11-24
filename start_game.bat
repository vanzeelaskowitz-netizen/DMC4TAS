@echo off
rem Starts the game with write watch on its memory pools (needed for fast step-back) and attaches
rem the TAS service without taking control. If the game is already running it is not restarted;
rem the tool just attaches to it. Then play to any scene and open tas_player.bat when needed.
rem First asks for the interface language (English / Chinese) and keeps it in language.txt, which
rem the player and the service read too. After 5 seconds the last choice is used.
cd /d "%~dp0"
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
    echo Run setup.bat first / 请先双击 setup.bat 完成安装
    echo.
    pause
    exit /b 1
)
set "TAS_LANG="
if exist language.txt set /p TAS_LANG=<language.txt
set "WAIT="
if /i "%TAS_LANG%"=="en" set "WAIT=/t 5 /d 1"
if /i "%TAS_LANG%"=="zh" set "WAIT=/t 5 /d 2"
echo Language / 语言
echo    1  English
echo    2  中文
if /i "%TAS_LANG%"=="en" echo    Press 1 or 2. English is kept after 5 seconds.
if /i "%TAS_LANG%"=="zh" echo    按 1 或 2，5 秒后沿用中文。
choice /c 12 /n %WAIT% /m "> "
if errorlevel 2 (set "TAS_LANG=zh") else (set "TAS_LANG=en")
>language.txt echo %TAS_LANG%
echo.
".venv\Scripts\python.exe" -B -u "tas\start_game.py" %*
echo.
pause
