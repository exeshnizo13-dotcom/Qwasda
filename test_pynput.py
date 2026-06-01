"""Тест pynput — чи працює хук через pythonw"""
import sys
import os

# Приховуємо консоль
try:
    import ctypes
    if sys.stdout is not None and sys.stdout.isatty():
        ctypes.windll.user32.ShowWindow(
            ctypes.windll.kernel32.GetConsoleWindow(), 0
        )
except Exception:
    pass

from pynput import keyboard

log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pynput_test.log")

def log(msg):
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

log("Starting pynput test...")

def on_press(key):
    try:
        log(f"Key pressed: {key} char={getattr(key, 'char', None)}")
    except Exception as e:
        log(f"Error: {e}")

def on_release(key):
    log(f"Key released: {key}")
    if key == keyboard.Key.esc:
        log("ESC pressed, stopping...")
        return False

log("Creating listener...")
with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
    log("Listener started, waiting...")
    listener.join()

log("Done.")
