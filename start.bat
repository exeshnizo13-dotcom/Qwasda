@echo off
chcp 65001 >nul 2>&1

:: Знаходимо директорію скрипта
set "SCRIPT_DIR=%~dp0"

:: Перевіряємо наявність Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [Помилка] Python не знайдено! Встановіть Python 3.8+
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Встановлюємо залежності якщо потрібно
pip install -q keyboard pystray pillow

:: Запускаємо Qwasda БЕЗ консолі через pythonw.exe
start "" pythonw "%SCRIPT_DIR%qwasda.py"
