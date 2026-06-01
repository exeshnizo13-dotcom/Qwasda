@echo off
chcp 65001 >nul 2>&1
title Qwasda Setup

echo ============================================
echo   Qwasda Setup
echo ============================================
echo.

:: Перевіряємо Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [Помилка] Python не знайдено!
    echo Встановіть Python 3.8+: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/3] Встановлення залежностей...
pip install keyboard pystray pillow
if errorlevel 1 (
    echo [Помилка] Не вдалося встановити залежності.
    pause
    exit /b 1
)

echo [2/3] Додавання до автозапуску...
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SCRIPT_DIR=%~dp0"

(
echo @echo off
echo start "" pythonw "%SCRIPT_DIR%qwasda.py"
) > "%STARTUP_DIR%\Qwasda.bat"

echo [3/3] Готово!
echo.
echo Qwasda додано до автозапуску Windows.
echo Перезавантажте комп'ютер або запустіть start.bat вручну.
echo.
pause
