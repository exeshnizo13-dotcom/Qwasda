"""
System tray icon and menu for Qwasda.

Uses pystray for cross-platform tray support (Windows only in practice).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Config
    from .dicts import DictionaryLoader
    from .learning import LearningManager

pystray: Any = None
Image: Any = None
ImageDraw: Any = None
ImageFont: Any = None

try:
    import pystray as pystray_module  # type: ignore[import-not-found]
    from PIL import Image as image_module
    from PIL import ImageDraw as image_draw_module
    from PIL import ImageFont as image_font_module
except ImportError:
    pass
else:
    pystray = pystray_module
    Image = image_module
    ImageDraw = image_draw_module
    ImageFont = image_font_module


class TrayIcon:
    """
    System tray icon with dynamic menu.
    Runs on dedicated thread; all UI callbacks marshal to main thread via queue.
    """

    def __init__(
        self,
        config: Config,
        learning: LearningManager,
        dict_loader: DictionaryLoader,
        on_toggle_enabled: Callable[[], None],
        on_toggle_auto: Callable[[], None],
        on_toggle_learning: Callable[[], None],
        on_forget_learned: Callable[[], None],
        on_toggle_startup: Callable[[], None],
        on_exit: Callable[[], None],
        version: str,
    ):
        self.config = config
        self.learning = learning
        self.dict_loader = dict_loader
        self.version = version

        # Callbacks (must be thread-safe)
        self._toggle_enabled_callback = on_toggle_enabled
        self._toggle_auto_callback = on_toggle_auto
        self._toggle_learning_callback = on_toggle_learning
        self._forget_learned_callback = on_forget_learned
        self._toggle_startup_callback = on_toggle_startup
        self._exit_callback = on_exit

        self._icon: Any = None
        self._thread: threading.Thread | None = None
        self._running = False

    def run(self) -> None:
        """Start tray icon in background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        if pystray is None:
            return

        self._icon = pystray.Icon(
            "Qwasda",
            self._make_image(),
            f"Qwasda v{self.version} — перемикач розкладки",
            self._make_menu(),
        )
        self._icon.notify(
            f"Qwasda v{self.version} запущено! Подвійний Ctrl — перемкнути слово.",
            "Qwasda",
        )
        self._icon.run()

    def stop(self) -> None:
        """Stop tray icon."""
        self._running = False
        if self._icon:
            self._icon.stop()
        if self._thread:
            self._thread.join(timeout=1.0)

    def is_running(self) -> bool:
        """Check if tray is running."""
        return self._running and self._thread is not None and self._thread.is_alive()

    def update_menu(self) -> None:
        """Refresh menu (call after config/learning changes)."""
        if self._icon:
            self._icon.menu = self._make_menu()

    def notify(self, message: str, title: str = "Qwasda") -> None:
        """Show tray notification."""
        if self._icon:
            self._icon.notify(message, title)

    def _make_image(self) -> Any:
        """Create tray icon image (64x64 RGBA)."""
        if Image is None:
            return None

        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([4, 4, 60, 60], radius=12, fill=(41, 128, 185))

        try:
            font = ImageFont.truetype("arial.ttf", 26)
        except Exception:
            font = ImageFont.load_default()

        draw.text((10, 14), "Qw", fill="white", font=font)
        return img

    def _make_menu(self) -> Any:
        """Build dynamic menu with current state."""
        if pystray is None:
            return None

        cfg = self.config  # Config object directly
        stats = self.learning.stats()

        return pystray.Menu(
            pystray.MenuItem(
                lambda item: "✅ Qwasda увімкнено" if cfg.enabled else "⏸ Qwasda вимкнено",
                self._on_toggle_enabled,
                checked=lambda item: cfg.enabled,
            ),
            pystray.MenuItem(
                lambda item: (
                    "🔄 Автокорекція: ON" if cfg.auto_correct_enabled else "🔄 Автокорекція: OFF"
                )
                + ("" if self.dict_loader.dicts_loaded else " (словники не завантажено)"),
                self._on_toggle_auto,
                checked=lambda item: cfg.auto_correct_enabled,
                enabled=lambda item: self.dict_loader.dicts_loaded,
            ),
            pystray.MenuItem(
                lambda item: "🧠 Навчання: ON" if cfg.learning_enabled else "🧠 Навчання: OFF",
                self._on_toggle_learning,
                checked=lambda item: cfg.learning_enabled,
            ),
            pystray.MenuItem(
                lambda item: f"🧹 Забути вивчене ({stats['total']})",
                self._on_forget_learned,
            ),
            pystray.MenuItem(
                lambda item: "✅ Автозапуск" if self._is_in_startup() else "❌ Автозапуск",
                self._on_toggle_startup,
                checked=lambda item: self._is_in_startup(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Вихід", self._on_exit),
        )

    def _is_in_startup(self) -> bool:
        import os

        startup_dir = os.path.join(
            os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"
        )
        return os.path.exists(os.path.join(startup_dir, "Qwasda.bat"))

    # Callback wrappers
    def _on_toggle_enabled(self, icon: Any, item: Any) -> None:
        self._toggle_enabled_callback()

    def _on_toggle_auto(self, icon: Any, item: Any) -> None:
        self._toggle_auto_callback()

    def _on_toggle_learning(self, icon: Any, item: Any) -> None:
        self._toggle_learning_callback()

    def _on_forget_learned(self, icon: Any, item: Any) -> None:
        self._forget_learned_callback()

    def _on_toggle_startup(self, icon: Any, item: Any) -> None:
        self._toggle_startup_callback()

    def _on_exit(self, icon: Any, item: Any) -> None:
        self._exit_callback()
