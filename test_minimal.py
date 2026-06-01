"""Мінімальний тест low-level keyboard hook"""
import ctypes
import ctypes.wintypes

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Встановлюємо типи
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int,
    ctypes.c_void_p,  # HOOKPROC
    ctypes.c_void_p,  # HINSTANCE
    ctypes.c_ulong    # DWORD
]
user32.SetWindowsHookExW.restype = ctypes.c_void_p

user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p,  # HHOOK
    ctypes.c_int,     # nCode
    ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM
]
user32.CallNextHookEx.restype = ctypes.c_long

user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = ctypes.c_bool

# Створюємо callback
def make_callback():
    @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
    def cb(nCode, wParam, lParam):
        if nCode >= 0 and wParam == WM_KEYDOWN:
            print(f"KEYDOWN! nCode={nCode} wParam={wParam}")
        return user32.CallNextHookEx(hook_handle, nCode, wParam, lParam)
    return cb

cb = make_callback()
print(f"Callback: {cb}")
print(f"Callback addr: {ctypes.cast(cb, ctypes.c_void_p).value:#x}")

# Отримуємо HINSTANCE — для WH_KEYBOARD_LL потрібен pythonapi._handle!
hinst = ctypes.pythonapi._handle
print(f"HINSTANCE (pythonapi): {hinst:#x}")
hinst_c = ctypes.c_void_p(hinst)
print(f"HINSTANCE (c_void_p): {hinst_c}")

# Встановлюємо хук
hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, cb, hinst_c, 0)
hook = hook_handle
err = kernel32.GetLastError()
print(f"Hook: {hook}, Error: {err}")

if hook:
    print("Hook installed! Press keys...")
    msg = ctypes.wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
else:
    print("FAILED!")
