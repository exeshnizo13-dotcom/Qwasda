"""Thread-confined Tk settings window for Qwasda."""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .custom_dicts import CustomDictionaryError
from .hotkeys import (
    MODIFIER_KEYS,
    HotkeyAction,
    HotkeyBinding,
    HotkeyBindings,
    binding_to_text,
    current_modifier_mask,
    default_hotkeys,
)
from .statistics import StatisticsManager
from .win32 import (
    VK_CONTROL,
    VK_ESCAPE,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_MENU,
    VK_RCONTROL,
    VK_RMENU,
    VK_RSHIFT,
    VK_SHIFT,
)

if TYPE_CHECKING:
    from .config import Config
    from .dicts import DictionaryLoader


class SettingsWindow:
    """Own a single settings window and all Tk objects on one UI thread."""

    def __init__(
        self,
        config: Config,
        dict_loader: DictionaryLoader,
        on_dictionaries_changed: Callable[[], None],
        on_hotkeys_changed: Callable[[HotkeyBindings], str | None],
        statistics: StatisticsManager,
        on_statistics_enabled: Callable[[bool], str | None],
        on_statistics_cleared: Callable[[], str | None],
    ):
        self.config = config
        self.dict_loader = dict_loader
        self.on_dictionaries_changed = on_dictionaries_changed
        self.on_hotkeys_changed = on_hotkeys_changed
        self.statistics = statistics
        self.on_statistics_enabled = on_statistics_enabled
        self.on_statistics_cleared = on_statistics_cleared
        self._commands: queue.Queue[tuple[str, str | None]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def show(self, tab: str = "dictionaries") -> None:
        """Create or focus the settings window on the requested tab."""
        if self._thread is None or not self._thread.is_alive():
            self._ready.clear()
            self._thread = threading.Thread(target=self._run, name="qwasda-settings", daemon=True)
            self._thread.start()
            self._ready.wait(timeout=2.0)
        self._commands.put(("show", tab))

    def stop(self) -> None:
        """Close the UI loop and wait briefly for its thread."""
        thread = self._thread
        if thread is None or not thread.is_alive():
            return
        self._commands.put(("stop", None))
        thread.join(timeout=2.0)

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, simpledialog, ttk

        root = tk.Tk()
        root.title("Qwasda — Налаштування")
        root.geometry("720x430")
        root.minsize(620, 360)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        dictionaries_tab = ttk.Frame(notebook)
        notebook.add(dictionaries_tab, text="Словники")

        toolbar = ttk.Frame(dictionaries_tab)
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(toolbar, text="Мова нового словника:").pack(side="left")
        language_var = tk.StringVar(value="UK")
        ttk.Combobox(
            toolbar,
            textvariable=language_var,
            values=("UK", "EN"),
            state="readonly",
            width=5,
        ).pack(side="left", padx=(6, 12))

        tree = ttk.Treeview(
            dictionaries_tab,
            columns=("language", "words", "status"),
            show="tree headings",
            selectmode="browse",
        )
        tree.heading("#0", text="Назва")
        tree.heading("language", text="Мова")
        tree.heading("words", text="Слів")
        tree.heading("status", text="Стан")
        tree.column("#0", width=280)
        tree.column("language", width=70, anchor="center")
        tree.column("words", width=100, anchor="e")
        tree.column("status", width=190)
        tree.pack(fill="both", expand=True)

        buttons = ttk.Frame(dictionaries_tab)
        buttons.pack(fill="x", pady=(8, 0))

        def selected_id() -> str | None:
            selection = tree.selection()
            return str(selection[0]) if selection else None

        def refresh() -> None:
            for item in tree.get_children():
                tree.delete(item)
            for record in self.dict_loader.custom_records:
                status = record.error or ("Увімкнено" if record.enabled else "Вимкнено")
                tree.insert(
                    "",
                    "end",
                    iid=record.id,
                    text=record.name,
                    values=(record.language.upper(), record.word_count, status),
                )

        def changed() -> None:
            refresh()
            self.on_dictionaries_changed()

        def import_dictionary() -> None:
            source = filedialog.askopenfilename(
                parent=root,
                title="Імпортувати словник",
                filetypes=(("Текстові словники", "*.txt"),),
            )
            if not source:
                return
            try:
                self.dict_loader.custom.import_file(
                    source,
                    "en" if language_var.get() == "EN" else "uk",
                    Path(source).stem,
                )
            except (CustomDictionaryError, OSError) as exc:
                messagebox.showerror("Не вдалося імпортувати", str(exc), parent=root)
                return
            changed()

        def rename_dictionary() -> None:
            record_id = selected_id()
            if record_id is None:
                return
            record = next(item for item in self.dict_loader.custom_records if item.id == record_id)
            name = simpledialog.askstring(
                "Перейменувати словник",
                "Нова назва:",
                initialvalue=record.name,
                parent=root,
            )
            if name is None:
                return
            try:
                self.dict_loader.custom.rename(record_id, name)
            except (CustomDictionaryError, OSError) as exc:
                messagebox.showerror("Не вдалося перейменувати", str(exc), parent=root)
                return
            changed()

        def toggle_dictionary() -> None:
            record_id = selected_id()
            if record_id is None:
                return
            record = next(item for item in self.dict_loader.custom_records if item.id == record_id)
            try:
                self.dict_loader.custom.set_enabled(record_id, not record.enabled)
            except (CustomDictionaryError, OSError) as exc:
                messagebox.showerror("Не вдалося змінити стан", str(exc), parent=root)
                return
            changed()

        def delete_dictionary() -> None:
            record_id = selected_id()
            if record_id is None:
                return
            record = next(item for item in self.dict_loader.custom_records if item.id == record_id)
            if not messagebox.askyesno(
                "Видалити словник",
                f"Видалити словник «{record.name}»?",
                parent=root,
            ):
                return
            try:
                self.dict_loader.custom.delete(record_id)
            except OSError as exc:
                messagebox.showerror("Не вдалося видалити", str(exc), parent=root)
                return
            changed()

        ttk.Button(buttons, text="Імпортувати…", command=import_dictionary).pack(side="left")
        ttk.Button(buttons, text="Перейменувати…", command=rename_dictionary).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(buttons, text="Увімкнути / вимкнути", command=toggle_dictionary).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(buttons, text="Видалити", command=delete_dictionary).pack(
            side="left", padx=(6, 0)
        )

        hotkeys_tab = ttk.Frame(notebook)
        notebook.add(hotkeys_tab, text="Хоткеї")
        pending_hotkeys = self.config.hotkeys.copy()
        hotkey_labels: dict[HotkeyAction, Any] = {}
        action_names = {
            HotkeyAction.MANUAL_CONVERSION: "Ручна конвертація",
            HotkeyAction.TOGGLE_ENABLED: "Увімкнути / вимкнути Qwasda",
            HotkeyAction.TOGGLE_AUTOCORRECT: "Увімкнути / вимкнути автокорекцію",
        }

        def refresh_hotkeys() -> None:
            for action, label in hotkey_labels.items():
                label.configure(text=binding_to_text(pending_hotkeys[action]))

        def capture_hotkey(action: HotkeyAction) -> None:
            dialog = tk.Toplevel(root)
            dialog.title("Записати hotkey")
            dialog.transient(root)
            dialog.resizable(False, False)
            prompt = ttk.Label(
                dialog,
                text=(
                    "Натисніть комбінацію з Ctrl, Alt, Shift або Win."
                    + (
                        "\nДля double-tap двічі натисніть Ctrl, Shift або Alt."
                        if action == HotkeyAction.MANUAL_CONVERSION
                        else ""
                    )
                ),
                padding=20,
            )
            prompt.pack()
            last_modifier = 0
            last_modifier_time = 0.0

            def normalize_modifier(key: int) -> int | None:
                if key in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL):
                    return VK_CONTROL
                if key in (VK_SHIFT, VK_LSHIFT, VK_RSHIFT):
                    return VK_SHIFT
                if key in (VK_MENU, VK_LMENU, VK_RMENU):
                    return VK_MENU
                return None

            def on_key(event: Any) -> str:
                nonlocal last_modifier, last_modifier_time
                key = int(event.keycode)
                if key == VK_ESCAPE:
                    dialog.destroy()
                    return "break"
                modifier = normalize_modifier(key)
                now = time.monotonic()
                if modifier is not None:
                    if (
                        action == HotkeyAction.MANUAL_CONVERSION
                        and modifier == last_modifier
                        and now - last_modifier_time <= 0.8
                    ):
                        pending_hotkeys[action] = HotkeyBinding("double_tap", modifier)
                        refresh_hotkeys()
                        dialog.destroy()
                    else:
                        last_modifier = modifier
                        last_modifier_time = now
                        prompt.configure(text="Натисніть модифікатор ще раз або завершіть chord.")
                    return "break"
                modifiers = current_modifier_mask()
                if key in MODIFIER_KEYS or not modifiers:
                    prompt.configure(text="Комбінація має містити модифікатор і основну клавішу.")
                    return "break"
                pending_hotkeys[action] = HotkeyBinding("chord", key, modifiers)
                refresh_hotkeys()
                dialog.destroy()
                return "break"

            dialog.bind("<KeyPress>", on_key)
            dialog.grab_set()
            dialog.focus_force()

        def clear_hotkey(action: HotkeyAction) -> None:
            pending_hotkeys[action] = None
            refresh_hotkeys()

        def capture_command(action: HotkeyAction) -> Callable[[], None]:
            return lambda: capture_hotkey(action)

        def clear_command(action: HotkeyAction) -> Callable[[], None]:
            return lambda: clear_hotkey(action)

        for row, action in enumerate(HotkeyAction):
            ttk.Label(hotkeys_tab, text=action_names[action]).grid(
                row=row, column=0, sticky="w", padx=12, pady=10
            )
            label = ttk.Label(hotkeys_tab, width=28)
            label.grid(row=row, column=1, sticky="w", padx=12, pady=10)
            hotkey_labels[action] = label
            ttk.Button(
                hotkeys_tab,
                text="Записати…",
                command=capture_command(action),
            ).grid(row=row, column=2, padx=6, pady=10)
            ttk.Button(
                hotkeys_tab,
                text="Очистити",
                command=clear_command(action),
            ).grid(row=row, column=3, padx=6, pady=10)

        def restore_hotkeys() -> None:
            pending_hotkeys.clear()
            pending_hotkeys.update(default_hotkeys())
            refresh_hotkeys()

        def apply_hotkeys() -> None:
            error = self.on_hotkeys_changed(pending_hotkeys.copy())
            if error:
                messagebox.showerror("Не вдалося застосувати hotkeys", error, parent=root)
                return
            pending_hotkeys.clear()
            pending_hotkeys.update(self.config.hotkeys)
            refresh_hotkeys()

        hotkey_actions = ttk.Frame(hotkeys_tab)
        hotkey_actions.grid(
            row=len(HotkeyAction), column=0, columnspan=4, sticky="w", padx=12, pady=18
        )
        ttk.Button(hotkey_actions, text="Застосувати", command=apply_hotkeys).pack(side="left")
        ttk.Button(hotkey_actions, text="Відновити стандартні", command=restore_hotkeys).pack(
            side="left", padx=(8, 0)
        )
        refresh_hotkeys()

        statistics_tab = ttk.Frame(notebook)
        notebook.add(statistics_tab, text="Статистика")
        statistics_enabled = tk.BooleanVar(value=self.statistics.enabled)
        stats_today = ttk.Label(statistics_tab)
        stats_lifetime = ttk.Label(statistics_tab)
        ttk.Checkbutton(
            statistics_tab,
            text="Збирати анонімні агреговані лічильники",
            variable=statistics_enabled,
            command=lambda: apply_statistics_enabled(),
        ).pack(anchor="w", padx=12, pady=(18, 12))
        stats_today.pack(anchor="w", padx=12, pady=4)
        stats_lifetime.pack(anchor="w", padx=12, pady=4)

        def refresh_statistics() -> None:
            snapshot = self.statistics.snapshot
            stats_today.configure(
                text=(
                    f"Сьогодні: перемикань {snapshot.today_layout_switches}, "
                    f"автокорекцій {snapshot.today_autocorrections}, "
                    f"ручних конвертацій {snapshot.today_manual_conversions}"
                )
            )
            stats_lifetime.configure(
                text=(
                    f"За весь час: перемикань {snapshot.lifetime_layout_switches}, "
                    f"автокорекцій {snapshot.lifetime_autocorrections}, "
                    f"ручних конвертацій {snapshot.lifetime_manual_conversions}"
                )
            )

        def apply_statistics_enabled() -> None:
            error = self.on_statistics_enabled(bool(statistics_enabled.get()))
            if error:
                statistics_enabled.set(self.statistics.enabled)
                messagebox.showerror("Не вдалося змінити статистику", error, parent=root)
            refresh_statistics()

        def clear_statistics() -> None:
            if not messagebox.askyesno(
                "Очистити статистику",
                "Видалити всі агреговані лічильники?",
                parent=root,
            ):
                return
            error = self.on_statistics_cleared()
            if error:
                messagebox.showerror("Не вдалося очистити статистику", error, parent=root)
            refresh_statistics()

        ttk.Button(statistics_tab, text="Очистити статистику", command=clear_statistics).pack(
            anchor="w", padx=12, pady=18
        )
        refresh_statistics()

        tabs: dict[str, Any] = {
            "dictionaries": dictionaries_tab,
            "hotkeys": hotkeys_tab,
            "statistics": statistics_tab,
        }

        def poll_commands() -> None:
            try:
                while True:
                    command, argument = self._commands.get_nowait()
                    if command == "stop":
                        root.destroy()
                        return
                    if command == "show":
                        if argument in tabs:
                            notebook.select(tabs[argument])  # type: ignore[no-untyped-call]
                        refresh()
                        root.deiconify()
                        root.lift()
                        root.focus_force()
            except queue.Empty:
                pass
            refresh_statistics()
            root.after(100, poll_commands)

        refresh()
        self._ready.set()
        root.after(50, poll_commands)
        root.mainloop()
