"""
Qwasda — перемикач розкладки клавіатури (EN ↔ UK).

Ручне перемикання:
  Подвійне натискання Ctrl (два Ctrl поспіль без іншої клавіші між ними) —
  конвертує ВЕСЬ щойно набраний текст (усі слова, набрані не в тій розкладці,
  а не лише останнє). Спрацьовує на відпусканні другого Ctrl, щоб виправлення
  не склалось у Ctrl-хоткей.
  Якщо буфер порожній, а щойно спрацювала автокорекція — подвійний Ctrl
  відкочує її (юзеру вона не була потрібна).

Автокорекція (за словниками uk/en):
  На межі слова (пробіл/Enter/Tab) програма перевіряє набране слово:
    • якщо слово є валідним у поточній мові — не чіпає;
    • якщо слово невалідне, але його конвертація в іншу розкладку дає
      валідне слово з протилежного словника — виправляє розкладку.
  Словники лежать у data/words_en.txt.gz та data/words_uk.txt.gz.

Пам'ять (навчання, learned.json):
  • Ручне перемикання слова, якого автокорекція не чіпала → слово вивчається,
    щоб надалі перемикатись автоматично (FORCE_*).
  • Відкат автокорекції подвійним Ctrl → слово стає винятком (BLOCK_*),
    і надалі автокорекція його не чіпає.

Критичні особливості реалізації:
  1. LLKHF_INJECTED — ігноруємо власні SendInput-події
  2. Автокорекція ТІЛЬКИ на межі слова, за словниками (без посимвольної)
  3. any_modifier_down() — не чіпаємо хоткеї (Ctrl+C тощо)
  4. Буфер через scan codes — без ToUnicodeEx
  5. Перемикання розкладки активного вікна через WM_INPUTLANGCHANGEREQUEST
  6. Розкладка кешується (LL-hook має повертатися швидко)

Запуск: Qwasda.exe  або  pythonw qwasda.py
"""

import sys
import os
import json
import gzip
import time
from array import array
import ctypes
import ctypes.wintypes
import atexit
import signal
import threading

__version__ = "1.3.4"

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    ctypes.windll.user32.MessageBoxW(
        None,
        "Не встановлено залежності.\nВиконайте: pip install pystray pillow",
        "Qwasda", 0x10,
    )
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════════
# Діагностичний лог (QWASDA_DEBUG=1 → %TEMP%\qwasda_debug.log)
# ═══════════════════════════════════════════════════════════════════════════════

DEBUG = os.environ.get("QWASDA_DEBUG") == "1"
_log_path = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")),
                         "qwasda_debug.log")
_log_lock = threading.Lock()


def _dbg(msg: str):
    if not DEBUG:
        return
    try:
        with _log_lock, open(_log_path, "a", encoding="utf-8") as f:
            f.write("%.3f  %s\n" % (time.time(), msg))
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Win32
# ═══════════════════════════════════════════════════════════════════════════════

user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WM_KEYDOWN     = 0x0100
WM_KEYUP       = 0x0101
WM_SYSKEYDOWN  = 0x0104
WM_SYSKEYUP    = 0x0105
WM_QUIT        = 0x0012
WM_INPUTLANGCHANGEREQUEST = 0x0050

_DOWN_MSGS = frozenset({WM_KEYDOWN, WM_SYSKEYDOWN})
_UP_MSGS   = frozenset({WM_KEYUP, WM_SYSKEYUP})

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
VK_PRIOR    = 0x21  # Page Up
VK_NEXT     = 0x22  # Page Down
VK_END      = 0x23
VK_HOME     = 0x24
VK_DELETE   = 0x2E
VK_LWIN     = 0x5B
VK_RWIN     = 0x5C
VK_LSHIFT   = 0xA0
VK_RSHIFT   = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU    = 0xA4
VK_RMENU    = 0xA5

# bit 4 у KBDLLHOOKSTRUCT.flags → подія ін'єктована (від нашого SendInput)
LLKHF_INJECTED = 0x10

# Mouse hook constants
WH_KEYBOARD_LL = 13
WH_MOUSE_LL    = 14
WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_XBUTTONDOWN = 0x020B
_MOUSE_DOWN_MSGS = frozenset({WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_XBUTTONDOWN})
LLMHF_INJECTED = 0x01  # Mouse event flags bit 0 → injected event

KLF_ACTIVATE   = 0x00000001
LANG_ENGLISH   = 0x0409
LANG_UKRAINIAN = 0x0422

# Клавіша-тригер ручного перемикання — подвійний Ctrl.
# (Shift був ненадійним: його тиснуть постійно для великих літер, тож
#  послідовність «два Shift поспіль» легко рветься звичайним друком.)
CTRL_VKS = frozenset({VK_CONTROL, VK_LCONTROL, VK_RCONTROL})

MODIFIER_VKS = frozenset({
    VK_SHIFT, VK_CONTROL, VK_MENU, VK_CAPITAL,
    VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL,
    VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN,
})

WORD_BREAK_VKS = frozenset({VK_SPACE, VK_RETURN, VK_TAB})
NAV_CLEAR_VKS  = frozenset({VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_ESCAPE,
                             VK_HOME, VK_END, VK_PRIOR, VK_NEXT, VK_DELETE})

# Пунктуація-термінатор: клавіші, що ЗАВЕРШУЮТЬ слово одним видимим символом
# і мають запускати автокорекцію на щойно набраному слові (напр. «так?» —
# інакше слово перед пунктуацією ніколи не перевіряється). На відміну від
# пробілу/Enter, у фразі для ручного перемикання пунктуація лишається межею.
# OEM-клавіші пунктуації (крім тих, що в укр. розкладці є ЛІТЕРАМИ — ті йдуть
# у буфер через scan-код і сюди не потрапляють).
OEM_PUNCT_VKS = frozenset({
    0xBA,  # ; :
    0xBB,  # = +
    0xBC,  # , <
    0xBD,  # - _
    0xBE,  # . >
    0xBF,  # / ?
    0xC0,  # ` ~
    0xDB,  # [ {
    0xDC,  # \ |
    0xDD,  # ] }
    0xDE,  # ' "
})

# Пороги корекції та вікно подвійного тапу — значення за замовчуванням.
# Перевизначаються з config.json (див. load_config) і доступні як глобальні
# змінні, бо налаштовуються користувачем у рантаймі.
MIN_AUTOCORRECT_LEN  = 2     # не виправляти надто короткі слова (крім однолітерних зі списку)
MIN_EN_TO_UK         = 3     # напрямок EN→UK суворіший: укр. словник величезний (3.8M),
                             # тож короткі латинські токени легко випадково «стають» укр.
DOUBLE_TAP_WINDOW    = 0.4   # макс. пауза (с) між двома Ctrl, щоб вважати їх «подвійним»

# Валідні ОДНОЛІТЕРНІ слова — щоб автокорекція перемикала «я»/«і»/«з» тощо,
# набрані не в тій розкладці (напр. «я»→«z»), але НЕ чіпала кожну випадкову
# літеру. Слово з однієї літери виправляється лише якщо його читання в іншій
# розкладці є в цьому списку.
UK_SINGLE_WORDS = frozenset("аійоуязєбжв")   # а і й о у я з є б ж в (укр. однолітерні)
EN_SINGLE_WORDS = frozenset("ai")            # a, i

# ═══════════════════════════════════════════════════════════════════════════════
# Scan code → символ (фізична позиція → символ у розкладці)
# ═══════════════════════════════════════════════════════════════════════════════

# Англійська (QWERTY) — тільки літери a-z.
SCAN_ENG = {
    0x10:'q', 0x11:'w', 0x12:'e', 0x13:'r', 0x14:'t', 0x15:'y',
    0x16:'u', 0x17:'i', 0x18:'o', 0x19:'p',
    0x1e:'a', 0x1f:'s', 0x20:'d', 0x21:'f', 0x22:'g', 0x23:'h',
    0x24:'j', 0x25:'k', 0x26:'l',
    0x2c:'z', 0x2d:'x', 0x2e:'c', 0x2f:'v', 0x30:'b', 0x31:'n', 0x32:'m',
}

# Українська (ЙЦУКЕН) — повний набір літер, включно з тими, що сидять
# на «пунктуаційних» клавішах англійської розкладки.
SCAN_UKR = {
    0x10:'й', 0x11:'ц', 0x12:'у', 0x13:'к', 0x14:'е', 0x15:'н',
    0x16:'г', 0x17:'ш', 0x18:'щ', 0x19:'з', 0x1a:'х', 0x1b:'ї',
    0x1e:'ф', 0x1f:'і', 0x20:'в', 0x21:'а', 0x22:'п', 0x23:'р',
    0x24:'о', 0x25:'л', 0x26:'д', 0x27:'ж', 0x28:'є',
    0x2c:'я', 0x2d:'ч', 0x2e:'с', 0x2f:'м', 0x30:'и', 0x31:'т', 0x32:'ь',
    0x33:'б', 0x34:'ю', 0x2b:'ґ',
}

# Символ англійської розкладки для КОЖНОЇ позиції українських літер
# (для тих клавіш, де англійська дає пунктуацію).
ENG_AT_POS = dict(SCAN_ENG)
ENG_AT_POS.update({
    0x1a:'[', 0x1b:']', 0x27:';', 0x28:"'", 0x33:',', 0x34:'.', 0x2b:'\\',
})

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


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt",          POINT),
        ("mouseData",   ctypes.wintypes.DWORD),
        ("flags",       ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize",        ctypes.wintypes.DWORD),
        ("flags",         ctypes.wintypes.DWORD),
        ("hwndActive",    ctypes.c_void_p),
        ("hwndFocus",     ctypes.c_void_p),
        ("hwndCapture",   ctypes.c_void_p),
        ("hwndMenuOwner", ctypes.c_void_p),
        ("hwndMoveSize",  ctypes.c_void_p),
        ("hwndCaret",     ctypes.c_void_p),
        ("rcCaret",       ctypes.wintypes.RECT),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         ctypes.c_ushort),
        ("wScan",       ctypes.c_ushort),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    # Потрібна лише для коректного розміру union/INPUT (на x64 — найбільший член).
    _fields_ = [
        ("dx",          ctypes.c_long),
        ("dy",          ctypes.c_long),
        ("mouseData",   ctypes.c_ulong),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _InputUnion(ctypes.Union):
    # MOUSEINPUT обов'язково, інакше sizeof(INPUT)=32 замість 40 на x64
    # і SendInput відхиляє події (cbSize != sizeof(INPUT)).
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _InputUnion)]


if ctypes.sizeof(INPUT) != 40 and ctypes.sizeof(ctypes.c_void_p) != 4:
    raise RuntimeError("INPUT struct size mismatch — SendInput не працюватиме")


user32.SetWindowsHookExW.restype  = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                      ctypes.c_void_p, ctypes.c_ulong]
user32.CallNextHookEx.restype     = ctypes.c_long
user32.CallNextHookEx.argtypes    = [ctypes.c_void_p, ctypes.c_int,
                                      ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.UnhookWindowsHookEx.restype  = ctypes.c_bool
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.GetForegroundWindow.restype  = ctypes.c_void_p
user32.GetKeyboardLayout.restype    = ctypes.c_void_p
user32.GetKeyboardLayout.argtypes   = [ctypes.c_ulong]
user32.LoadKeyboardLayoutW.restype  = ctypes.c_void_p
user32.LoadKeyboardLayoutW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
user32.PostMessageW.restype  = ctypes.c_int
user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                ctypes.c_void_p, ctypes.c_void_p]
user32.PostThreadMessageW.restype  = ctypes.c_int
user32.PostThreadMessageW.argtypes = [ctypes.c_ulong, ctypes.c_uint,
                                      ctypes.c_void_p, ctypes.c_void_p]
user32.SendInput.restype  = ctypes.c_uint
user32.SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
user32.GetKeyState.restype  = ctypes.c_short
user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetWindowThreadProcessId.restype  = ctypes.c_ulong
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.AttachThreadInput.restype  = ctypes.c_bool
user32.AttachThreadInput.argtypes = [ctypes.c_ulong, ctypes.c_ulong, ctypes.c_bool]
user32.ActivateKeyboardLayout.restype  = ctypes.c_void_p
user32.ActivateKeyboardLayout.argtypes = [ctypes.c_void_p, ctypes.c_uint]
user32.GetClassNameW.restype  = ctypes.c_int
user32.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.GetGUIThreadInfo.restype  = ctypes.c_bool
user32.GetGUIThreadInfo.argtypes = [ctypes.c_ulong, ctypes.c_void_p]
user32.GetKeyboardLayoutList.restype  = ctypes.c_int
user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.c_void_p]
kernel32.GetCurrentThreadId.restype = ctypes.c_ulong
kernel32.CreateMutexW.restype  = ctypes.c_void_p
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]


# ═══════════════════════════════════════════════════════════════════════════════
# Низькорівневі Win32 функції
# ═══════════════════════════════════════════════════════════════════════════════

_seen_hkl     = {}   # lang_id → останній РЕАЛЬНО використаний повний HKL
_hkl_fallback = {}   # lang_id → HKL зі списку встановлених (запасний)


def _pick_installed_hkl(lang_id: int):
    """
    HKL уже встановленої розкладки для мови, БЕЗ LoadKeyboardLayoutW
    (щоб не додавати в систему типову розкладку поряд з обраною).
    Якщо для мови є кілька розкладок (напр. «українська» і «українська
    розширена»), віддаємо перевагу нетиповій підрозкладці.
    """
    n = user32.GetKeyboardLayoutList(0, None)
    best = None
    if n > 0:
        arr = (ctypes.c_void_p * n)()
        user32.GetKeyboardLayoutList(n, arr)
        for h in arr:
            if h and (h & 0xFFFF) == lang_id:
                if best is None:
                    best = h
                if ((h >> 16) & 0xFFFF) != lang_id:   # не-типова (як «розширена»)
                    return h
    if best is not None:
        return best
    return user32.LoadKeyboardLayoutW("%08x" % lang_id, KLF_ACTIVATE)


def _hkl_for(lang_id: int):
    """Повний HKL для перемикання: спершу той, що користувач реально вживає."""
    h = _seen_hkl.get(lang_id)
    if h:
        return h
    h = _hkl_fallback.get(lang_id)
    if h is None:
        h = _pick_installed_hkl(lang_id)
        _hkl_fallback[lang_id] = h
    return h


def _fg_class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def get_foreground_layout() -> int:
    """
    Розкладка вікна, що РЕАЛЬНО отримує клавіші.

    У сучасних застосунків (новий Notepad, Chrome, UWP) поле вводу живе в
    окремому потоці, не в тому, що повертає GetForegroundWindow(). Тому
    шукаємо сфокусований контрол через GetGUIThreadInfo і беремо розкладку
    ЙОГО потоку, а не потоку top-level вікна.
    """
    hwnd   = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(hwnd, None)

    target_tid = fg_tid
    gui = GUITHREADINFO()
    gui.cbSize = ctypes.sizeof(GUITHREADINFO)
    if user32.GetGUIThreadInfo(fg_tid, ctypes.byref(gui)) and gui.hwndFocus:
        ft = user32.GetWindowThreadProcessId(gui.hwndFocus, None)
        if ft:
            target_tid = ft

    hkl  = user32.GetKeyboardLayout(target_tid)
    lang = hkl & 0xFFFF
    if hkl:
        _seen_hkl[lang] = hkl          # памʼятаємо саме ту розкладку, що в ужитку
    if DEBUG:
        _dbg("layout-probe: class=%r fg_tid=%s focus_tid=%s hkl=0x%x -> lang=0x%04x"
             % (_fg_class(hwnd), fg_tid, target_tid, (hkl or 0) & 0xFFFFFFFF, lang))
    return lang


def set_foreground_layout(lang_id: int):
    """
    Перемикає розкладку активного вікна — двома способами, бо жоден
    не універсальний:
      1) WM_INPUTLANGCHANGEREQUEST — працює у класичних Win32-вікнах;
      2) AttachThreadInput + ActivateKeyboardLayout — потрібно для Chrome,
         Electron, UWP та інших, що ігнорують повідомлення (як у Punto).
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        _dbg("set_layout: no foreground window")
        return
    hkl = _hkl_for(lang_id)

    # Спосіб 1
    user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, None, hkl)

    # Спосіб 2
    fg_tid  = user32.GetWindowThreadProcessId(hwnd, None)
    cur_tid = kernel32.GetCurrentThreadId()
    attached = False
    if fg_tid and fg_tid != cur_tid:
        attached = user32.AttachThreadInput(cur_tid, fg_tid, True)
    try:
        user32.ActivateKeyboardLayout(hkl, KLF_ACTIVATE)
    finally:
        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
    _dbg("set_layout: lang=%04x hkl=%s fg_tid=%s attached=%s"
         % (lang_id, hkl, fg_tid, attached))


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
    sent = user32.SendInput(len(evts), ctypes.byref(arr), ctypes.sizeof(INPUT))
    _dbg("send_backspaces: n=%d sent=%d/%d err=%d"
         % (n, sent, len(evts), kernel32.GetLastError()))


def send_key(vk: int):
    """Натискання реальної віртуальної клавіші (для Enter/Tab — не Unicode-символ)."""
    arr = (INPUT * 2)(_ki(vk=vk, flags=0), _ki(vk=vk, flags=2))
    user32.SendInput(2, ctypes.byref(arr), ctypes.sizeof(INPUT))


def send_key_shifted(vk: int, shifted: bool):
    """
    Відтворення фізичної клавіші з врахуванням Shift — для пунктуації-термінатора
    (напр. «?» = Shift+«/»). Відтворюємо в ПОТОЧНІЙ (ще не переключеній) розкладці,
    тож символ виходить той самий, що набрав користувач, без ToUnicodeEx.
    """
    evts = []
    if shifted:
        evts.append(_ki(vk=VK_SHIFT, flags=0))
    evts.append(_ki(vk=vk, flags=0))
    evts.append(_ki(vk=vk, flags=2))
    if shifted:
        evts.append(_ki(vk=VK_SHIFT, flags=2))
    arr = (INPUT * len(evts))(*evts)
    user32.SendInput(len(evts), ctypes.byref(arr), ctypes.sizeof(INPUT))


def send_unicode_string(text: str):
    if not text:
        return
    evts = []
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:        # поза BMP — пропускаємо (укр./англ. сюди не потрапляють)
            continue
        evts.append(_ki(scan=code, flags=0x0004))           # KEYEVENTF_UNICODE down
        evts.append(_ki(scan=code, flags=0x0004 | 0x0002))  # + KEYEVENTF_KEYUP
    arr = (INPUT * len(evts))(*evts)
    sent = user32.SendInput(len(evts), ctypes.byref(arr), ctypes.sizeof(INPUT))
    _dbg("send_unicode: text=%r sent=%d/%d err=%d"
         % (text, sent, len(evts), kernel32.GetLastError()))


# ═══════════════════════════════════════════════════════════════════════════════
# Словники
# ═══════════════════════════════════════════════════════════════════════════════

class SortedWordIndex:
    """
    Членство по великому відсортованому списку слів через бінарний пошук
    прямо в gzip-розпакованому блобі. Український словник — 3.8 млн словоформ;
    тримати їх як frozenset коштувало б ~480 МБ, а так — ~90 МБ.

    Вимога: рядки відсортовані в байтовому порядку (UTF-8 зберігає порядок
    кодпоінтів), файл закінчується '\\n'.
    """
    __slots__ = ("_data", "_offs", "_count")

    def __init__(self, data: bytes):
        if b"\r" in data:                 # стійкість до CRLF-файлів
            data = data.replace(b"\r", b"")
        if data and not data.endswith(b"\n"):
            data += b"\n"
        if len(data) >= 2 ** 32:
            raise ValueError("словник завеликий для 32-бітних офсетів")
        self._data = data
        offs = array("I", [0])            # 4 байти/запис (офсети < 4 ГБ) — економить ~15 МБ
        find, append = data.find, offs.append
        i = find(b"\n")
        while i != -1:
            append(i + 1)
            i = find(b"\n", i + 1)
        self._offs  = offs
        self._count = len(offs) - 1   # кількість рядків (= слів)

    def __len__(self):
        return self._count

    def __contains__(self, word: str) -> bool:
        key  = word.encode("utf-8")
        data = self._data
        offs = self._offs
        lo, hi = 0, self._count
        while lo < hi:
            mid = (lo + hi) >> 1
            cur = data[offs[mid]:offs[mid + 1] - 1]   # без завершального '\n'
            if cur < key:
                lo = mid + 1
            elif cur > key:
                hi = mid
            else:
                return True
        return False


DICT_EN: frozenset = frozenset()
DICT_UK = SortedWordIndex(b"")
dicts_loaded = False

# ── Вивчені користувачем слова (персистентні, learned.json) ─────────────────
# FORCE_* — слова, які автокорекція має ПРИМУСОВО виправляти, навіть якщо їх
#           нема у словнику (юзер сам перемкнув, бо авто не спрацювало).
# BLOCK_* — слова-винятки, які автокорекція НЕ має чіпати (юзер відкотив
#           небажане автовиправлення). Усі — у нижньому регістрі.
FORCE_EN: set = set()   # укр.-набране, що треба перемикати в EN
FORCE_UK: set = set()   # лат.-набране, що треба перемикати в UK
BLOCK_UK: set = set()   # укр. слова, які лишати як є (не робити з них EN)
BLOCK_EN: set = set()   # англ. слова, які лишати як є (не робити з них UK)


def _resource(name: str) -> str:
    base = getattr(sys, "_MEIPASS",
                   os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "data", name)


def _load_frozenset(name: str) -> frozenset:
    try:
        with gzip.open(_resource(name), "rt", encoding="utf-8") as f:
            return frozenset(line.strip() for line in f if line.strip())
    except OSError:
        return frozenset()


def _load_index(name: str) -> SortedWordIndex:
    try:
        with gzip.open(_resource(name), "rb") as f:
            return SortedWordIndex(f.read())
    except OSError:
        return SortedWordIndex(b"")


def load_dicts():
    """Завантажує словники у фоні (укр. ~3.8M словоформ — не блокуємо старт)."""
    global DICT_EN, DICT_UK, dicts_loaded
    DICT_EN = _load_frozenset("words_en.txt.gz")   # ~370k форм — компактно як set
    DICT_UK = _load_index("words_uk.txt.gz")        # ~3.8M форм — бінарний пошук
    dicts_loaded = bool(len(DICT_EN) and len(DICT_UK))
    _dbg("dicts loaded: en=%d uk=%d ok=%s" % (len(DICT_EN), len(DICT_UK), dicts_loaded))
    if tray_icon is not None:
        tray_icon.update_menu()


# ═══════════════════════════════════════════════════════════════════════════════
# Конвертація тексту між розкладками
# ═══════════════════════════════════════════════════════════════════════════════

# Буфер слова зберігає SCAN-КОДИ клавіш, а не символи. Це ключове: літери
# ю/є/х/ї/ж/б/ґ в укр. розкладці сидять на клавішах .'[];,\ — якби ми
# буферизували символи поточної розкладки, ці клавіші в англ. розкладці
# давали б пунктуацію і рвали слово. Зі scan-кодами слово читається в ОБОХ
# розкладках уже на межі слова (як у Punto Switcher).

LETTER_SCANS = frozenset(SCAN_UKR)   # всі scan-коди, що є літерами хоч в одній розкладці

# Буфер тримає пари (scan_code, shifted), де shifted — чи була велика літера
# на момент натискання (Shift XOR CapsLock). Завдяки цьому при корекції
# зберігається регістр («Привіт» лишається «Привіт», а не «привіт»).


def _read(scans, table) -> str:
    out = []
    for sc, shifted in scans:
        ch = table.get(sc, "")
        out.append(ch.upper() if shifted else ch)
    return "".join(out)


def scans_to_ukr(scans) -> str:
    return _read(scans, SCAN_UKR)


def scans_to_eng(scans) -> str:
    return _read(scans, ENG_AT_POS)


def manual_target(scans, layout: int):
    """
    Ручне перемикання — читаємо ті самі клавіші в ІНШІЙ розкладці.
    Працює лише з UK/EN; для інших розкладок (рос. тощо) — нічого.
    Повертає (converted, target_layout) або (None, None).
    """
    if not scans or layout not in (LANG_UKRAINIAN, LANG_ENGLISH):
        return None, None
    if layout == LANG_UKRAINIAN:
        return scans_to_eng(scans), LANG_ENGLISH
    return scans_to_ukr(scans), LANG_UKRAINIAN


def autocorrect_target(scans, layout: int):
    """
    Автокорекція за словниками на основі scan-кодів. Читаємо слово в обох
    розкладках і дивимось, у якій воно валідне. Зберігаємо регістр.
    Працює лише коли активна UK або EN (рос. та інші не чіпаємо).
    Повертає (converted, target_layout) або (None, None).
    """
    if not dicts_loaded or not scans:
        return None, None
    if layout not in (LANG_UKRAINIAN, LANG_ENGLISH):
        return None, None
    ukr = scans_to_ukr(scans)            # як слово виглядає в укр. розкладці (з регістром)
    eng = scans_to_eng(scans)            # ... і в англійській
    ukr_l, eng_l = ukr.lower(), eng.lower()

    # Однолітерні слова — окремою гілкою: виправляємо лише валідні однолітерні
    # слова (я, і, з, у, в, о, а, є, й / a, i), інакше кожна випадкова літера
    # перемикалась би. Обходить загальний поріг MIN_AUTOCORRECT_LEN.
    if len(scans) == 1:
        if layout == LANG_UKRAINIAN:
            if ukr_l in BLOCK_UK or ukr_l in UK_SINGLE_WORDS:
                return None, None       # валідна укр. однолітерна / виняток — лишаємо
            if eng_l in EN_SINGLE_WORDS or eng_l in FORCE_EN:
                return eng, LANG_ENGLISH
        else:
            if eng_l in BLOCK_EN or eng_l in EN_SINGLE_WORDS:
                return None, None
            if ukr_l in UK_SINGLE_WORDS or ukr_l in FORCE_UK:
                return ukr, LANG_UKRAINIAN
        return None, None

    if len(scans) < MIN_AUTOCORRECT_LEN:
        return None, None

    if layout == LANG_UKRAINIAN:
        # На екрані зараз ukr.
        if ukr_l in BLOCK_UK:            # юзер відкотив це виправлення — не чіпаємо
            return None, None
        if ukr_l in DICT_UK:             # валідне укр. слово — не чіпаємо
            return None, None
        # В англ. читанні — валідне слово АБО вивчене користувачем.
        if eng_l in DICT_EN or eng_l in FORCE_EN:
            return eng, LANG_ENGLISH
    else:
        # На екрані зараз eng (латинська розкладка).
        if eng_l in BLOCK_EN:            # юзер відкотив це виправлення — не чіпаємо
            return None, None
        if eng_l in DICT_EN:
            return None, None
        # Вивчене слово перемикаємо без огляду на поріг довжини; словникове —
        # лише з суворішим порогом MIN_EN_TO_UK проти випадкових збігів.
        if ukr_l in FORCE_UK or (len(scans) >= MIN_EN_TO_UK and ukr_l in DICT_UK):
            return ukr, LANG_UKRAINIAN
    return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# Ручне перемикання цілої фрази (кілька слів через межі слів)
# ═══════════════════════════════════════════════════════════════════════════════

# phrase_tokens — накопичена «фраза» для ручного перемикання подвійним Ctrl.
# Токен: ['w', [(scan, shifted), ...]] — слово (літерні scan-коди)
#        ['s', vk]                     — роздільник (пробіл/Enter/Tab)
# На відміну від typed_scans (лише поточне слово), фраза переживає межі слів,
# тож подвійний Ctrl перемикає ВЕСЬ попередній текст, набраний не в тій
# розкладці, а не лише останнє слово. Очищається, щойно спрацьовує
# автокорекція (тоді текст уже «осів» правильно й переконвертувати його цілим
# було б хибно), а також на навігації/пунктуації/хоткеях.


def convert_phrase(phrase, layout: int):
    """
    Ручне перемикання фрази: кожне слово читається в ІНШІЙ розкладці,
    роздільники лишаються як є. Повертає (segments, strip_len, target_layout)
    або (None, 0, None), якщо конвертувати нічого.

    segments — послідовність ('text', str) та ('sep', vk) для передруку;
    strip_len — скільки видимих символів стерти (слова + роздільники).
    """
    if not phrase or layout not in (LANG_UKRAINIAN, LANG_ENGLISH):
        return None, 0, None
    to_eng = layout == LANG_UKRAINIAN
    target = LANG_ENGLISH if to_eng else LANG_UKRAINIAN
    segments = []
    strip_len = 0
    has_word = False
    for tok in phrase:
        if tok[0] == "w":
            if not tok[1]:
                continue
            text = scans_to_eng(tok[1]) if to_eng else scans_to_ukr(tok[1])
            segments.append(("text", text))
            strip_len += len(tok[1])
            has_word = True
        else:  # роздільник
            segments.append(("sep", tok[1]))
            strip_len += 1
    if not has_word:
        return None, 0, None
    return segments, strip_len, target


def is_word_terminator(vk: int, shifted: bool) -> bool:
    """
    Чи завершує ця клавіша слово одним видимим символом (пунктуація), що має
    запускати автокорекцію попереднього слова. OEM-пунктуація — завжди;
    цифрова клавіша — лише з Shift (символи !@#$%%^&*() тощо), бо звичайні
    цифри часто є частиною слова-ідентифікатора.
    """
    if vk in OEM_PUNCT_VKS:
        return True
    if 0x30 <= vk <= 0x39 and shifted:
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Пам'ять: вивчання слів та винятків (learned.json)
# ═══════════════════════════════════════════════════════════════════════════════

def learn_valid_word(word_l: str, target_layout: int) -> bool:
    """Запам'ятати слово як валідне для мови (щоб автокорекція перемикала його).
    Повертає True, якщо множина змінилась."""
    tgt = FORCE_EN if target_layout == LANG_ENGLISH else FORCE_UK
    if word_l in tgt:
        return False
    tgt.add(word_l)
    return True


def learn_block_word(word_l: str, layout: int) -> bool:
    """Запам'ятати слово як виняток (автокорекція не має його чіпати).
    layout — мова, в якій слово має ЛИШИТИСЬ. Повертає True, якщо змінилось."""
    tgt = BLOCK_UK if layout == LANG_UKRAINIAN else BLOCK_EN
    if word_l in tgt:
        return False
    tgt.add(word_l)
    return True


def forget_learned():
    """Очистити всю вивчену пам'ять."""
    for s in (FORCE_EN, FORCE_UK, BLOCK_UK, BLOCK_EN):
        s.clear()
    save_learned()


# ═══════════════════════════════════════════════════════════════════════════════
# Конфігурація (персистентна, %APPDATA%\Qwasda\config.json)
# ═══════════════════════════════════════════════════════════════════════════════

APP_DIR      = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Qwasda")
CONFIG_PATH  = os.path.join(APP_DIR, "config.json")
LEARNED_PATH = os.path.join(APP_DIR, "learned.json")

# Ключі конфігу, що мапляться на однойменні (великими) глобальні змінні-пороги.
_CONFIG_TUNABLES = ("MIN_AUTOCORRECT_LEN", "MIN_EN_TO_UK", "DOUBLE_TAP_WINDOW")


def _config_snapshot() -> dict:
    """Поточний стан, який варто зберегти між запусками."""
    g = globals()
    snap = {
        "enabled": enabled,
        "auto_correct_enabled": auto_correct_enabled,
        "learning_enabled": learning_enabled,
    }
    for k in _CONFIG_TUNABLES:
        snap[k.lower()] = g[k]
    return snap


def load_config():
    """Читає config.json і застосовує його до глобального стану (тихо, якщо файлу нема)."""
    global enabled, auto_correct_enabled, learning_enabled
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(cfg, dict):
        return
    if isinstance(cfg.get("enabled"), bool):
        enabled = cfg["enabled"]
    if isinstance(cfg.get("auto_correct_enabled"), bool):
        auto_correct_enabled = cfg["auto_correct_enabled"]
    if isinstance(cfg.get("learning_enabled"), bool):
        learning_enabled = cfg["learning_enabled"]
    g = globals()
    for k in _CONFIG_TUNABLES:
        v = cfg.get(k.lower())
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            g[k] = v
    _dbg("config loaded: %s" % _config_snapshot())


def save_config():
    """Атомарно зберігає поточний стан у config.json (помилки не фатальні)."""
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_config_snapshot(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except OSError as e:
        _dbg("save_config failed: %s" % e)


# Множини вивчених слів ↔ ключі у learned.json.
_LEARNED_SETS = ("FORCE_EN", "FORCE_UK", "BLOCK_UK", "BLOCK_EN")


def load_learned():
    """Читає learned.json у множини FORCE_*/BLOCK_* (тихо, якщо файлу нема)."""
    try:
        with open(LEARNED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    g = globals()
    for k in _LEARNED_SETS:
        vals = data.get(k.lower())
        if isinstance(vals, list):
            g[k].clear()
            g[k].update(str(w).lower() for w in vals if isinstance(w, str) and w)
    _dbg("learned loaded: force_en=%d force_uk=%d block_uk=%d block_en=%d"
         % (len(FORCE_EN), len(FORCE_UK), len(BLOCK_UK), len(BLOCK_EN)))


def save_learned():
    """Атомарно зберігає вивчені слова у learned.json (помилки не фатальні)."""
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        g = globals()
        data = {k.lower(): sorted(g[k]) for k in _LEARNED_SETS}
        tmp = LEARNED_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LEARNED_PATH)
    except OSError as e:
        _dbg("save_learned failed: %s" % e)


# ═══════════════════════════════════════════════════════════════════════════════
# Детектор подвійного тапу клавіші-тригера (подвійний Ctrl)
# ═══════════════════════════════════════════════════════════════════════════════

class DoubleTapDetector:
    """
    Розпізнає подвійний тап клавіші-тригера (Ctrl) — два чистих тапи поспіль
    без будь-якої іншої клавіші між ними. Спрацьовує на ВІДПУСКАННІ другого
    тапу, щоб у момент дії фізична клавіша вже була відпущена.

    Чистий тап = Ctrl натиснуто й відпущено, і поки він був утиснутий, не
    натискали інших клавіш (інакше це хоткей на кшталт Ctrl+C, не тап).
    """
    __slots__ = ("_last_tap", "_down", "_interrupted")

    def __init__(self):
        self._last_tap    = 0.0
        self._down        = False
        self._interrupted = True   # поки не було чистого тапу

    def on_trigger_down(self):
        """Натискання Ctrl (auto-repeat ігнорується)."""
        if not self._down:
            self._down        = True
            self._interrupted = False

    def on_trigger_up(self, now: float, window: float) -> bool:
        """
        Відпускання Ctrl. Повертає True, якщо це другий чистий тап у межах
        вікна `window` секунд — тобто час спрацювати.
        """
        self._down = False
        if self._interrupted:
            self._last_tap = 0.0
            return False
        if self._last_tap and now - self._last_tap < window:
            self._last_tap = 0.0
            return True
        self._last_tap = now       # перший тап — чекаємо другий
        return False

    def on_other_key(self):
        """Будь-яка інша клавіша «бруднить» поточний тап і рве ланцюг."""
        self._interrupted = True
        self._last_tap    = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Детектор редагування: якщо курсор рухався, наступний фрагмент—вибір шматка
# ═══════════════════════════════════════════════════════════════════════════════

class CaretGuard:
    """
    Запобігає автокорекції першого фрагмента після навігації клавішами.

    Логіка: якщо користувач натиснув навігаційну клавішу (стрілка, Home, End,
    PgUp, PgDn, Delete), то буфер очистили, але наступне введення—імовірно
    редагування наявного слова, а не новий текст. На межі слова (пробіл/Enter)
    чи пунктуації перевірка скидається.
    """
    __slots__ = ("_suppressed",)

    def __init__(self):
        self._suppressed = False

    def on_nav(self):
        """Навігаційна клавіша натиснута—наступний фрагмент може бути редагуванням."""
        self._suppressed = True

    def on_word_break(self) -> bool:
        """
        Межа слова. Повертає True, якщо була заборона на автокорекцію.
        Скидає прапорець (межа = свіже слово).
        """
        result = self._suppressed
        self._suppressed = False
        return result

    def on_focus_change(self):
        """Зміна вікна—буфер видив інше середовище, свіжий контекст."""
        self._suppressed = False

    @property
    def suppressed(self) -> bool:
        """True, якщо наступна автокорекція мала б бути заблокована."""
        return self._suppressed


# ═══════════════════════════════════════════════════════════════════════════════
# Глобальний стан
# ═══════════════════════════════════════════════════════════════════════════════

running              = True
enabled              = True
auto_correct_enabled = True
learning_enabled     = True        # запам'ятовувати ручні перемикання (пам'ять)
tray_icon            = None
ctrl_tap             = DoubleTapDetector()
caret_guard          = CaretGuard()

typed_scans       = []     # Буфер поточного слова — scan-коди клавіш (не символи)
phrase_tokens     = []     # Буфер фрази для перемикання всього тексту (див. convert_phrase)
hook_handle       = None
mouse_hook_handle = None
main_thread_id    = 0

# Активне вікно на момент попереднього натискання. Якщо змінилося — курсор/контекст
# інші, а буфери (typed_scans/phrase_tokens) більше не відповідають екрану.
_last_hwnd        = None

# Кеш розкладки (LL-hook має повертатися швидко)
_cached_layout      = LANG_ENGLISH
_cached_layout_time = 0.0

# Зараз виконуємо корекцію — не обробляємо hook
_correcting   = False
_correct_lock = threading.Lock()   # серіалізує заміни (одна за раз)
_input_seq    = 0                  # лічильник реальних натискань (для скасування гонки)
_pending_corrections = []          # (orig_len, converted, target_layout, sep_vk) — відкладені виправлення

# Остання автокорекція, яку можна відкотити подвійним Ctrl (для пам'яті винятків).
# (orig_scans, from_layout, to_layout, converted_text, sep_vk) або None.
_last_autocorrect          = None
_autocorrect_undo_available = False   # True одразу після автокорекції, поки юзер не друкує далі


def _foreground_changed() -> bool:
    """
    True, якщо активне вікно змінилося з моменту попереднього натискання.
    Дешевий виклик (читає глобальний стан) — безпечно кликати в LL-hook на кожну
    клавішу. Перший виклик (коли _last_hwnd is None) поверне True, але буфери тоді
    порожні, тож скидання нешкідливе.
    """
    global _last_hwnd
    hwnd = user32.GetForegroundWindow()
    if hwnd != _last_hwnd:
        _last_hwnd = hwnd
        return True
    return False


def current_layout(force: bool = False) -> int:
    """
    Кешована розкладка активного вікна (оновлюється не частіше ~4/с).
    force=True — читати свіже значення (для ручних дій, де 250мс лагу кешу
    дали б конвертацію в неправильному напрямку, якщо щойно перемкнули вручну).
    """
    global _cached_layout, _cached_layout_time
    now = time.time()
    if force or now - _cached_layout_time > 0.25:
        _cached_layout      = get_foreground_layout()
        _cached_layout_time = now
    return _cached_layout


# ═══════════════════════════════════════════════════════════════════════════════
# Корекція
# ═══════════════════════════════════════════════════════════════════════════════

def _sep_len(sep_vk: int) -> int:
    """Скільки видимих символів займає роздільник (0 — якщо його нема)."""
    return 1 if sep_vk else 0


def _send_sep(sep_vk: int, sep_shifted: bool = False):
    """
    Відправляє роздільник після скоригованого слова:
      • пробіл — Unicode;
      • Enter/Tab — реальна клавіша (інакше зʼїдає перенос);
      • пунктуація-термінатор — відтворюємо фізичну клавішу з Shift.
    """
    if sep_vk == VK_SPACE:
        send_unicode_string(" ")
    elif sep_vk in (VK_RETURN, VK_TAB):
        time.sleep(0.005)
        send_key(sep_vk)
    elif sep_vk:
        time.sleep(0.005)
        send_key_shifted(sep_vk, sep_shifted)


def _do_replace(strip_len: int, text: str, target_layout: int,
                sep_vk: int = 0, sep_shifted: bool = False):
    """
    Видаляє strip_len символів, друкує text, повертає роздільник, перемикає
    розкладку. Викликати лише під _acquire_correction().
    """
    send_backspaces(strip_len)
    time.sleep(0.02)
    send_unicode_string(text)
    _send_sep(sep_vk, sep_shifted)
    time.sleep(0.02)
    set_foreground_layout(target_layout)
    typed_scans.clear()


def _do_replace_batch(pending: list, cur_len: int, cur_converted: str,
                      cur_target: int, cur_sep_vk: int, cur_sep_shifted: bool = False):
    """
    Пакетне виправлення: pending (старі відкладені слова) + поточне слово.
    pending: список (orig_len, converted, target_layout, sep_vk, sep_shifted)
    від найстарішого до найновішого. Курсор стоїть після cur_sep.
    """
    total_bs = _sep_len(cur_sep_vk) + cur_len
    for orig_len, _, _, psep_vk, _ in pending:
        total_bs += orig_len + _sep_len(psep_vk)

    send_backspaces(total_bs)
    time.sleep(0.02)

    for _, pconverted, _, psep_vk, psep_shifted in pending:
        send_unicode_string(pconverted)
        _send_sep(psep_vk, psep_shifted)

    send_unicode_string(cur_converted)
    _send_sep(cur_sep_vk, cur_sep_shifted)

    time.sleep(0.02)
    set_foreground_layout(cur_target)
    typed_scans.clear()


def _acquire_correction() -> bool:
    """Беремо ексклюзив на корекцію + одразу ставимо _correcting (до будь-яких пауз)."""
    global _correcting
    if not _correct_lock.acquire(blocking=False):
        return False
    _correcting = True
    return True


def _release_correction():
    global _correcting
    _correcting = False
    _correct_lock.release()


# ── Буфер фрази (phrase_tokens) — підтримка з hook-потоку ────────────────────

def _phrase_add_letter(sc: int, shifted: bool):
    """Додати літеру до поточного слова фрази (створивши слово, якщо треба)."""
    if phrase_tokens and phrase_tokens[-1][0] == "w":
        phrase_tokens[-1][1].append((sc, shifted))
    else:
        phrase_tokens.append(["w", [(sc, shifted)]])
    if len(phrase_tokens) > 400:       # м'який запобіжник від безмежного росту
        del phrase_tokens[:200]


def _phrase_add_sep(vk: int):
    """Додати роздільник (пробіл/Enter/Tab). Провідні роздільники ігноруємо."""
    if phrase_tokens:
        phrase_tokens.append(["s", vk])


def _phrase_backspace():
    """Синхронізувати фразу з Backspace: прибрати останній символ."""
    if not phrase_tokens:
        return
    last = phrase_tokens[-1]
    if last[0] == "w":
        if last[1]:
            last[1].pop()
        if not last[1]:
            phrase_tokens.pop()
    else:                              # роздільник
        phrase_tokens.pop()


# ── Пам'ять: undo автокорекції ───────────────────────────────────────────────

def _clear_autocorrect_undo():
    global _autocorrect_undo_available, _last_autocorrect
    _autocorrect_undo_available = False
    _last_autocorrect = None


def _learn_from_phrase(phrase, layout: int, target: int):
    """
    Вивчити слова фрази, яку юзер сам перемкнув (авто не спрацювало). Вчимо
    лише ті слова, чиє джерельне читання НЕ є валідним словом поточної мови —
    тобто справді набрані не в тій розкладці (щоб не засмічувати пам'ять).
    """
    changed = False
    for tok in phrase:
        if tok[0] != "w" or len(tok[1]) < MIN_AUTOCORRECT_LEN:
            continue
        if layout == LANG_UKRAINIAN:
            src_l = scans_to_ukr(tok[1]).lower()
            tgt_l = scans_to_eng(tok[1]).lower()
            if dicts_loaded and src_l in DICT_UK:   # реальне укр. слово — не вчимо
                continue
        else:
            src_l = scans_to_eng(tok[1]).lower()
            tgt_l = scans_to_ukr(tok[1]).lower()
            if dicts_loaded and src_l in DICT_EN:   # реальне англ. слово — не вчимо
                continue
        if not tgt_l.isalpha():
            continue
        if learn_valid_word(tgt_l, target):
            changed = True
    if changed:
        save_learned()
        _dbg("learned valid: force_en=%d force_uk=%d" % (len(FORCE_EN), len(FORCE_UK)))


def _undo_autocorrect():
    """
    Відкат щойно зробленої автокорекції (юзеру вона не була потрібна).
    Повертає оригінал, перемикає розкладку назад і запам'ятовує слово як
    виняток, щоб надалі його не чіпати. Викликати під _acquire_correction().
    """
    info = _last_autocorrect
    if not info:
        return
    orig_scans, from_layout, to_layout, converted, sep_vk, sep_shifted = info
    orig_text = (scans_to_ukr(orig_scans) if from_layout == LANG_UKRAINIAN
                 else scans_to_eng(orig_scans))
    _dbg("undo autocorrect: %r -> back to %r (from=%04x)"
         % (converted, orig_text, from_layout))
    send_backspaces(len(converted) + _sep_len(sep_vk))
    time.sleep(0.02)
    send_unicode_string(orig_text)
    _send_sep(sep_vk, sep_shifted)
    time.sleep(0.02)
    set_foreground_layout(from_layout)
    typed_scans.clear()
    phrase_tokens.clear()
    if learning_enabled and learn_block_word(orig_text.lower(), from_layout):
        save_learned()
        _dbg("learned block: block_uk=%d block_en=%d" % (len(BLOCK_UK), len(BLOCK_EN)))
    _clear_autocorrect_undo()


def _do_replace_phrase(segments, strip_len: int, target_layout: int):
    """Стерти strip_len символів і передрукувати фразу посегментно, тоді перемкнути."""
    send_backspaces(strip_len)
    time.sleep(0.02)
    for kind, val in segments:
        if kind == "text":
            send_unicode_string(val)
        else:                          # роздільник
            _send_sep(val)
    time.sleep(0.02)
    set_foreground_layout(target_layout)


def manual_convert():
    """
    Ручне перемикання — подвійний Ctrl.
      • Є набрана фраза → конвертуємо ВЕСЬ текст (усі слова, що досі набрані
        не в тій розкладці) і за потреби вивчаємо їх у пам'ять.
      • Буфер порожній, але щойно спрацювала автокорекція → відкочуємо її
        (юзеру вона була не потрібна) і запам'ятовуємо слово як виняток.
    """
    if not _acquire_correction():
        return
    try:
        phrase = [tok for tok in phrase_tokens]
        has_word = any(t[0] == "w" and t[1] for t in phrase)
        if has_word:
            layout = current_layout(force=True)   # свіже: юзер міг щойно перемкнути вручну
            segments, strip_len, target = convert_phrase(phrase, layout)
            _dbg("manual_convert: tokens=%d layout=%04x strip=%d target=%s"
                 % (len(phrase), layout, strip_len, target))
            if not segments:
                return
            _do_replace_phrase(segments, strip_len, target)
            typed_scans.clear()
            phrase_tokens.clear()
            _clear_autocorrect_undo()
            if learning_enabled:
                _learn_from_phrase(phrase, layout, target)
            return
        # Буфер порожній — можливо, відкат щойно зробленої автокорекції.
        if _autocorrect_undo_available and _last_autocorrect:
            _undo_autocorrect()
    finally:
        _release_correction()


def auto_correct_word(scans, layout: int, sep_vk: int, seq0: int, sep_shifted: bool = False):
    """Автокорекція на межі слова. seq0 — лічильник вводу на момент межі слова.
    sep_vk — роздільник (пробіл/Enter/Tab або пунктуація-термінатор);
    sep_shifted — чи був Shift для пунктуації (для точного відтворення символу)."""
    global _pending_corrections, _last_autocorrect, _autocorrect_undo_available
    if not _acquire_correction():
        return
    try:
        converted, target_layout = autocorrect_target(scans, layout)
        if DEBUG:
            _dbg("autocorrect: ukr=%r eng=%r layout=%04x -> conv=%r target=%s"
                 % (scans_to_ukr(scans), scans_to_eng(scans), layout,
                    converted, target_layout))

        if not converted and not _pending_corrections:
            return  # Ні поточне, ні відкладені — нічого робити

        # Даємо роздільнику зʼявитись на екрані, тоді стираємо слово+роздільник.
        time.sleep(0.03)

        if _input_seq != seq0:
            # Користувач продовжив друк.
            if converted:
                # Поточне слово теж неправильне — зберігаємо у відкладені.
                _pending_corrections.append((len(scans), converted, target_layout, sep_vk, sep_shifted))
                _dbg("autocorrect: відкладено (pending=%d)" % len(_pending_corrections))
            else:
                # Поточне слово правильне — відкладені не можемо безпечно застосувати
                # (курсор зрушив), очищуємо.
                _pending_corrections.clear()
            return

        sep_len = _sep_len(sep_vk)
        pending = list(_pending_corrections)
        _pending_corrections.clear()

        # Автокорекція «осідає» — фраза для ручного перемикання більше не
        # відображає екран правильно; скидаємо її.
        phrase_tokens.clear()

        if not converted:
            # Поточне слово правильне, але є відкладені — виправляємо їх,
            # а поточне слово передруковуємо без змін.
            cur_text = scans_to_ukr(scans) if layout == LANG_UKRAINIAN else scans_to_eng(scans)
            total_bs = sep_len + len(scans)
            for orig_len, _, _, psep_vk, _ in pending:
                total_bs += orig_len + _sep_len(psep_vk)
            send_backspaces(total_bs)
            time.sleep(0.02)
            for _, pconverted, _, psep_vk, psep_shifted in pending:
                send_unicode_string(pconverted)
                _send_sep(psep_vk, psep_shifted)
            send_unicode_string(cur_text)
            _send_sep(sep_vk, sep_shifted)
            time.sleep(0.02)
            typed_scans.clear()
            _clear_autocorrect_undo()
        elif pending:
            _do_replace_batch(pending, len(scans), converted, target_layout, sep_vk, sep_shifted)
            _clear_autocorrect_undo()   # пакет складно відкочувати — не пропонуємо
        else:
            _do_replace(len(scans) + sep_len, converted, target_layout, sep_vk, sep_shifted)
            # Одиночна автокорекція — дозволяємо відкат подвійним Ctrl (пам'ять винятків).
            if learning_enabled:
                _last_autocorrect = (list(scans), layout, target_layout, converted, sep_vk, sep_shifted)
                _autocorrect_undo_available = True
            else:
                _clear_autocorrect_undo()
    finally:
        _release_correction()


# ═══════════════════════════════════════════════════════════════════════════════
# Mouse hook
# ═══════════════════════════════════════════════════════════════════════════════

@ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int,
                    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
def mouse_hook(nCode, wParam, lParam):
    """Миша: скидаємо буфер при кліку (користувач крутиться в тексту)."""
    if nCode < 0 or wParam not in _MOUSE_DOWN_MSGS:
        return user32.CallNextHookEx(mouse_hook_handle, nCode, wParam, lParam)

    ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
    flags = ms.flags

    if flags & LLMHF_INJECTED:
        return user32.CallNextHookEx(mouse_hook_handle, nCode, wParam, lParam)

    typed_scans.clear()
    phrase_tokens.clear()
    _pending_corrections.clear()

    return user32.CallNextHookEx(mouse_hook_handle, nCode, wParam, lParam)


# Keyboard hook
# ═══════════════════════════════════════════════════════════════════════════════

@ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int,
                    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
def keyboard_hook(nCode, wParam, lParam):
    global _input_seq

    if nCode < 0:
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    is_down = wParam in _DOWN_MSGS
    is_up   = wParam in _UP_MSGS
    if not (is_down or is_up):
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    kb    = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
    flags = kb.flags
    vk    = kb.vkCode
    sc    = kb.scanCode

    # ── Ігноруємо власні SendInput-події ────────────────────────────────────
    if flags & LLKHF_INJECTED:
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Відпускання клавіш ──────────────────────────────────────────────────
    if is_up:
        # Подвійний Ctrl спрацьовує на ВІДПУСКАННІ другого Ctrl: на момент друку
        # виправлення фізичний Ctrl уже відпущений, тож backspace та літери не
        # складаються в Ctrl-хоткеї (Ctrl+Backspace тощо).
        if vk in CTRL_VKS and ctrl_tap.on_trigger_up(time.time(), DOUBLE_TAP_WINDOW):
            if enabled:
                threading.Thread(target=manual_convert, daemon=True).start()
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Реальне (не ін'єктоване) натискання — рухаємо лічильник вводу ────────
    _input_seq += 1

    # ── Якщо корекція виконується — не заважаємо ────────────────────────────
    if _correcting:
        ctrl_tap.on_other_key()
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Зміна активного вікна — буфер більше не відповідає екрану, скидаємо ──
    # (перемкнулись у інше вікно й повернулись; курсор/контекст уже інші).
    # Alt+Tab ловиться нижче через модифікатор, але клік мишею по іншому вікну
    # інакше лишив би застарілий хвіст слова, до якого допишеться новий ввід.
    if _foreground_changed():
        typed_scans.clear()
        phrase_tokens.clear()
        _pending_corrections.clear()
        _clear_autocorrect_undo()
        caret_guard.on_focus_change()

    # ── Ctrl — початок (потенційно чистого) тапу для подвійного натискання ───
    if vk in CTRL_VKS:
        ctrl_tap.on_trigger_down()
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Будь-яка інша клавіша «бруднить» поточний Ctrl-тап і рве ланцюг ──────
    ctrl_tap.on_other_key()

    # Реальний друк (будь-яка не-Ctrl клавіша) знімає можливість відкотити
    # автокорекцію: подвійний Ctrl тепер стосуватиметься нового тексту, не її.
    _clear_autocorrect_undo()

    # ── Інші модифікатори (Shift/Alt/Win/Caps) — далі не обробляємо ──────────
    if vk in MODIFIER_VKS:
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Ctrl / Alt / Win утиснуто — не чіпаємо хоткеї ───────────────────────
    if any_modifier_down():
        typed_scans.clear()
        phrase_tokens.clear()
        _pending_corrections.clear()
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    if not enabled:
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Backspace — видаляємо останній scan-код з буфера ────────────────────
    if vk == VK_BACK:
        if typed_scans:
            typed_scans.pop()
        _phrase_backspace()
        _pending_corrections.clear()
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Навігація — скидаємо буфер (курсор перемістився) ────────────────────
    if vk in NAV_CLEAR_VKS:
        typed_scans.clear()
        phrase_tokens.clear()
        _pending_corrections.clear()
        caret_guard.on_nav()
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Межа слова (пробіл / Enter / Tab) ───────────────────────────────────
    if vk in WORD_BREAK_VKS:
        if auto_correct_enabled and typed_scans and not caret_guard.on_word_break():
            scans  = list(typed_scans)
            layout = current_layout()
            if DEBUG:
                _dbg("word-break: scans=%d layout=%04x auto=%s dicts=%s"
                     % (len(scans), layout, auto_correct_enabled, dicts_loaded))
            threading.Thread(
                target=auto_correct_word,
                args=(scans, layout, vk, _input_seq), daemon=True
            ).start()
        else:
            caret_guard.on_word_break()
        _phrase_add_sep(vk)            # роздільник лишається у фразі для ручного перемикання
        typed_scans.clear()
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Буферизуємо буквені клавіші за SCAN-КОДОМ (незалежно від розкладки) ──
    if sc in LETTER_SCANS:
        # запамʼятовуємо регістр (Shift XOR CapsLock), щоб зберегти його при корекції
        shifted = bool(user32.GetKeyState(VK_SHIFT) & 0x8000) ^ \
                  bool(user32.GetKeyState(VK_CAPITAL) & 0x0001)
        typed_scans.append((sc, shifted))
        if len(typed_scans) > 100:
            del typed_scans[:-50]
        _phrase_add_letter(sc, shifted)
    else:
        # Цифра / пунктуація поза літерними позиціями / OEM — межа слова.
        # Пунктуація-термінатор (напр. «так?») теж має запускати автокорекцію
        # на щойно набраному слові — інакше слово перед пунктуацією без пробілу
        # ніколи не перевіряється. Символ пунктуації вже зʼявиться на екрані,
        # тож корекція відтворить його назад (з Shift), як роздільник.
        term_shifted = bool(user32.GetKeyState(VK_SHIFT) & 0x8000)
        if (enabled and auto_correct_enabled and typed_scans
                and is_word_terminator(vk, term_shifted) and not caret_guard.on_word_break()):
            scans  = list(typed_scans)
            layout = current_layout()
            if DEBUG:
                _dbg("punct-break: vk=0x%02x shifted=%s scans=%d layout=%04x"
                     % (vk, term_shifted, len(scans), layout))
            threading.Thread(
                target=auto_correct_word,
                args=(scans, layout, vk, _input_seq, term_shifted), daemon=True
            ).start()
        else:
            caret_guard.on_word_break()
        # Пунктуація — межа для ручної фрази (як просив користувач).
        typed_scans.clear()
        phrase_tokens.clear()

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
    save_config()
    icon.update_menu()


def _toggle_auto(icon, item):
    global auto_correct_enabled
    auto_correct_enabled = not auto_correct_enabled
    save_config()
    icon.update_menu()


def _toggle_learning(icon, item):
    global learning_enabled
    learning_enabled = not learning_enabled
    if not learning_enabled:
        _clear_autocorrect_undo()
    save_config()
    icon.update_menu()


def _forget_learned(icon, item):
    total = len(FORCE_EN) + len(FORCE_UK) + len(BLOCK_UK) + len(BLOCK_EN)
    if total == 0:
        return
    # MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2 (за замовч. — «Ні»)
    resp = user32.MessageBoxW(
        None,
        "Забути всі вивчені слова (%d)?\nЦю дію не можна скасувати." % total,
        "Qwasda — забути вивчене", 0x04 | 0x20 | 0x100,
    )
    if resp != 6:            # IDYES
        return
    forget_learned()
    icon.notify("Пам'ять очищено — вивчені слова забуто.", "Qwasda")
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
    # Розбудити блокуючий GetMessageW у головному потоці
    if main_thread_id:
        user32.PostThreadMessageW(main_thread_id, WM_QUIT, None, None)
    icon.stop()


def _make_menu():
    return pystray.Menu(
        pystray.MenuItem(
            lambda i: "✅ Qwasda увімкнено" if enabled else "⏸ Qwasda вимкнено",
            _toggle_enabled, checked=lambda i: enabled,
        ),
        pystray.MenuItem(
            lambda i: ("🔄 Автокорекція: ON" if auto_correct_enabled else "🔄 Автокорекція: OFF")
                      + ("" if dicts_loaded else " (словники не завантажено)"),
            _toggle_auto, checked=lambda i: auto_correct_enabled,
            enabled=lambda i: dicts_loaded,
        ),
        pystray.MenuItem(
            lambda i: "🧠 Навчання: ON" if learning_enabled else "🧠 Навчання: OFF",
            _toggle_learning, checked=lambda i: learning_enabled,
        ),
        pystray.MenuItem(
            lambda i: "🧹 Забути вивчене (%d)"
                      % (len(FORCE_EN) + len(FORCE_UK) + len(BLOCK_UK) + len(BLOCK_EN)),
            _forget_learned,
        ),
        pystray.MenuItem(
            lambda i: "✅ Автозапуск" if is_in_startup() else "❌ Автозапуск",
            _toggle_startup, checked=lambda i: is_in_startup(),
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Вихід", _on_exit),
    )


def _run_tray():
    global tray_icon
    tray_icon = pystray.Icon(
        "Qwasda", _make_icon_image(),
        "Qwasda v%s — перемикач розкладки" % __version__, _make_menu(),
    )
    tray_icon.notify("Qwasda v%s запущено! Подвійний Ctrl — перемкнути слово."
                     % __version__, "Qwasda")
    tray_icon.run()


# ═══════════════════════════════════════════════════════════════════════════════
# Захист від другого екземпляра
# ═══════════════════════════════════════════════════════════════════════════════

ERROR_ALREADY_EXISTS = 183
_instance_mutex = None   # тримаємо хендл живим увесь час роботи процесу


def _acquire_single_instance() -> bool:
    """
    True, якщо ми єдиний екземпляр. Два LL-хуки одночасно дали б подвійні
    backspace/корекції, тож при повторному запуску просто виходимо.
    """
    global _instance_mutex
    _instance_mutex = kernel32.CreateMutexW(None, False, "Qwasda_SingleInstance_Mutex")
    return kernel32.GetLastError() != ERROR_ALREADY_EXISTS


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global running, hook_handle, mouse_hook_handle, main_thread_id

    if not _acquire_single_instance():
        user32.MessageBoxW(None, "Qwasda вже запущено.", "Qwasda", 0x40)
        sys.exit(0)

    load_config()
    load_learned()

    main_thread_id = kernel32.GetCurrentThreadId()
    _dbg("=== Qwasda v%s start === frozen=%s debug_log=%s"
         % (__version__, getattr(sys, "frozen", False), _log_path))

    # Словники — у фоні, щоб не блокувати старт трею
    threading.Thread(target=load_dicts, daemon=True).start()

    hinst = ctypes.pythonapi._handle
    hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_hook, hinst, 0)
    _dbg("hook installed: handle=%s" % hook_handle)

    if not hook_handle:
        user32.MessageBoxW(None, "Не вдалося встановити клавіатурний хук.",
                           "Qwasda", 0x10)
        sys.exit(1)

    mouse_hook_handle = user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_hook, hinst, 0)
    if mouse_hook_handle:
        _dbg("mouse hook installed: handle=%s" % mouse_hook_handle)
    else:
        _dbg("WARNING: mouse hook failed (non-fatal)")

    threading.Thread(target=_run_tray, daemon=True).start()

    def _cleanup_hooks():
        global hook_handle, mouse_hook_handle
        if hook_handle:
            user32.UnhookWindowsHookEx(hook_handle)
            hook_handle = None
        if mouse_hook_handle:
            user32.UnhookWindowsHookEx(mouse_hook_handle)
            mouse_hook_handle = None

    atexit.register(_cleanup_hooks)

    def _sig(sig, frame):
        global running
        running = False
        _cleanup_hooks()
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
