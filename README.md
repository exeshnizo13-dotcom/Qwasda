# Qwasda

Автоматичне визначення та перемикання української й англійської розкладок клавіатури для Windows.

## Встановлення Windows

Запустіть `Qwasda-Setup-1.4.0-x64.exe`. Інсталятор працює для поточного користувача, не потребує Python і створює ярлик у Start Menu. Автозапуск Windows вимкнений за замовчуванням.

Для portable-режиму запустіть `Qwasda-1.4.0-x64.exe`; він не змінює інсталяцію та також не потребує Python.

Під час оновлення зберігаються `config.json`, `learned.json`, власні словники й приватна статистика. Звичайне видалення не очищує AppData; повне очищення доступне окремим прапорцем uninstall.

## Можливості

- ручна конвертація подвійним Ctrl або налаштованим hotkey;
- автокорекція за вбудованими й власними EN/UK словниками;
- навчання, undo, tray-керування та налаштування;
- opt-in агрегована статистика без збереження введеного тексту;
- single-instance, штатний cleanup і службові команди для оновлення.

## Запуск із source для розробників

```powershell
pip install -e ".[dev]"
python -m qwasda
```

CLI-команди:

```powershell
python -m qwasda --version
python -m qwasda --smoke-test
python -m qwasda --shutdown
```

## Автозапуск

Автозапуск вмикається через tray або прапорець у NSIS-інсталяторі. Запис зберігається в HKCU Run і не потребує адміністративних прав. Старий Qwasda startup batch мігрується автоматично.

## Тести та збірка

```powershell
pytest
ruff check .
black --check .
mypy qwasda
python release.py
```

`release.py` створює `artifacts/Qwasda-1.4.0-x64.exe`, `artifacts/Qwasda-Setup-1.4.0-x64.exe` і `artifacts/SHA256SUMS.txt`.

## Дані користувача

Налаштування та learned data: `%APPDATA%\Qwasda`. Логи, crash reports і локальні службові файли: `%LOCALAPPDATA%\Qwasda`.

## Ліцензія

MIT
