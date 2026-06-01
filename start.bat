@echo off
chcp 65001 >nul 2>&1
title Qwasda

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
echo Перевірка залежностей...
pip install -q keyboard pystray pillow

:: Запускаємо Qwasda
echo Запуск Qwasda...
echo.
python "%SCRIPT_DIR%qwasda.py"

:: Якщо програма завершилась з помилкою
if errorlevel 1 (
    echo.
    echo [Помилка] Qwasda завершилась з помилкою.
    pause
)
