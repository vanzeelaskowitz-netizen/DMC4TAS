@echo off
chcp 65001 >nul
rem First-time setup: creates the Python environment in .venv and installs the packages the tool needs.
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    echo 没有找到 Python。请先到 https://www.python.org/downloads/ 安装 Python 3.13（64 位），
    echo 安装时勾选 "py launcher"，装好后再双击本文件。
    echo.
    pause
    exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
    echo 正在创建 Python 环境…
    py -3 -m venv .venv
    if errorlevel 1 goto failed
)
echo 正在安装需要的组件（需要联网，可能要几分钟）…
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed
echo.
echo 安装完成。以后双击 start_game.bat 启动游戏，再双击 tas_player.bat 打开播放器。
echo.
pause
exit /b 0
:failed
echo.
echo 安装失败，请把上面的报错信息发给开发者。
echo.
pause
exit /b 1
