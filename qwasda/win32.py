"""
Win32 constants, structures, and low-level helpers for Qwasda.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from ctypes import wintypes

# =============================================================================
# Virtual Key Codes
# =============================================================================
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_CAPITAL = 0x14
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21  # Page Up
VK_NEXT = 0x22  # Page Down
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_DELETE = 0x2E
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5

# Language IDs
LANG_ENGLISH = 0x0409
LANG_UKRAINIAN = 0x0422

# Hook constants
WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
WM_HOTKEY = 0x0312
WM_INPUTLANGCHANGEREQUEST = 0x0050

WM_LBUTTONDOWN = 0x0201
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONDOWN = 0x0207
WM_XBUTTONDOWN = 0x020B

DOWN_MSGS = frozenset({WM_KEYDOWN, WM_SYSKEYDOWN})
UP_MSGS = frozenset({WM_KEYUP, WM_SYSKEYUP})
MOUSE_DOWN_MSGS = frozenset({WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_XBUTTONDOWN})

# LLKHF_INJECTED flag (bit 4) - event was injected via SendInput
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01

KLF_ACTIVATE = 0x00000001
ERROR_ALREADY_EXISTS = 183

# Modifier key sets
CTRL_VKS = frozenset({VK_CONTROL, VK_LCONTROL, VK_RCONTROL})
MODIFIER_VKS = frozenset(
    {
        VK_SHIFT,
        VK_CONTROL,
        VK_MENU,
        VK_CAPITAL,
        VK_LSHIFT,
        VK_RSHIFT,
        VK_LCONTROL,
        VK_RCONTROL,
        VK_LMENU,
        VK_RMENU,
        VK_LWIN,
        VK_RWIN,
    }
)

# Word break keys (Space, Enter, Tab)
WORD_BREAK_VKS = frozenset({VK_SPACE, VK_RETURN, VK_TAB})

# Navigation keys that clear buffers
NAV_CLEAR_VKS = frozenset(
    {
        VK_LEFT,
        VK_UP,
        VK_RIGHT,
        VK_DOWN,
        VK_ESCAPE,
        VK_HOME,
        VK_END,
        VK_PRIOR,
        VK_NEXT,
        VK_DELETE,
    }
)

# OEM punctuation keys that act as word terminators
OEM_PUNCT_VKS = frozenset(
    {
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
    }
)

# =============================================================================
# Win32 Structures
# =============================================================================


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _INPUT_UNION)]


# Verify INPUT size (40 bytes on x64, 28 on x86)
if ctypes.sizeof(INPUT) not in (28, 40):
    raise RuntimeError(f"INPUT struct size mismatch: {ctypes.sizeof(INPUT)} bytes")


# =============================================================================
# Win32 Function Bindings
# =============================================================================

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# LRESULT is a signed pointer-sized value (LONG_PTR), not C ``long``.
# Using c_long here truncates hook return values in 64-bit builds and can
# corrupt the low-level hook chain with a native access violation.
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]

user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]

user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetKeyboardLayout.restype = wintypes.HKL
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]

user32.LoadKeyboardLayoutW.restype = wintypes.HKL
user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]

user32.PostMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

user32.PostThreadMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [
    wintypes.DWORD,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]

user32.SendInput.restype = wintypes.UINT
user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]

user32.GetKeyState.restype = ctypes.c_short
user32.GetKeyState.argtypes = [ctypes.c_int]

user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]

user32.AttachThreadInput.restype = wintypes.BOOL
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]

user32.ActivateKeyboardLayout.restype = wintypes.HKL
user32.ActivateKeyboardLayout.argtypes = [wintypes.HKL, wintypes.UINT]

user32.GetClassNameW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

user32.GetGUIThreadInfo.restype = wintypes.BOOL
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.c_void_p]

user32.GetKeyboardLayoutList.restype = ctypes.c_int
user32.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.c_void_p]

kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.ReleaseMutex.restype = wintypes.BOOL
kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.GetLastError.restype = wintypes.DWORD

user32.RegisterHotKey.restype = wintypes.BOOL
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]


# =============================================================================
# Low-level Helpers
# =============================================================================

_seen_hkl: dict[int, int] = {}  # lang_id -> last used full HKL
_hkl_fallback: dict[int, int] = {}  # lang_id -> fallback HKL from installed list


def pick_installed_hkl(lang_id: int) -> int:
    """
    Get HKL of already-installed layout for language WITHOUT calling
    LoadKeyboardLayoutW (which would add a duplicate layout).
    Prefers non-default sublayout (e.g., "Ukrainian (Enhanced)") over default.
    """
    n = user32.GetKeyboardLayoutList(0, None)
    best = 0
    if n > 0:
        arr = (ctypes.c_void_p * n)()
        user32.GetKeyboardLayoutList(n, arr)
        for h in arr:
            if h and (h & 0xFFFF) == lang_id:
                if best == 0:
                    best = h
                if ((h >> 16) & 0xFFFF) != lang_id:  # non-default sublayout
                    return int(h)
    if best:
        return int(best)
    # Fallback: load default layout
    return int(user32.LoadKeyboardLayoutW(f"{lang_id:08x}", KLF_ACTIVATE))


def hkl_for(lang_id: int) -> int:
    """Get full HKL for layout switching, preferring user's active one."""
    h = _seen_hkl.get(lang_id)
    if h:
        return h
    h = _hkl_fallback.get(lang_id)
    if h is None:
        h = pick_installed_hkl(lang_id)
        _hkl_fallback[lang_id] = h
    return h


def fg_class(hwnd: wintypes.HWND) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def get_foreground_layout() -> int:
    """
    Get keyboard layout of the window that actually receives keystrokes.
    Uses GetGUIThreadInfo to find focused control's thread.
    """
    hwnd = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(hwnd, None)

    target_tid = fg_tid
    gui = GUITHREADINFO()
    gui.cbSize = ctypes.sizeof(GUITHREADINFO)
    if user32.GetGUIThreadInfo(fg_tid, ctypes.byref(gui)) and gui.hwndFocus:
        ft = user32.GetWindowThreadProcessId(gui.hwndFocus, None)
        if ft:
            target_tid = ft

    hkl = user32.GetKeyboardLayout(target_tid)
    lang = hkl & 0xFFFF
    if hkl:
        _seen_hkl[lang] = hkl
    return int(lang)


def set_foreground_layout(lang_id: int) -> None:
    """
    Switch active window's keyboard layout using two methods:
    1. WM_INPUTLANGCHANGEREQUEST (classic Win32)
    2. AttachThreadInput + ActivateKeyboardLayout (Chrome, Electron, UWP)
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return

    hkl = hkl_for(lang_id)

    # Method 1
    user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, hkl)

    # Method 2
    fg_tid = user32.GetWindowThreadProcessId(hwnd, None)
    cur_tid = kernel32.GetCurrentThreadId()
    attached = False
    if fg_tid and fg_tid != cur_tid:
        attached = user32.AttachThreadInput(cur_tid, fg_tid, True)
    try:
        user32.ActivateKeyboardLayout(hkl, KLF_ACTIVATE)
    finally:
        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)


def any_modifier_down() -> bool:
    """Check if Ctrl, Alt, or Win is currently pressed."""
    for vk in (VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN):
        if user32.GetKeyState(vk) & 0x8000:
            return True
    return False


def make_key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT()
    inp.type = 1  # INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    inp.union.ki.wScan = scan
    inp.union.ki.dwFlags = flags
    inp.union.ki.time = 0
    inp.union.ki.dwExtraInfo = None
    return inp


def send_backspaces(n: int) -> None:
    if n <= 0:
        return
    evts = []
    for _ in range(n):
        evts.append(make_key_input(vk=VK_BACK, flags=0))
        evts.append(make_key_input(vk=VK_BACK, flags=2))  # KEYUP
    arr = (INPUT * len(evts))(*evts)
    user32.SendInput(len(evts), ctypes.byref(arr), ctypes.sizeof(INPUT))


def send_key(vk: int) -> None:
    arr = (INPUT * 2)(make_key_input(vk=vk, flags=0), make_key_input(vk=vk, flags=2))
    user32.SendInput(2, ctypes.byref(arr), ctypes.sizeof(INPUT))


def send_key_shifted(vk: int, shifted: bool) -> None:
    """Send key with optional Shift (for punctuation)."""
    evts = []
    if shifted:
        evts.append(make_key_input(vk=VK_SHIFT, flags=0))
    evts.append(make_key_input(vk=vk, flags=0))
    evts.append(make_key_input(vk=vk, flags=2))
    if shifted:
        evts.append(make_key_input(vk=VK_SHIFT, flags=2))
    arr = (INPUT * len(evts))(*evts)
    user32.SendInput(len(evts), ctypes.byref(arr), ctypes.sizeof(INPUT))


def send_unicode_string(text: str) -> None:
    if not text:
        return
    evts = []
    for ch in text:
        code = ord(ch)
        if code > 0xFFFF:
            continue
        evts.append(make_key_input(scan=code, flags=0x0004))  # UNICODE down
        evts.append(make_key_input(scan=code, flags=0x0004 | 0x0002))  # UNICODE up
    arr = (INPUT * len(evts))(*evts)
    user32.SendInput(len(evts), ctypes.byref(arr), ctypes.sizeof(INPUT))
