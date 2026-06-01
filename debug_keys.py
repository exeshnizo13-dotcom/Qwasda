"""Тестування клавіш — запустіть і натискайте клавіші, щоб побачити їх коди."""
import keyboard
import time

def on_event(e):
    if e.event_type == keyboard.KEY_DOWN:
        print(f"DOWN: name={repr(e.name)}, scan={e.scan_code}")

keyboard.hook(on_event)
print("Hooked! Press keys for 15 seconds...")
print("Спробуйте натиснути ` (backtick) і ' (апостроф)")
time.sleep(15)
keyboard.unhook_all()
print("Stopped.")
