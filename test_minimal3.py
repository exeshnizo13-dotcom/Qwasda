"""Мінімальний тест — callback як глобальна змінна"""
import ctypes
import ctypes.wintypes
import sys

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_long

# Глобальні змінні — щоб GC не знищив
_g_hook = None
_g_cb = None

LOG = open(r'C:\Users\exesh\Qwasda\minimal3.log', 'w', encoding='utf-8')

def log(msg):
    LOG.write(msg + '\n')
    LOG.flush()

@ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
def _keyboard_proc(nCode, wParam, lParam):
    log(f'CALLBACK! nCode={nCode} wParam={wParam}')
    if nCode >= 0 and wParam == WM_KEYDOWN:
        kb = ctypes.cast(lParam, ctypes.POINTER(ctypes.wintypes.DWORD)).contents
        log(f'KEYDOWN vk={kb:#x}')
    return user32.CallNextHookEx(_g_hook, nCode, wParam, lParam)

log('Starting...')
log(f'Callback: {_keyboard_proc}')

hinst = ctypes.pythonapi._handle
log(f'HINSTANCE: {hex(hinst)}')

_g_cb = _keyboard_proc  # Зберігаємо посилання!
_g_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _g_cb, ctypes.c_void_p(hinst), 0)
err = kernel32.GetLastError()
log(f'Hook: {_g_hook}, Error: {err}')

if _g_hook:
    log('Hook OK! Press keys now...')
    msg = ctypes.wintypes.MSG()
    # Таймаут для GetMessage — щоб не блокувати назавжди
    while True:
        result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if result == 0 or result == -1:
            break
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
else:
    log('FAILED!')

LOG.close()
