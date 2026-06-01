"""Мінімальний тест — запис у файл"""
import ctypes
import ctypes.wintypes

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_long

LOG = open(r'C:\Users\exesh\Qwasda\minimal.log', 'w', encoding='utf-8')

def log(msg):
    LOG.write(msg + '\n')
    LOG.flush()

log('Starting...')

hook_handle = [None]  # Список для можливості зміни в замиканні

@ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
def cb(nCode, wParam, lParam):
    log(f'CALLBACK! nCode={nCode} wParam={wParam}')
    if nCode >= 0 and wParam == WM_KEYDOWN:
        log(f'KEYDOWN detected!')
    return user32.CallNextHookEx(hook_handle[0], nCode, wParam, lParam)

log(f'Callback: {cb}')

hinst = ctypes.pythonapi._handle
log(f'HINSTANCE: {hex(hinst)}')

hook_handle[0] = user32.SetWindowsHookExW(WH_KEYBOARD_LL, cb, ctypes.c_void_p(hinst), 0)
err = kernel32.GetLastError()
log(f'Hook: {hook_handle[0]}, Error: {err}')

if hook_handle[0]:
    log('Hook OK! Entering message loop...')
    msg = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
else:
    log('FAILED!')

LOG.close()
