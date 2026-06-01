"""Тестовий скрипт для перевірки хука"""
import ctypes
import ctypes.wintypes
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('vkCode', ctypes.wintypes.DWORD),
        ('scanCode', ctypes.wintypes.DWORD),
        ('flags', ctypes.wintypes.DWORD),
        ('time', ctypes.wintypes.DWORD),
        ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong)),
    ]

user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
user32.CallNextHookEx.restype = ctypes.c_long
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]

# Логуємо у файл
LOG_PATH = r'C:\Users\exesh\Qwasda\hook_test.log'

def log(msg):
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(f'{time.strftime("%H:%M:%S")} {msg}\n')

log('=== Starting hook test ===')

# Пробуємо CFUNCTYPE замість WINFUNCTYPE
HOOKPROC = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)

@HOOKPROC
def keyboard_hook(nCode, wParam, lParam):
    if nCode >= 0 and wParam == WM_KEYDOWN:
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents

        # Отримуємо символ
        state = ctypes.create_string_buffer(256)
        user32.GetKeyboardState(ctypes.byref(state))
        hwnd = user32.GetForegroundWindow()
        thread_id = user32.GetWindowThreadProcessId(hwnd, None)
        hkl = user32.GetKeyboardLayout(thread_id)
        layout = hkl & 0xFFFF

        buf = ctypes.create_unicode_buffer(8)
        result = user32.ToUnicodeEx(kb.vkCode, kb.scanCode, state, buf, 8, 0, hkl)
        char = buf.value if result > 0 else None

        log(f'vk={kb.vkCode:#x} sc={kb.scanCode} layout={layout:#x} char={repr(char)}')

    return user32.CallNextHookEx(None, nCode, wParam, lParam)

log('Installing hook...')
hinst = ctypes.pythonapi._handle
log(f'HINSTANCE: {hex(hinst)}')

hinst_c = ctypes.c_void_p(hinst)
log(f'HINSTANCE as c_void_p: {hinst_c}')
hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_hook, hinst_c, 0)
err = kernel32.GetLastError()
log(f'Hook result: {hook}, Error: {err}')

if hook:
    log('Hook installed! Press keys now...')
    log('(Press Ctrl+C in terminal to stop)')
    msg = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
else:
    log('FAILED to install hook!')
