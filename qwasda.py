"""
Qwasda — автоматичне визначення та перемикання розкладки клавіатури.
Виявляє, коли користувач друкує в неправильній розкладці (укр/англ),
перемикає розкладку та виправляє набраний текст.

Запуск: Qwasda.exe  або  pythonw qwasda.py
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

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════════
# Win32 API
# ═══════════════════════════════════════════════════════════════════════════════

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

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
VK_OEM_3 = 0xC0  # ` backtick
VK_OEM_7 = 0xDE  # ' apostrophe

LANG_ENGLISH = 0x0409
LANG_UKRAINIAN = 0x0422

MODIFIER_VKS = {
    VK_SHIFT, VK_CONTROL, VK_MENU, VK_CAPITAL,
    VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL,
    VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN
}

NAVIGATION_VKS = {
    VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN,
    VK_TAB, VK_ESCAPE, VK_BACK, VK_RETURN
}

# ═══════════════════════════════════════════════════════════════════════════════
# МАПУВАННЯ SCAN CODE → СИМВОЛ
# ═══════════════════════════════════════════════════════════════════════════════
# Scan code — це фізична позиція клавіші, не залежить від розкладки.
# Один і той же scan code дає різні символи в різних розкладках.

# Англійська розкладка (QWERTY)
SCAN_ENG = {
    0x10: 'q', 0x11: 'w', 0x12: 'e', 0x13: 'r', 0x14: 't', 0x15: 'y',
    0x16: 'u', 0x17: 'i', 0x18: 'o', 0x19: 'p', 0x1a: '[', 0x1b: ']',
    0x1e: 'a', 0x1f: 's', 0x20: 'd', 0x21: 'f', 0x22: 'g', 0x23: 'h',
    0x24: 'j', 0x25: 'k', 0x26: 'l', 0x27: ';', 0x28: "'", 0x29: '`',
    0x2b: '\\',
    0x2c: 'z', 0x2d: 'x', 0x2e: 'c', 0x2f: 'v', 0x30: 'b', 0x31: 'n',
    0x32: 'm', 0x33: ',', 0x34: '.', 0x35: '/',
    0x02: '1', 0x03: '2', 0x04: '3', 0x05: '4', 0x06: '5',
    0x07: '6', 0x08: '7', 0x09: '8', 0x0a: '9', 0x0b: '0',
    0x0c: '-', 0x0d: '=',
    0x39: ' ',
    # Верхній ряд з Shift
    0x2a: '',  # LSHIFT (модифікатор)
}

# Українська розкладка (ЙЦУКЕН)
SCAN_UKR = {
    0x10: 'й', 0x11: 'ц', 0x12: 'у', 0x13: 'к', 0x14: 'е', 0x15: 'н',
    0x16: 'г', 0x17: 'ш', 0x18: 'щ', 0x19: 'з', 0x1a: 'х', 0x1b: 'ї',
    0x1e: 'ф', 0x1f: 'і', 0x20: 'в', 0x21: 'а', 0x22: 'п', 0x23: 'р',
    0x24: 'о', 0x25: 'л', 0x26: 'д', 0x27: 'ж', 0x28: 'є', 0x29: 'ґ',
    0x2b: '\\',
    0x2c: 'я', 0x2d: 'ч', 0x2e: 'с', 0x2f: 'м', 0x30: 'и', 0x31: 'т',
    0x32: 'ь', 0x33: 'б', 0x34: 'ю', 0x35: '.',
    0x02: '1', 0x03: '2', 0x04: '3', 0x05: '4', 0x06: '5',
    0x07: '6', 0x08: '7', 0x09: '8', 0x0a: '9', 0x0b: '0',
    0x0c: '-', 0x0d: '=',
    0x39: ' ',
}

# Мапування: англійський символ → український (ті самі клавіші)
ENG_TO_UKR = {}
for sc in SCAN_ENG:
    if sc in SCAN_UKR and SCAN_ENG[sc] and SCAN_UKR[sc]:
        ENG_TO_UKR[SCAN_ENG[sc]] = SCAN_UKR[sc]

# Мапування: український символ → англійський
UKR_TO_ENG = {v: k for k, v in ENG_TO_UKR.items()}

# Нейтральні символи (однакові в обох розкладках)
NEUTRAL = set('0123456789`-=[]\\;\',./~!@#$%^&*()_+{}|:"<>? \t\n\r')


# ═══════════════════════════════════════════════════════════════════════════════
# Win32 структури та типи
# ═══════════════════════════════════════════════════════════════════════════════

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.wintypes.DWORD),
        ("scanCode", ctypes.wintypes.DWORD),
        ("flags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
user32.CallNextHookEx.restype = ctypes.c_long
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.UnhookWindowsHookEx.restype = ctypes.c_bool
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.GetKeyboardLayout.restype = ctypes.c_void_p
user32.GetKeyboardLayout.argtypes = [ctypes.c_ulong]
user32.ActivateKeyboardLayout.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.SendInput.restype = ctypes.c_uint
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]


# ═══════════════════════════════════════════════════════════════════════════════
# Функції
# ═══════════════════════════════════════════════════════════════════════════════

def get_foreground_layout():
    hwnd = user32.GetForegroundWindow()
    thread_id = user32.GetWindowThreadProcessId(hwnd, None)
    hkl = user32.GetKeyboardLayout(thread_id)
    return hkl & 0xFFFF


def switch_layout(lang_id):
    user32.ActivateKeyboardLayout(ctypes.c_void_p(lang_id), 0)


def get_char_from_scan(scan_code, layout):
    """Повертає символ за scan code та розкладкою."""
    if layout == LANG_ENGLISH:
        return SCAN_ENG.get(scan_code)
    else:
        return SCAN_UKR.get(scan_code)


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


# ═══════════════════════════════════════════════════════════════════════════════
# Глобальний стан
# ═══════════════════════════════════════════════════════════════════════════════

running = True
enabled = True
typed_buffer = ""
last_correction_time = 0
last_trigger_time = 0
trigger_key = VK_OEM_3  # ` backtick
hook_handle = None


# ═══════════════════════════════════════════════════════════════════════════════
# Логіка конвертації
# ═══════════════════════════════════════════════════════════════════════════════

def convert_text(text):
    """Конвертує текст між розкладками."""
    if not text or len(text.strip()) == 0:
        return None, None
    ukr_as_eng = sum(1 for ch in text if ch in UKR_TO_ENG)
    eng_as_ukr = sum(1 for ch in text if ch in ENG_TO_UKR)
    if ukr_as_eng == 0 and eng_as_ukr == 0:
        return None, None
    if ukr_as_eng >= eng_as_ukr:
        return "".join(UKR_TO_ENG.get(ch, ch) for ch in text), LANG_UKRAINIAN
    else:
        return "".join(ENG_TO_UKR.get(ch, ch) for ch in text), LANG_ENGLISH


def manual_convert():
    """Ручне перемикання — подвійне натискання тригера."""
    global typed_buffer
    text = typed_buffer.strip()
    if not text:
        return
    converted, target_layout = convert_text(text)
    if not converted or converted == text:
        return
    for _ in range(len(text)):
        send_key(VK_BACK)
        time.sleep(0.005)
    time.sleep(0.02)
    switch_layout(target_layout)
    time.sleep(0.02)
    for ch in converted:
        send_unicode_char(ch)
        time.sleep(0.005)
    typed_buffer = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Low-level keyboard hook
# ═══════════════════════════════════════════════════════════════════════════════

@ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
def keyboard_hook(nCode, wParam, lParam):
    global typed_buffer, last_correction_time, last_trigger_time

    # Обробляємо тільки keydown
    if nCode < 0 or wParam not in (WM_KEYDOWN, WM_SYSKEYDOWN):
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
    vk = kb.vkCode
    sc = kb.scanCode

    # Ручне перемикання: подвійне натискання тригера
    if vk == trigger_key and enabled:
        now = time.time()
        if now - last_trigger_time < 0.5:
            last_trigger_time = 0
            manual_convert()
            return 1
        else:
            last_trigger_time = now
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    if not enabled:
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # Модифікатори — пропускаємо
    if vk in MODIFIER_VKS:
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # Навігація — очищаємо буфер
    if vk in NAVIGATION_VKS:
        if vk == VK_BACK and typed_buffer:
            typed_buffer = typed_buffer[:-1]
        elif vk != VK_BACK:
            typed_buffer = ""
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # Визначаємо символи за scan code для ОБОХ розкладок
    layout = get_foreground_layout()
    char_current = get_char_from_scan(sc, layout)  # Що набрано в поточній розкладці
    char_other = get_char_from_scan(sc, LANG_UKRAINIAN if layout == LANG_ENGLISH else LANG_ENGLISH)  # Що було б в іншій

    if char_current is None:
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # Оновлюємо буфер (символ з поточної розкладки)
    if char_current not in NEUTRAL:
        typed_buffer += char_current
        if len(typed_buffer) > 200:
            typed_buffer = typed_buffer[-100:]
    elif char_current == ' ':
        typed_buffer += ' '

    # Автоматичне виправлення:
    # Якщо в іншій розкладці на цій клавіші — українська літера (не нейтральна),
    # а поточна розкладка — англійська, значить користувач помилився.
    # І навпаки: якщо в іншій розкладці — англійська літера, а поточна — українська.
    if char_current not in NEUTRAL and char_other and char_other not in NEUTRAL and char_other != char_current:
        needs_fix = False
        corrected = None
        target_layout = None

        if layout == LANG_ENGLISH and char_other in UKR_TO_ENG:
            # В англ. розкладці, але на цій клавіші в укр. розкладці — укр. літера
            needs_fix = True
            corrected = char_other
            target_layout = LANG_UKRAINIAN
        elif layout == LANG_UKRAINIAN and char_other in ENG_TO_UKR:
            # В укр. розкладці, але на цій клавіші в англ. розкладці — англ. літера
            needs_fix = True
            corrected = char_other
            target_layout = LANG_ENGLISH

        if needs_fix:
            now = time.time()
            if now - last_correction_time < 0.3:
                return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)
            send_key(VK_BACK)
            time.sleep(0.01)
            switch_layout(target_layout)
            time.sleep(0.01)
            send_unicode_char(corrected)
            if typed_buffer:
                typed_buffer = typed_buffer[:-1] + corrected
            last_correction_time = now
            return 1

    return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)


# ═══════════════════════════════════════════════════════════════════════════════
# Автозапуск
# ═══════════════════════════════════════════════════════════════════════════════

def _get_startup_folder():
    return os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")

def _get_bat_path():
    return os.path.join(_get_startup_folder(), "Qwasda.bat")

def add_to_startup():
    startup_dir = _get_startup_folder()
    os.makedirs(startup_dir, exist_ok=True)
    script_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
    exe_path = sys.executable if getattr(sys, 'frozen', False) else os.path.join(script_dir, "qwasda.py")
    if getattr(sys, 'frozen', False):
        bat_content = f'@echo off\nstart "" "{exe_path}"\n'
    else:
        pythonw_exe = sys.executable.replace("python.exe", "pythonw.exe")
        bat_content = f'@echo off\nstart "" "{pythonw_exe}" "{exe_path}"\n'
    with open(_get_bat_path(), "w", encoding="utf-8") as f:
        f.write(bat_content)

def remove_from_startup():
    bat_path = _get_bat_path()
    if os.path.exists(bat_path):
        os.remove(bat_path)
        return True
    return False

def is_in_startup():
    return os.path.exists(_get_bat_path())


# ═══════════════════════════════════════════════════════════════════════════════
# Системний трей
# ═══════════════════════════════════════════════════════════════════════════════

def _create_icon_image():
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


def _toggle_trigger(icon, item):
    global trigger_key
    if trigger_key == VK_OEM_3:
        trigger_key = VK_OEM_7
    else:
        trigger_key = VK_OEM_3
    icon.update_menu()


def _toggle_startup(icon, item):
    if is_in_startup():
        remove_from_startup()
    else:
        add_to_startup()
    icon.update_menu()


def _on_exit(icon, item):
    global running, hook_handle
    running = False
    if hook_handle:
        user32.UnhookWindowsHookEx(hook_handle)
        hook_handle = None
    icon.stop()


def _trigger_name():
    return "Подвійне ` (backtick)" if trigger_key == VK_OEM_3 else "Подвійне ' (апостроф)"


def _make_menu():
    return pystray.Menu(
        pystray.MenuItem(
            lambda item: "✅ Автоперемикання увімкнено" if enabled else "⏸ Автоперемикання вимкнено",
            _toggle_enabled,
            checked=lambda item: enabled,
        ),
        pystray.MenuItem(
            lambda item: f"⌨️ Ручне: {_trigger_name()}",
            _toggle_trigger,
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
    icon = pystray.Icon(
        "Qwasda",
        _create_icon_image(),
        "Qwasda — перемикання розкладки",
        _make_menu(),
    )
    icon.notify("Qwasda запущено!", "Qwasda")
    icon.run()


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global running, hook_handle

    hinst = ctypes.pythonapi._handle
    hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_hook, hinst, 0)

    if not hook_handle:
        sys.exit(1)

    tray_thread = threading.Thread(target=run_tray, daemon=True)
    tray_thread.start()

    atexit.register(lambda: user32.UnhookWindowsHookEx(hook_handle) if hook_handle else None)

    def handler(sig, frame):
        global running, hook_handle
        running = False
        if hook_handle:
            user32.UnhookWindowsHookEx(hook_handle)
            hook_handle = None
        sys.exit(0)

    signal.signal(signal.SIGINT, handler)

    msg = ctypes.wintypes.MSG()
    while running:
        result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if result == 0 or result == -1:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    main()
