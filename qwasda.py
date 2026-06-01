"""
Qwasda — автоматичне визначення та перемикання розкладки клавіатури.
Виявляє, коли користувач друкує в неправильній розкладці (укр/англ),
перемикає розкладку та виправляє набраний текст.

Запуск: python qwasda.py
Вихід: Ctrl+C або закриття вікна

ВАЖЛИВО: Потрібно запускати від імені адміністратора!
"""

import sys
import time
import ctypes
import ctypes.wintypes
import atexit
import signal

try:
    import keyboard
except ImportError:
    print("Помилка: бібліотека 'keyboard' не встановлена.")
    print("Запустіть: pip install keyboard")
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

# ─── Мапування символів між розкладками ────────────────────────────────────────

# Коли розкладка англійська, але користувач набирає "українські" літери
# (тобто натискає клавіші, де у укр. розкладці стоять укр. літери)
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

# Зворотне мапування
ENG_TO_UKR = {v: k for k, v in UKR_TO_ENG.items()}

# Нейтральні символи (не залежать від розкладки)
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


# ─── SendInput для надсилання клавіш ───────────────────────────────────────────

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
    """Надсилає натискання клавіші з заданим VK-кодом."""
    down = INPUT()
    down.type = 1
    down.union.ki.wVk = vk
    down.union.ki.wScan = 0
    down.union.ki.dwFlags = 0
    up = INPUT()
    up.type = 1
    up.union.ki.wVk = vk
    up.union.ki.wScan = 0
    up.union.ki.dwFlags = 2  # KEYEVENTF_KEYUP
    _send_inputs(down, up)


def send_unicode_char(char):
    """Надсилає Unicode-символ."""
    down = INPUT()
    down.type = 1
    down.union.ki.wVk = 0
    down.union.ki.wScan = ord(char)
    down.union.ki.dwFlags = 0x0004  # KEYEVENTF_UNICODE
    up = INPUT()
    up.type = 1
    up.union.ki.wVk = 0
    up.union.ki.wScan = ord(char)
    up.union.ki.dwFlags = 0x0006  # KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
    _send_inputs(down, up)


# ─── Стан ──────────────────────────────────────────────────────────────────────

running = True
last_correction_time = 0


# ─── Обробник клавіатури ──────────────────────────────────────────────────────

def on_key_event(event):
    """Обробник події натискання клавіші."""
    global last_correction_time

    # Працюємо тільки з key_down подіями
    if event.event_type != keyboard.KEY_DOWN:
        return

    vk = event.scan_code

    # Ігноруємо модифікатори та навігацію
    if vk in MODIFIER_KEYS or vk in NAVIGATION_KEYS:
        return

    # Отримуємо символ
    char = event.name

    # Фільтруємо: тільки однолітерні символи, не нейтральні
    if not char or len(char) != 1 or char in NEUTRAL:
        return

    layout = get_foreground_layout()

    needs_fix = False
    corrected = None
    target_layout = None

    if layout == LANG_ENGLISH and char in UKR_TO_ENG:
        # Англійська розкладка, але набрали символ, який є тільки в укр. розкладці
        needs_fix = True
        corrected = UKR_TO_ENG[char]
        target_layout = LANG_UKRAINIAN

    elif layout == LANG_UKRAINIAN and char in ENG_TO_UKR:
        # Українська розкладка, але набрали символ, який є тільки в англ. розкладці
        needs_fix = True
        corrected = ENG_TO_UKR[char]
        target_layout = LANG_ENGLISH

    if needs_fix:
        now = time.time()
        if now - last_correction_time < 0.3:
            return

        # 1. Backspace — видаляємо помилковий символ
        send_key(VK_BACK)
        time.sleep(0.01)

        # 2. Перемикаємо розкладку
        switch_layout(target_layout)
        time.sleep(0.01)

        # 3. Надсилаємо виправлений символ
        send_unicode_char(corrected)

        last_correction_time = now


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    global running

    print("=" * 55)
    print("  Layout Switcher 🇺🇦🇬🇧")
    print("  Автоматичне виправлення розкладки клавіатури")
    print("  Вихід: Ctrl+C")
    print("=" * 55)

    # Реєструємо глобальний хук
    keyboard.hook(on_key_event)

    print("\n✅ Хук встановлено. Працюємо...\n")
    print("Підказка: спробуйте набрати 'ghbdtn' в англ. розкладці")
    print("          — має стати 'привіт'\n")

    # Очищення
    def cleanup():
        keyboard.unhook_all()
        print("\nLayout Switcher зупинено.")

    atexit.register(cleanup)

    def handler(sig, frame):
        global running
        running = False
        cleanup()
        sys.exit(0)

    signal.signal(signal.SIGINT, handler)

    # Тримаємо програму живою
    try:
        while running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
