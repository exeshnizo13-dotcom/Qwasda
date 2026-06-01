"""
Qwasda — перемикач розкладки клавіатури (EN ↔ UK).

Ручне перемикання: двічі натиснути ` (backtick) або ' (апостроф) —
поточне слово конвертується між розкладками і розкладка переключається.

Правило автокорекції:
  Якщо в UKRAINIAN-розкладці набрали суто ASCII-літери (a-z), значить
  хотіли друкувати англійською але розкладка була не та → конвертуємо.
  (Зворотній випадок технічно неможливий: в EN-розкладці не можна набрати
  кирилицю — вона і так у буфері як латиниця.)

Критичні виправлення порівняно зі старою версією:
  1. Перевірка LLKHF_INJECTED — ігноруємо власні SendInput-події
  2. Автокорекція ТІЛЬКИ на межі слова, не посимвольно
  3. modifiers_pressed() — не чіпаємо хоткеї (Ctrl+C тощо)
  4. Буфер через scan codes — без ToUnicodeEx (він давав помилки для UK)

Запуск: Qwasda.exe  або  pythonw qwasda.py
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
# Win32
# ═══════════════════════════════════════════════════════════════════════════════

user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WM_KEYDOWN     = 0x0100
WM_SYSKEYDOWN  = 0x0104

VK_BACK     = 0x08
VK_TAB      = 0x09
VK_RETURN   = 0x0D
VK_SHIFT    = 0x10
VK_CONTROL  = 0x11
VK_MENU     = 0x12
VK_CAPITAL  = 0x14
VK_ESCAPE   = 0x1B
VK_SPACE    = 0x20
VK_LEFT     = 0x25
VK_UP       = 0x26
VK_RIGHT    = 0x27
VK_DOWN     = 0x28
VK_LWIN     = 0x5B
VK_RWIN     = 0x5C
VK_LSHIFT   = 0xA0
VK_RSHIFT   = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU    = 0xA4
VK_RMENU    = 0xA5
VK_OEM_3    = 0xC0   # ` backtick
VK_OEM_7    = 0xDE   # ' apostrophe

# bit 4 у KBDLLHOOKSTRUCT.flags → подія ін'єктована (від нашого SendInput)
LLKHF_INJECTED = 0x10

LANG_ENGLISH   = 0x0409
LANG_UKRAINIAN = 0x0422

MODIFIER_VKS = frozenset({
    VK_SHIFT, VK_CONTROL, VK_MENU, VK_CAPITAL,
    VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL,
    VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN,
})

WORD_BREAK_VKS = frozenset({VK_SPACE, VK_RETURN, VK_TAB})
NAV_CLEAR_VKS  = frozenset({VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_ESCAPE})

# ═══════════════════════════════════════════════════════════════════════════════
# Scan code → символ (фізична позиція → символ в розкладці)
# Тільки літери! Цифри/пунктуація — не враховуємо в буфері.
# ═══════════════════════════════════════════════════════════════════════════════

SCAN_ENG = {   # QWERTY
    0x10:'q', 0x11:'w', 0x12:'e', 0x13:'r', 0x14:'t', 0x15:'y',
    0x16:'u', 0x17:'i', 0x18:'o', 0x19:'p',
    0x1e:'a', 0x1f:'s', 0x20:'d', 0x21:'f', 0x22:'g', 0x23:'h',
    0x24:'j', 0x25:'k', 0x26:'l',
    0x2c:'z', 0x2d:'x', 0x2e:'c', 0x2f:'v', 0x30:'b', 0x31:'n', 0x32:'m',
}

SCAN_UKR = {   # ЙЦУКЕН
    0x10:'й', 0x11:'ц', 0x12:'у', 0x13:'к', 0x14:'е', 0x15:'н',
    0x16:'г', 0x17:'ш', 0x18:'щ', 0x19:'з',
    0x1e:'ф', 0x1f:'і', 0x20:'в', 0x21:'а', 0x22:'п', 0x23:'р',
    0x24:'о', 0x25:'л', 0x26:'д',
    0x2c:'я', 0x2d:'ч', 0x2e:'с', 0x2f:'м', 0x30:'и', 0x31:'т', 0x32:'ь',
}

# Ці scan codes відповідають літерам в ОБОХ розкладках
ENG_TO_UKR = {SCAN_ENG[sc]: SCAN_UKR[sc] for sc in SCAN_ENG if sc in SCAN_UKR}
UKR_TO_ENG = {v: k for k, v in ENG_TO_UKR.items()}

# Множини "чисто англійських" та "чисто українських" літер (що мають пари)
ENG_LETTERS = frozenset(ENG_TO_UKR.keys())   # a-z (ті що є в обох розкладках)
UKR_LETTERS = frozenset(UKR_TO_ENG.keys())   # українські літери

# Scan codes що є літерами (для визначення чи це буквена клавіша)
LETTER_SCAN_CODES = frozenset(SCAN_ENG.keys())


# ═══════════════════════════════════════════════════════════════════════════════
# Win32 структури
# ═══════════════════════════════════════════════════════════════════════════════

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode",      ctypes.wintypes.DWORD),
        ("scanCode",    ctypes.wintypes.DWORD),
        ("flags",       ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         ctypes.c_ushort),
        ("wScan",       ctypes.c_ushort),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _InputUnion)]


user32.SetWindowsHookExW.restype  = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_ulong]
user32.CallNextHookEx.restype     = ctypes.c_long
user32.CallNextHookEx.argtypes    = [ctypes.c_void_p, ctypes.c_int,
                                      ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.UnhookWindowsHookEx.restype  = ctypes.c_bool
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.GetKeyboardLayout.restype    = ctypes.c_void_p
user32.GetKeyboardLayout.argtypes   = [ctypes.c_ulong]
user32.ActivateKeyboardLayout.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.SendInput.restype  = ctypes.c_uint
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
user32.GetKeyState.restype  = ctypes.c_short
user32.GetKeyState.argtypes = [ctypes.c_int]


# ═══════════════════════════════════════════════════════════════════════════════
# Низькорівневі Win32 функції
# ═══════════════════════════════════════════════════════════════════════════════

def get_foreground_layout() -> int:
    hwnd = user32.GetForegroundWindow()
    tid  = user32.GetWindowThreadProcessId(hwnd, None)
    hkl  = user32.GetKeyboardLayout(tid)
    return hkl & 0xFFFF


def set_layout(lang_id: int):
    user32.ActivateKeyboardLayout(ctypes.c_void_p(lang_id), 0)


def any_modifier_down() -> bool:
    """True якщо Ctrl, Alt або Win зараз утиснуті."""
    for vk in (VK_LCONTROL, VK_RCONTROL,
               VK_LMENU,    VK_RMENU,
               VK_LWIN,     VK_RWIN):
        if user32.GetKeyState(vk) & 0x8000:
            return True
    return False


def _ki(vk=0, scan=0, flags=0) -> INPUT:
    inp = INPUT()
    inp.type = 1
    inp.union.ki.wVk     = vk
    inp.union.ki.wScan   = scan
    inp.union.ki.dwFlags = flags
    inp.union.ki.time    = 0
    inp.union.ki.dwExtraInfo = None
    return inp


def send_backspaces(n: int):
    if n <= 0:
        return
    evts = []
    for _ in range(n):
        evts.append(_ki(vk=VK_BACK, flags=0))
        evts.append(_ki(vk=VK_BACK, flags=2))   # KEYEVENTF_KEYUP
    arr = (INPUT * len(evts))(*evts)
    user32.SendInput(len(evts), ctypes.byref(arr), ctypes.sizeof(INPUT))


def send_unicode_string(text: str):
    if not text:
        return
    evts = []
    for ch in text:
        evts.append(_ki(scan=ord(ch), flags=0x0004))           # KEYEVENTF_UNICODE down
        evts.append(_ki(scan=ord(ch), flags=0x0004 | 0x0002))  # + KEYEVENTF_KEYUP
    arr = (INPUT * len(evts))(*evts)
    user32.SendInput(len(evts), ctypes.byref(arr), ctypes.sizeof(INPUT))


# ═══════════════════════════════════════════════════════════════════════════════
# Конвертація тексту між розкладками
# ═══════════════════════════════════════════════════════════════════════════════

def try_convert(word: str, current_layout: int):
    """
    Повертає (converted_word, target_layout) або (None, None).

    Для РУЧНОГО перемикання (manual_convert):
      Визначаємо напрямок за більшістю символів.

    Для АВТОКОРЕКЦІЇ (auto_correct_word):
      EN-розкладка: слово складається тільки з латинських літер, але
        переключення не потрібне (в EN розкладці це нормально).
      UK-розкладка: слово складається тільки з латинських літер →
        очевидно набрали в невірній розкладці → конвертуємо EN→UK.
    """
    letters = [ch.lower() for ch in word if ch.isalpha()]
    if not letters:
        return None, None

    if current_layout is None:
        # Ручне перемикання — визначаємо напрямок за вмістом
        eng_cnt = sum(1 for ch in letters if ch in ENG_LETTERS)
        ukr_cnt = sum(1 for ch in letters if ch in UKR_LETTERS)
        if eng_cnt > ukr_cnt:
            converted = "".join(ENG_TO_UKR.get(ch, ch) for ch in word.lower())
            return converted, LANG_UKRAINIAN
        elif ukr_cnt > eng_cnt:
            converted = "".join(UKR_TO_ENG.get(ch, ch) for ch in word.lower())
            return converted, LANG_ENGLISH
        return None, None
    else:
        # Автокорекція по межі слова
        if current_layout == LANG_UKRAINIAN:
            # В UK розкладці всі літери латинські → набрали в невірній розкладці
            all_eng = all(ch in ENG_LETTERS for ch in letters)
            if all_eng:
                converted = "".join(ENG_TO_UKR.get(ch, ch) for ch in word.lower())
                return converted, LANG_ENGLISH
        # EN розкладка: якщо слово є суто кирилицею — малоймовірно, але перевіримо
        elif current_layout == LANG_ENGLISH:
            all_ukr = all(ch in UKR_LETTERS for ch in letters)
            if all_ukr:
                converted = "".join(UKR_TO_ENG.get(ch, ch) for ch in word.lower())
                return converted, LANG_UKRAINIAN
        return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# Глобальний стан
# ═══════════════════════════════════════════════════════════════════════════════

running              = True
enabled              = True
auto_correct_enabled = True

typed_word        = ""    # Буфер поточного слова (з scan codes)
last_trigger_time = 0.0
trigger_key       = VK_OEM_3   # ` backtick
hook_handle       = None

# Mutex: зараз виконуємо корекцію — не обробляємо hook
_correcting = False


# ═══════════════════════════════════════════════════════════════════════════════
# Корекція
# ═══════════════════════════════════════════════════════════════════════════════

def _do_replace(word: str, converted: str, target_layout: int):
    """Видаляємо word, перемикаємо розкладку, друкуємо converted."""
    global typed_word, _correcting
    _correcting = True
    try:
        send_backspaces(len(word))
        time.sleep(0.025)
        set_layout(target_layout)
        time.sleep(0.025)
        send_unicode_string(converted)
        typed_word = converted
    finally:
        _correcting = False


def manual_convert():
    """Ручне перемикання — подвійний тригер."""
    word = typed_word.strip()
    if not word:
        return
    converted, target_layout = try_convert(word, None)
    if not converted or converted == word:
        return
    _do_replace(word, converted, target_layout)


def auto_correct_word(word: str, layout: int):
    """Автокорекція на межі слова."""
    converted, target_layout = try_convert(word, layout)
    if not converted or converted == word:
        return
    _do_replace(word, converted, target_layout)


# ═══════════════════════════════════════════════════════════════════════════════
# Keyboard hook
# ═══════════════════════════════════════════════════════════════════════════════

@ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int,
                    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
def keyboard_hook(nCode, wParam, lParam):
    global typed_word, last_trigger_time

    # ── Пропускаємо системні події ──────────────────────────────────────────
    if nCode < 0:
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    if wParam not in (WM_KEYDOWN, WM_SYSKEYDOWN):
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    kb    = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
    flags = kb.flags
    vk    = kb.vkCode
    sc    = kb.scanCode

    # ── КРИТИЧНО: Ігноруємо власні SendInput-події (LLKHF_INJECTED) ─────────
    if flags & LLKHF_INJECTED:
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Якщо корекція виконується — не заважаємо ────────────────────────────
    if _correcting:
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Модифікатори (Shift, CapsLock тощо) — пропускаємо ───────────────────
    if vk in MODIFIER_VKS:
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Ctrl / Alt / Win — НІКОЛИ не чіпаємо хоткеї ───────────────────────
    if any_modifier_down():
        typed_word = ""
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Ручне перемикання: подвійний тригер (`` ` `` або ' ) ────────────────
    if vk == trigger_key:
        now = time.time()
        if now - last_trigger_time < 0.5:
            last_trigger_time = 0.0
            if enabled:
                threading.Thread(target=manual_convert, daemon=True).start()
            return 1   # Блокуємо другий тригер
        last_trigger_time = now
        typed_word = ""   # Тригер не є літерою — скидаємо слово
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    if not enabled:
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Backspace — видаляємо останній символ з буфера ───────────────────────
    if vk == VK_BACK:
        if typed_word:
            typed_word = typed_word[:-1]
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Навігація — скидаємо буфер (курсор перемістився) ────────────────────
    if vk in NAV_CLEAR_VKS:
        typed_word = ""
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Межа слова (пробіл / enter / tab) ───────────────────────────────────
    if vk in WORD_BREAK_VKS:
        if auto_correct_enabled and typed_word:
            word   = typed_word
            layout = get_foreground_layout()
            threading.Thread(
                target=auto_correct_word, args=(word, layout), daemon=True
            ).start()
        typed_word = ""
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Буферизуємо тільки буквені клавіші (через scan code — без ToUnicodeEx)
    if sc in LETTER_SCAN_CODES:
        layout = get_foreground_layout()
        if layout == LANG_ENGLISH:
            ch = SCAN_ENG.get(sc)
        else:
            ch = SCAN_UKR.get(sc)
        if ch:
            typed_word += ch
            if len(typed_word) > 100:
                typed_word = typed_word[-50:]
    else:
        # Цифра / пунктуація / OEM — скидаємо буфер (межа слова)
        typed_word = ""

    return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)


# ═══════════════════════════════════════════════════════════════════════════════
# Автозапуск
# ═══════════════════════════════════════════════════════════════════════════════

def _startup_folder():
    return os.path.join(os.environ.get("APPDATA", ""),
                        r"Microsoft\Windows\Start Menu\Programs\Startup")

def _bat_path():
    return os.path.join(_startup_folder(), "Qwasda.bat")

def add_to_startup():
    os.makedirs(_startup_folder(), exist_ok=True)
    if getattr(sys, 'frozen', False):
        content = f'@echo off\nstart "" "{sys.executable}"\n'
    else:
        pw = sys.executable.replace("python.exe", "pythonw.exe")
        content = f'@echo off\nstart "" "{pw}" "{os.path.abspath(__file__)}"\n'
    with open(_bat_path(), "w", encoding="utf-8") as f:
        f.write(content)

def remove_from_startup():
    p = _bat_path()
    if os.path.exists(p):
        os.remove(p)

def is_in_startup():
    return os.path.exists(_bat_path())


# ═══════════════════════════════════════════════════════════════════════════════
# Системний трей
# ═══════════════════════════════════════════════════════════════════════════════

def _make_icon_image():
    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
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


def _toggle_auto(icon, item):
    global auto_correct_enabled
    auto_correct_enabled = not auto_correct_enabled
    icon.update_menu()


def _toggle_trigger(icon, item):
    global trigger_key
    trigger_key = VK_OEM_7 if trigger_key == VK_OEM_3 else VK_OEM_3
    icon.update_menu()


def _toggle_startup(icon, item):
    remove_from_startup() if is_in_startup() else add_to_startup()
    icon.update_menu()


def _on_exit(icon, item):
    global running, hook_handle
    running = False
    if hook_handle:
        user32.UnhookWindowsHookEx(hook_handle)
        hook_handle = None
    icon.stop()


def _make_menu():
    return pystray.Menu(
        pystray.MenuItem(
            lambda i: "✅ Qwasda увімкнено" if enabled else "⏸ Qwasda вимкнено",
            _toggle_enabled, checked=lambda i: enabled,
        ),
        pystray.MenuItem(
            lambda i: "🔄 Автокорекція: ON" if auto_correct_enabled else "🔄 Автокорекція: OFF",
            _toggle_auto, checked=lambda i: auto_correct_enabled,
        ),
        pystray.MenuItem(
            lambda i: "⌨️ Тригер: `` ` `` ×2" if trigger_key == VK_OEM_3 else "⌨️ Тригер: '' ×2",
            _toggle_trigger,
        ),
        pystray.MenuItem(
            lambda i: "✅ Автозапуск" if is_in_startup() else "❌ Автозапуск",
            _toggle_startup, checked=lambda i: is_in_startup(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Вихід", _on_exit),
    )


def _run_tray():
    icon = pystray.Icon(
        "Qwasda", _make_icon_image(),
        "Qwasda — перемикач розкладки", _make_menu(),
    )
    icon.notify("Qwasda запущено!", "Qwasda")
    icon.run()


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global running, hook_handle

    hinst       = ctypes.pythonapi._handle
    hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_hook, hinst, 0)

    if not hook_handle:
        sys.exit(1)

    threading.Thread(target=_run_tray, daemon=True).start()

    atexit.register(lambda: user32.UnhookWindowsHookEx(hook_handle) if hook_handle else None)

    def _sig(sig, frame):
        global running, hook_handle
        running = False
        if hook_handle:
            user32.UnhookWindowsHookEx(hook_handle)
            hook_handle = None
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig)

    msg = ctypes.wintypes.MSG()
    while running:
        r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r == 0 or r == -1:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


if __name__ == "__main__":
    main()
