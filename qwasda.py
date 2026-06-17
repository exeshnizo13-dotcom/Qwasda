"""
Qwasda — перемикач розкладки клавіатури (EN ↔ UK).

Ручне перемикання:
  Подвійне натискання Shift (два Shift поспіль без іншої клавіші між ними) —
  поточне слово конвертується між розкладками.

Автокорекція (за словниками uk/en):
  На межі слова (пробіл/Enter/Tab) програма перевіряє набране слово:
    • якщо слово є валідним у поточній мові — не чіпає;
    • якщо слово невалідне, але його конвертація в іншу розкладку дає
      валідне слово з протилежного словника — виправляє розкладку.
  Словники лежать у data/words_en.txt.gz та data/words_uk.txt.gz.

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
import gzip
import time
from array import array
import ctypes
import ctypes.wintypes
import atexit
import signal
import threading

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

KLF_ACTIVATE   = 0x00000001
LANG_ENGLISH   = 0x0409
LANG_UKRAINIAN = 0x0422

SHIFT_VKS = frozenset({VK_SHIFT, VK_LSHIFT, VK_RSHIFT})

MODIFIER_VKS = frozenset({
    VK_SHIFT, VK_CONTROL, VK_MENU, VK_CAPITAL,
    VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL,
    VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN,
})

WORD_BREAK_VKS = frozenset({VK_SPACE, VK_RETURN, VK_TAB})
NAV_CLEAR_VKS  = frozenset({VK_LEFT, VK_UP, VK_RIGHT, VK_DOWN, VK_ESCAPE})

MIN_AUTOCORRECT_LEN = 2   # не виправляти надто короткі слова
MIN_EN_TO_UK        = 3   # напрямок EN→UK суворіший: укр. словник величезний (3.8M),
                          # тож короткі латинські токени легко випадково «стають» укр.

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


assert ctypes.sizeof(INPUT) == 40 or ctypes.sizeof(ctypes.c_void_p) == 4, \
    "INPUT struct size mismatch — SendInput не працюватиме"


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
        assert len(data) < 2 ** 32, "словник завеликий для 32-бітних офсетів"
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
    if not dicts_loaded or len(scans) < MIN_AUTOCORRECT_LEN:
        return None, None
    if layout not in (LANG_UKRAINIAN, LANG_ENGLISH):
        return None, None
    ukr = scans_to_ukr(scans)            # як слово виглядає в укр. розкладці (з регістром)
    eng = scans_to_eng(scans)            # ... і в англійській
    ukr_l, eng_l = ukr.lower(), eng.lower()

    if layout == LANG_UKRAINIAN:
        # На екрані зараз ukr. Якщо це валідне укр. слово — не чіпаємо.
        if ukr_l in DICT_UK:
            return None, None
        if eng_l in DICT_EN:             # але в англ. читанні — валідне слово
            return eng, LANG_ENGLISH
    else:
        # На екрані зараз eng (латинська розкладка).
        if eng_l in DICT_EN:
            return None, None
        # EN→UK: суворіший поріг довжини проти випадкових збігів.
        if len(scans) >= MIN_EN_TO_UK and ukr_l in DICT_UK:
            return ukr, LANG_UKRAINIAN
    return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# Глобальний стан
# ═══════════════════════════════════════════════════════════════════════════════

running              = True
enabled              = True
auto_correct_enabled = True
tray_icon            = None

typed_scans       = []     # Буфер поточного слова — scan-коди клавіш (не символи)
last_shift_time   = 0.0    # Для подвійного Shift
shift_interrupted = True   # True = між двома Shift була інша клавіша
shift_down        = False  # Shift фізично утиснутий (фільтр auto-repeat)
hook_handle       = None
main_thread_id    = 0

# Кеш розкладки (LL-hook має повертатися швидко)
_cached_layout      = LANG_ENGLISH
_cached_layout_time = 0.0

# Зараз виконуємо корекцію — не обробляємо hook
_correcting   = False
_correct_lock = threading.Lock()   # серіалізує заміни (одна за раз)
_input_seq    = 0                  # лічильник реальних натискань (для скасування гонки)
_pending_corrections = []          # (orig_len, converted, target_layout, sep_vk) — відкладені виправлення


def current_layout() -> int:
    """Кешована розкладка активного вікна (оновлюється не частіше ~4/с)."""
    global _cached_layout, _cached_layout_time
    now = time.time()
    if now - _cached_layout_time > 0.25:
        _cached_layout      = get_foreground_layout()
        _cached_layout_time = now
    return _cached_layout


# ═══════════════════════════════════════════════════════════════════════════════
# Корекція
# ═══════════════════════════════════════════════════════════════════════════════

def _do_replace(strip_len: int, text: str, target_layout: int, sep_vk: int = 0):
    """
    Видаляє strip_len символів, друкує text, повертає роздільник, перемикає
    розкладку. Викликати лише під _acquire_correction().
    """
    send_backspaces(strip_len)
    time.sleep(0.02)
    send_unicode_string(text)
    # роздільник: пробіл — Unicode, Enter/Tab — реальна клавіша (інакше зʼїдає перенос)
    if sep_vk == VK_SPACE:
        send_unicode_string(" ")
    elif sep_vk in (VK_RETURN, VK_TAB):
        time.sleep(0.005)
        send_key(sep_vk)
    time.sleep(0.02)
    set_foreground_layout(target_layout)
    typed_scans.clear()


def _send_sep(sep_vk: int):
    """Відправляє роздільник після скоригованого слова."""
    if sep_vk == VK_SPACE:
        send_unicode_string(" ")
    elif sep_vk in (VK_RETURN, VK_TAB):
        time.sleep(0.005)
        send_key(sep_vk)


def _do_replace_batch(pending: list, cur_len: int, cur_converted: str,
                      cur_target: int, cur_sep_vk: int):
    """
    Пакетне виправлення: pending (старі відкладені слова) + поточне слово.
    pending: список (orig_len, converted, target_layout, sep_vk) від найстарішого до найновішого.
    Курсор стоїть після cur_sep (поточний роздільник вже на екрані).
    """
    cur_sep_len = 1 if cur_sep_vk in WORD_BREAK_VKS else 0
    total_bs = cur_sep_len + cur_len
    for orig_len, _, _, psep_vk in pending:
        total_bs += orig_len + (1 if psep_vk in WORD_BREAK_VKS else 0)

    send_backspaces(total_bs)
    time.sleep(0.02)

    for _, pconverted, _, psep_vk in pending:
        send_unicode_string(pconverted)
        _send_sep(psep_vk)

    send_unicode_string(cur_converted)
    _send_sep(cur_sep_vk)

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


def manual_convert():
    """Ручне перемикання — подвійний Shift. Конвертує поточне слово."""
    if not _acquire_correction():
        return
    try:
        scans = list(typed_scans)
        if not scans:
            return
        layout = current_layout()
        converted, target_layout = manual_target(scans, layout)
        _dbg("manual_convert: scans=%d layout=%04x -> conv=%r target=%s"
             % (len(scans), layout, converted, target_layout))
        if not converted:
            return
        _do_replace(len(scans), converted, target_layout)
    finally:
        _release_correction()


def auto_correct_word(scans, layout: int, sep_vk: int, seq0: int):
    """Автокорекція на межі слова. seq0 — лічильник вводу на момент межі слова."""
    global _pending_corrections
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
                _pending_corrections.append((len(scans), converted, target_layout, sep_vk))
                _dbg("autocorrect: відкладено (pending=%d)" % len(_pending_corrections))
            else:
                # Поточне слово правильне — відкладені не можемо безпечно застосувати
                # (курсор зрушив), очищуємо.
                _pending_corrections.clear()
            return

        sep_len = 1 if sep_vk in WORD_BREAK_VKS else 0
        pending = list(_pending_corrections)
        _pending_corrections.clear()

        if not converted:
            # Поточне слово правильне, але є відкладені — виправляємо їх,
            # а поточне слово передруковуємо без змін.
            cur_text = scans_to_ukr(scans) if layout == LANG_UKRAINIAN else scans_to_eng(scans)
            total_bs = sep_len + len(scans)
            for orig_len, _, _, psep_vk in pending:
                total_bs += orig_len + (1 if psep_vk in WORD_BREAK_VKS else 0)
            send_backspaces(total_bs)
            time.sleep(0.02)
            for _, pconverted, _, psep_vk in pending:
                send_unicode_string(pconverted)
                _send_sep(psep_vk)
            send_unicode_string(cur_text)
            _send_sep(sep_vk)
            time.sleep(0.02)
            typed_scans.clear()
        elif pending:
            _do_replace_batch(pending, len(scans), converted, target_layout, sep_vk)
        else:
            _do_replace(len(scans) + sep_len, converted, target_layout, sep_vk)
    finally:
        _release_correction()


# ═══════════════════════════════════════════════════════════════════════════════
# Keyboard hook
# ═══════════════════════════════════════════════════════════════════════════════

@ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int,
                    ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
def keyboard_hook(nCode, wParam, lParam):
    global last_shift_time, shift_interrupted, shift_down, _input_seq

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

    # ── Відпускання Shift — лише знімаємо прапорець фізичного натискання ─────
    if is_up:
        if vk in SHIFT_VKS:
            shift_down = False
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Реальне (не ін'єктоване) натискання — рухаємо лічильник вводу ────────
    _input_seq += 1

    # ── Якщо корекція виконується — не заважаємо ────────────────────────────
    if _correcting:
        shift_interrupted = True
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Shift — обробка подвійного натискання ───────────────────────────────
    if vk in SHIFT_VKS:
        if not shift_down:                       # справжнє натискання (не auto-repeat)
            shift_down = True
            now = time.time()
            if not shift_interrupted and now - last_shift_time < 0.4:
                last_shift_time   = 0.0
                shift_interrupted = True
                if enabled:
                    threading.Thread(target=manual_convert, daemon=True).start()
            else:
                last_shift_time   = now
                shift_interrupted = False        # чекаємо другий Shift
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Інші модифікатори — перериваємо послідовність Shift ──────────────────
    if vk in MODIFIER_VKS:
        shift_interrupted = True
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Будь-яка не-Shift клавіша перериває послідовність подвійного Shift ───
    shift_interrupted = True

    # ── Ctrl / Alt / Win утиснуто — не чіпаємо хоткеї ───────────────────────
    if any_modifier_down():
        typed_scans.clear()
        _pending_corrections.clear()
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    if not enabled:
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Backspace — видаляємо останній scan-код з буфера ────────────────────
    if vk == VK_BACK:
        if typed_scans:
            typed_scans.pop()
        _pending_corrections.clear()
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Навігація — скидаємо буфер (курсор перемістився) ────────────────────
    if vk in NAV_CLEAR_VKS:
        typed_scans.clear()
        _pending_corrections.clear()
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)

    # ── Межа слова (пробіл / Enter / Tab) ───────────────────────────────────
    if vk in WORD_BREAK_VKS:
        if auto_correct_enabled and typed_scans:
            scans  = list(typed_scans)
            layout = current_layout()
            if DEBUG:
                _dbg("word-break: scans=%d layout=%04x auto=%s dicts=%s"
                     % (len(scans), layout, auto_correct_enabled, dicts_loaded))
            threading.Thread(
                target=auto_correct_word,
                args=(scans, layout, vk, _input_seq), daemon=True
            ).start()
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
    else:
        # Цифра / пунктуація поза літерними позиціями / OEM — межа слова
        typed_scans.clear()

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
        "Qwasda — перемикач розкладки", _make_menu(),
    )
    tray_icon.notify("Qwasda запущено! Подвійний Shift — перемкнути слово.", "Qwasda")
    tray_icon.run()


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    global running, hook_handle, main_thread_id

    main_thread_id = kernel32.GetCurrentThreadId()
    _dbg("=== Qwasda start === frozen=%s debug_log=%s"
         % (getattr(sys, "frozen", False), _log_path))

    # Словники — у фоні, щоб не блокувати старт трею
    threading.Thread(target=load_dicts, daemon=True).start()

    hinst       = ctypes.pythonapi._handle
    hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_hook, hinst, 0)
    _dbg("hook installed: handle=%s" % hook_handle)

    if not hook_handle:
        user32.MessageBoxW(None, "Не вдалося встановити клавіатурний хук.",
                           "Qwasda", 0x10)
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
