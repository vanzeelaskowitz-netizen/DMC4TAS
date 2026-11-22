@echo off
rem Opens the TAS console (pause / load / step / resume). Double-click this file.
cd /d "%~dp0"
chcp 65001 >nul
if not exist ".venv\Scripts\python.exe" (
    echo Run setup.bat first / 请先双击 setup.bat 完成安装
    echo.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -B "tas\tasctl.py"
echo.
pause
