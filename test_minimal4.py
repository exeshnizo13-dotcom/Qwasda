"""Мінімальний тест — PeekMessage замість GetMessage"""
import ctypes
import ctypes.wintypes
import time

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
PM_REMOVE = 0x0001

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_long
user32.PeekMessageW.argtypes = [ctypes.POINTER(ctypes.wintypes.MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
user32.PeekMessageW.restype = ctypes.c_bool

_g_hook = None

LOG = open(r'C:\Users\exesh\Qwasda\minimal4.log', 'w', encoding='utf-8')

def log(msg):
    LOG.write(f'{time.strftime("%H:%M:%S")} {msg}\n')
    LOG.flush()

@ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
def _keyboard_proc(nCode, wParam, lParam):
    log(f'CALLBACK! nCode={nCode} wParam={wParam}')
    if nCode >= 0 and wParam == WM_KEYDOWN:
        # Читаємо KBDLLHOOKSTRUCT
        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ('vkCode', ctypes.wintypes.DWORD),
                ('scanCode', ctypes.wintypes.DWORD),
                ('flags', ctypes.wintypes.DWORD),
                ('time', ctypes.wintypes.DWORD),
                ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
            ]
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        log(f'KEYDOWN vk={kb.vkCode:#x} sc={kb.scanCode}')
    return user32.CallNextHookEx(_g_hook, nCode, wParam, lParam)

log('Starting...')

hinst = ctypes.pythonapi._handle
log(f'HINSTANCE: {hex(hinst)}')

_g_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _keyboard_proc, ctypes.c_void_p(hinst), 0)
err = kernel32.GetLastError()
log(f'Hook: {_g_hook}, Error: {err}')

if _g_hook:
    log('Hook OK! Entering PeekMessage loop...')
    msg = ctypes.wintypes.MSG()
    count = 0
    while count < 500:  # Максимум 500 ітерацій (~5 секунд)
        result = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE)
        if result:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)
        count += 1
    log(f'Loop ended after {count} iterations')
else:
    log('FAILED!')

LOG.close()
