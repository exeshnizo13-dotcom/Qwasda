"""
Qwasda — автоматичне визначення та перемикання розкладки клавіатури.
Виявляє, коли користувач друкує в неправильній розкладці (укр/англ),
перемикає розкладку та виправляє набраний текст.

Запуск: Qwasda.exe
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
WM_SYSKEYDOWN = 0x0104

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

# Мапування: українські → англійські (scan code → символ)
UKR_TO_ENG = {
    'й': 'q', 'ц': 'w', 'у': 'e', 'к': 'r', 'е': 't', 'н': 'y', 'г': 'u',
    'ш': 'i', 'щ': 'o', 'з': 'p', 'х': '[', 'ї': ']', 'ф': 'a', 'і': 's',
    'в': 'd', 'а': 'f', 'п': 'g', 'р': 'h', 'о': 'j', 'л': 'k', 'д': 'l',
    'ж': ';', 'є': "'", 'я': 'z', 'ч': 'x', 'с': 'c', 'м': 'v', 'и': 'b',
    'т': 'n', 'ь': 'm', 'б': ',', 'ю': '.', 'ґ': '\\',
    'Й': 'Q', 'Ц': 'W', 'У': 'E', 'К': 'R', 'Е': 'T', 'Н': 'Y', 'Г': 'U',
    'Ш': 'I', 'Щ': 'O', 'З': 'P', 'Х': '{', 'Ї': '}', 'Ф': 'A', 'І': 'S',
    'В': 'D', 'А': 'F', 'П': 'G', 'Р': 'H', 'О': 'J', 'Л': 'K', 'Д': 'L',
    'Ж': ':', 'Є': '"', 'Я': 'Z', 'Ч': 'X', 'С': 'C', 'М': 'V', 'И': 'B',
    'Т': 'N', 'Ь': 'M', 'Б': '<', 'Ю': '>', 'Ґ': '|',
}
ENG_TO_UKR = {v: k for k, v in UKR_TO_ENG.items()}
NEUTRAL = set('0123456789`-=[]\\;\',./~!@#$%^&*()_+{}|:"<>? \t\n\r')


# ─── Налаштування Win32 API типів ──────────────────────────────────────────────

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
user32.ToUnicodeEx.restype = ctypes.c_int
user32.ToUnicodeEx.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p]
user32.GetKeyboardState.argtypes = [ctypes.c_void_p]


# ─── Функції ───────────────────────────────────────────────────────────────────

def get_foreground_layout():
    hwnd = user32.GetForegroundWindow()
    thread_id = user32.GetWindowThreadProcessId(hwnd, None)
    hkl = user32.GetKeyboardLayout(thread_id)
    return hkl & 0xFFFF


def switch_layout(lang_id):
    user32.ActivateKeyboardLayout(ctypes.c_void_p(lang_id), 0)


def vk_to_unicode(vk_code, scan_code):
    state = ctypes.create_string_buffer(256)
    user32.GetKeyboardState(ctypes.byref(state))
    hwnd = user32.GetForegroundWindow()
    thread_id = user32.GetWindowThreadProcessId(hwnd, None)
    hkl = user32.GetKeyboardLayout(thread_id)
    buf = ctypes.create_unicode_buffer(8)
    result = user32.ToUnicodeEx(vk_code, scan_code, state, buf, 8, 0, hkl)
    if result > 0:
        return buf.value
    return None


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

    if nCode >= 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk = kb.vkCode
        sc = kb.scanCode

        # Ручне перемикання: подвійне натискання тригера
        if vk == trigger_key and enabled:
            now = time.time()
            if now - last_trigger_time < 0.4:
                last_trigger_time = 0
                manual_convert()
                return 1
            else:
                last_trigger_time = now
            return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

        if not enabled:
            return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

        if vk in MODIFIER_VKS:
            return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

        if vk in NAVIGATION_VKS:
            if vk == VK_BACK and typed_buffer:
                typed_buffer = typed_buffer[:-1]
            elif vk != VK_BACK:
                typed_buffer = ""
            return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

        char = vk_to_unicode(vk, sc)

        if char and len(char) == 1:
            if char not in NEUTRAL:
                typed_buffer += char
                if len(typed_buffer) > 200:
                    typed_buffer = typed_buffer[-100:]
            elif char == ' ':
                typed_buffer += ' '

            if char not in NEUTRAL:
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

    # Встановлюємо low-level hook
    hinst = ctypes.pythonapi._handle
    hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_hook, hinst, 0)

    if not hook_handle:
        sys.exit(1)

    # Запускаємо трей
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

    # Message loop
    msg = ctypes.wintypes.MSG()
    while running:
        result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if result == 0 or result == -1:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    main()
