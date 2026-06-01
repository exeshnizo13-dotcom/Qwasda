"""
Qwasda — автоматичне визначення та перемикання розкладки клавіатури.
Виявляє, коли користувач друкує в неправильній розкладці (укр/англ),
перемикає розкладку та виправляє набраний текст.

Запуск: pythonw qwasda.py  (без консолі)
        або start.bat
Вихід: правий клік на іконці в треї → "Вихід"
"""

import sys
import os
import time
import ctypes
import ctypes.wintypes
import atexit
import signal
import threading

# ─── Приховуємо консоль ───────────────────────────────────────────────────────
# Якщо запущено через pythonw.exe — консолі немає.
# Якщо запущено через python.exe — приховуємо вікно консолі.
if sys.stdout is None or not sys.stdout.isatty():
    # Вже запущено без консолі (pythonw)
    pass
else:
    # Намагаємось приховати консоль
    try:
        ctypes.windll.user32.ShowWindow(
            ctypes.windll.kernel32.GetConsoleWindow(), 0  # SW_HIDE = 0
        )
    except Exception:
        pass

try:
    import keyboard
except ImportError:
    # Якщо консоль прихована — показуємо повідомлення
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            "Бібліотека 'keyboard' не встановлена.\nЗапустіть: pip install keyboard",
            "Qwasda — Помилка",
            0x10  # MB_ICONERROR
        )
    except Exception:
        pass
    sys.exit(1)

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    try:
        ctypes.windll.user32.MessageBoxW(
            0,
            "Бібліотеки 'pystray' та 'pillow' не встановлені.\nЗапустіть: pip install pystray pillow",
            "Qwasda — Помилка",
            0x10
        )
    except Exception:
        pass
    sys.exit(1)


# ─── Константи ─────────────────────────────────────────────────────────────────

LANG_ENGLISH = 0x0409
LANG_UKRAINIAN = 0x0422

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5

MODIFIER_KEYS = {
    VK_SHIFT, VK_CONTROL, VK_MENU, VK_CAPITAL,
    VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL,
    VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN
}

NAVIGATION_KEYS = {
    VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN,
    VK_TAB, VK_ESCAPE, VK_BACK, VK_RETURN
}

# Мапування символів між розкладками
UKR_TO_ENG = {
    'й': 'q', 'ц': 'w', 'у': 'e', 'к': 'r', 'е': 't', 'н': 'y', 'г': 'u',
    'ш': 'i', 'щ': 'o', 'з': 'p', 'х': '[', 'ї': ']', 'ф': 'a', 'і': 's',
    'в': 'd', 'а': 'f', 'п': 'g', 'р': 'h', 'о': 'j', 'л': 'k', 'д': 'l',
    'ж': ';', 'є': "'", 'я': 'z', 'ч': 'x', 'с': 'c', 'м': 'v', 'и': 'b',
    'т': 'n', 'ь': 'm', 'б': ',', 'ю': '.',
    'Й': 'Q', 'Ц': 'W', 'У': 'E', 'К': 'R', 'Е': 'T', 'Н': 'Y', 'Г': 'U',
    'Ш': 'I', 'Щ': 'O', 'З': 'P', 'Х': '{', 'Ї': '}', 'Ф': 'A', 'І': 'S',
    'В': 'D', 'А': 'F', 'П': 'G', 'Р': 'H', 'О': 'J', 'Л': 'K', 'Д': 'L',
    'Ж': ':', 'Є': '"', 'Я': 'Z', 'Ч': 'X', 'С': 'C', 'М': 'V', 'И': 'B',
    'Т': 'N', 'Ь': 'M', 'Б': '<', 'Ю': '>',
    'ґ': '\\', 'Ґ': '|',
}

ENG_TO_UKR = {v: k for k, v in UKR_TO_ENG.items()}
NEUTRAL = set('0123456789`-=[]\\;\',./~!@#$%^&*()_+{}|:"<>? \t\n\r')

# ─── Win32 API ─────────────────────────────────────────────────────────────────

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def get_foreground_layout():
    """Повертає LANGID поточної розкладки активного вікна."""
    hwnd = user32.GetForegroundWindow()
    thread_id = user32.GetWindowThreadProcessId(hwnd, None)
    hkl = user32.GetKeyboardLayout(thread_id)
    return hkl & 0xFFFF


def switch_layout(lang_id):
    """Перемикає розкладку клавіатури."""
    user32.ActivateKeyboardLayout(ctypes.c_void_p(lang_id), 0)


# ─── SendInput ─────────────────────────────────────────────────────────────────

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", INPUT_UNION)]


def _send_inputs(*inputs):
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))


def send_key(vk):
    down = INPUT()
    down.type = 1
    down.union.ki.wVk = vk
    down.union.ki.wScan = 0
    down.union.ki.dwFlags = 0
    up = INPUT()
    up.type = 1
    up.union.ki.wVk = vk
    up.union.ki.wScan = 0
    up.union.ki.dwFlags = 2
    _send_inputs(down, up)


def send_unicode_char(char):
    down = INPUT()
    down.type = 1
    down.union.ki.wVk = 0
    down.union.ki.wScan = ord(char)
    down.union.ki.dwFlags = 0x0004
    up = INPUT()
    up.type = 1
    up.union.ki.wVk = 0
    up.union.ki.wScan = ord(char)
    up.union.ki.dwFlags = 0x0006
    _send_inputs(down, up)


# ─── Налаштування ──────────────────────────────────────────────────────────────

# Клавіша для ручного перемикання (подвійне натискання)
MANUAL_TRIGGER_KEY = '`'  # backtick (код 223 на клавіатурі)
# Альтернативна клавіша: "'" (апостроф)

# Чутливість подвійного натискання (секунди)
DOUBLE_PRESS_INTERVAL = 0.4


# ─── Стан ──────────────────────────────────────────────────────────────────────

running = True
last_correction_time = 0
enabled = True
last_trigger_press_time = 0  # Час останнього натискання тригера
typed_buffer = ""  # Буфер набраних символів


# ─── Функція конвертації тексту ────────────────────────────────────────────────

def convert_text(text):
    """Конвертує текст з однієї розкладки в іншу.
    Повертає (converted_text, target_layout).
    Якщо конвертація не потрібна — повертає (None, None).
    """
    if not text:
        return None, None

    # Визначаємо, яка розкладка була "задумана" за набраним текстом
    # Якщо текст містить українські літери в англійській розкладці — конвертуємо в укр
    # Якщо текст містить англійські літери в українській розкладці — конвертуємо в англ

    has_ukr_as_eng = False
    has_eng_as_ukr = False

    for ch in text:
        if ch in UKR_TO_ENG:
            has_ukr_as_eng = True
        if ch in ENG_TO_UKR:
            has_eng_as_ukr = True

    converted = None
    target_layout = None

    if has_ukr_as_eng and not has_eng_as_ukr:
        # Набрано "українські" літери в англійській розкладці
        converted = "".join(UKR_TO_ENG.get(ch, ch) for ch in text)
        target_layout = LANG_UKRAINIAN
    elif has_eng_as_ukr and not has_ukr_as_eng:
        # Набрано "англійські" літери в українській розкладці
        converted = "".join(ENG_TO_UKR.get(ch, ch) for ch in text)
        target_layout = LANG_ENGLISH
    elif has_ukr_as_eng and has_eng_as_ukr:
        # Змішаний текст — визначаємо за більшістю
        ukr_count = sum(1 for ch in text if ch in UKR_TO_ENG)
        eng_count = sum(1 for ch in text if ch in ENG_TO_UKR)
        if ukr_count >= eng_count:
            converted = "".join(UKR_TO_ENG.get(ch, ch) for ch in text)
            target_layout = LANG_UKRAINIAN
        else:
            converted = "".join(ENG_TO_UKR.get(ch, ch) for ch in text)
            target_layout = LANG_ENGLISH

    return converted, target_layout


# ─── Ручне перемикання з виправленням ──────────────────────────────────────────

def manual_convert():
    """Викликається при подвійному натисканні тригера.
    Копіює виділений текст (або весь текст у полі введення),
    конвертує його і вставляє назад.
    """
    global typed_buffer

    # Використовуємо буфер набраних символів
    text_to_convert = typed_buffer
    if not text_to_convert:
        return

    converted, target_layout = convert_text(text_to_convert)
    if not converted or converted == text_to_convert:
        return

    # 1. Видаляємо набраний текст (по одному символу)
    for _ in range(len(text_to_convert)):
        send_key(VK_BACK)
        time.sleep(0.005)

    time.sleep(0.02)

    # 2. Перемикаємо розкладку
    switch_layout(target_layout)
    time.sleep(0.02)

    # 3. Вводимо конвертований текст
    for ch in converted:
        send_unicode_char(ch)
        time.sleep(0.005)

    # Очищаємо буфер
    typed_buffer = ""


# ─── Обробник клавіатури ──────────────────────────────────────────────────────

def on_key_event(event):
    global last_correction_time, last_trigger_press_time, typed_buffer

    if not enabled:
        return
    if event.event_type != keyboard.KEY_DOWN:
        return

    vk = event.scan_code
    char = event.name

    # ── Перевірка на подвійне натискання тригера ──
    if char == MANUAL_TRIGGER_KEY:
        now = time.time()
        if now - last_trigger_press_time < DOUBLE_PRESS_INTERVAL:
            # Подвійне натискання! Конвертуємо текст
            last_trigger_press_time = 0  # Скидаємо, щоб не спрацьовувало 3+ разів
            manual_convert()
            return  # Блокуємо саму клавішу
        else:
            last_trigger_press_time = now
            # Не блокуємо — даємо клавіші пройти далі
            return

    if vk in MODIFIER_KEYS or vk in NAVIGATION_KEYS:
        # Очищаємо буфер при навігації
        if vk == VK_BACK and typed_buffer:
            typed_buffer = typed_buffer[:-1]
        elif vk in (VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN, VK_TAB, VK_RETURN):
            typed_buffer = ""
        return

    # Оновлюємо буфер набраних символів
    if char and len(char) == 1 and char not in NEUTRAL:
        typed_buffer += char
        if len(typed_buffer) > 200:
            typed_buffer = typed_buffer[-100:]
    elif char == 'space':
        typed_buffer += ' '
    elif vk == VK_BACK and typed_buffer:
        typed_buffer = typed_buffer[:-1]

    # ── Автоматичне виправлення ──
    if not char or len(char) != 1 or char in NEUTRAL:
        return

    layout = get_foreground_layout()
    needs_fix = False
    corrected = None
    target_layout = None

    if layout == LANG_ENGLISH and char in UKR_TO_ENG:
        needs_fix = True
        corrected = UKR_TO_ENG[char]
        target_layout = LANG_UKRAINIAN
    elif layout == LANG_UKRAINIAN and char in ENG_TO_UKR:
        needs_fix = True
        corrected = ENG_TO_UKR[char]
        target_layout = LANG_ENGLISH

    if needs_fix:
        now = time.time()
        if now - last_correction_time < 0.3:
            return
        send_key(VK_BACK)
        time.sleep(0.01)
        switch_layout(target_layout)
        time.sleep(0.01)
        send_unicode_char(corrected)
        # Оновлюємо буфер: замість символу додаємо виправлений
        if typed_buffer:
            typed_buffer = typed_buffer[:-1] + corrected
        last_correction_time = now


# ─── Автозапуск ────────────────────────────────────────────────────────────────

def _get_startup_folder():
    """Повертає шлях до папки автозапуску."""
    return os.path.join(
        os.environ.get("APPDATA", ""),
        r"Microsoft\Windows\Start Menu\Programs\Startup"
    )


def _get_bat_path():
    return os.path.join(_get_startup_folder(), "Qwasda.bat")


def add_to_startup():
    """Додає Qwasda до автозапуску Windows."""
    startup_dir = _get_startup_folder()
    os.makedirs(startup_dir, exist_ok=True)
    bat_path = _get_bat_path()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    python_exe = sys.executable
    script_path = os.path.join(script_dir, "qwasda.py")

    bat_content = f'@echo off\nstart "" "{python_exe}" "{script_path}"\n'
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)
    return bat_path


def remove_from_startup():
    """Видаляє Qwasda з автозапуску."""
    bat_path = _get_bat_path()
    if os.path.exists(bat_path):
        os.remove(bat_path)
        return True
    return False


def is_in_startup():
    """Перевіряє, чи Qwasda є в автозапуску."""
    return os.path.exists(_get_bat_path())


# ─── Системний трей ────────────────────────────────────────────────────────────

def _create_icon_image():
    """Створює іконку для системного трею."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([4, 4, 60, 60], radius=12, fill=(41, 128, 185))

    try:
        font = ImageFont.truetype("arial.ttf", 26)
    except Exception:
        font = ImageFont.load_default()

    draw.text((10, 14), "Qw", fill="white", font=font)
    return img


def _toggle_enabled(icon, item):
    global enabled
    enabled = not enabled
    icon.update_menu()


def _toggle_startup(icon, item):
    if is_in_startup():
        remove_from_startup()
    else:
        add_to_startup()
    icon.update_menu()


def _on_exit(icon, item):
    global running
    running = False
    keyboard.unhook_all()
    icon.stop()


def _toggle_trigger_key(icon, item):
    """Перемикає клавішу ручного перемикання між ` та '"""
    global MANUAL_TRIGGER_KEY
    if MANUAL_TRIGGER_KEY == '`':
        MANUAL_TRIGGER_KEY = "'"
    else:
        MANUAL_TRIGGER_KEY = '`'
    icon.update_menu()


def _make_menu():
    return pystray.Menu(
        pystray.MenuItem(
            lambda item: "✅ Перемикання увімкнено" if enabled else "⏸ Перемикання вимкнено",
            _toggle_enabled,
            checked=lambda item: enabled,
        ),
        pystray.MenuItem(
            lambda item: f"⌨️ Ручне: подвійне {MANUAL_TRIGGER_KEY}",
            _toggle_trigger_key,
        ),
        pystray.MenuItem(
            lambda item: "✅ Автозапуск увімкнено" if is_in_startup() else "❌ Автозапуск вимкнено",
            _toggle_startup,
            checked=lambda item: is_in_startup(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Вихід", _on_exit),
    )


def run_tray():
    """Запускає іконку в системному трею."""
    icon = pystray.Icon(
        "Qwasda",
        _create_icon_image(),
        "Qwasda — перемикання розкладки",
        _make_menu(),
    )
    # Показуємо сповіщення при старті
    icon.notify("Qwasda запущено! Керуйте через іконку в треї.", "Qwasda")
    icon.run()


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    global running

    keyboard.hook(on_key_event)

    tray_thread = threading.Thread(target=run_tray, daemon=True)
    tray_thread.start()

    atexit.register(lambda: keyboard.unhook_all())

    def handler(sig, frame):
        global running
        running = False
        keyboard.unhook_all()
        sys.exit(0)

    signal.signal(signal.SIGINT, handler)

    try:
        while running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        keyboard.unhook_all()


if __name__ == "__main__":
    main()
