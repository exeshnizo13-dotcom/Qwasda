"""Thread-confined Tk settings window for Qwasda."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .custom_dicts import CustomDictionaryError

if TYPE_CHECKING:
    from .dicts import DictionaryLoader


class SettingsWindow:
    """Own a single settings window and all Tk objects on one UI thread."""

    def __init__(
        self,
        dict_loader: DictionaryLoader,
        on_dictionaries_changed: Callable[[], None],
    ):
        self.dict_loader = dict_loader
        self.on_dictionaries_changed = on_dictionaries_changed
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

        tabs: dict[str, Any] = {"dictionaries": dictionaries_tab}

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
            root.after(100, poll_commands)

        refresh()
        self._ready.set()
        root.after(50, poll_commands)
        root.mainloop()
