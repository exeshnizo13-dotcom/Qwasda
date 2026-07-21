"""
Dictionary loader for Qwasda.

Loads English (frozenset) and Ukrainian (SortedWordIndex) dictionaries
from gzipped text files in background thread.
"""

import gzip
import os
import sys
import threading
from array import array
from collections.abc import Callable


class SortedWordIndex:
    """
    Binary-search membership test on sorted, gzipped word list.

    Memory-efficient: stores raw bytes + 32-bit offsets array (~90 MB for 3.8M words)
    vs ~480 MB for frozenset of strings.
    """

    __slots__ = ("_data", "_offs", "_count")

    def __init__(self, data: bytes):
        if b"\r" in data:
            data = data.replace(b"\r", b"")
        if data and not data.endswith(b"\n"):
            data += b"\n"
        if len(data) >= 2**32:
            raise ValueError("Dictionary too large for 32-bit offsets")

        self._data = data
        offs = array("I", [0])
        find = data.find
        i = find(b"\n")
        while i != -1:
            offs.append(i + 1)
            i = find(b"\n", i + 1)
        self._offs = offs
        self._count = len(offs) - 1

    def __len__(self) -> int:
        return self._count

    def __contains__(self, word: str) -> bool:
        key = word.encode("utf-8")
        data = self._data
        offs = self._offs
        lo, hi = 0, self._count
        while lo < hi:
            mid = (lo + hi) >> 1
            cur = data[offs[mid] : offs[mid + 1] - 1]
            if cur < key:
                lo = mid + 1
            elif cur > key:
                hi = mid
            else:
                return True
        return False


# Module-level variables for backward compatibility with tests
DICT_EN: frozenset[str] = frozenset()
DICT_UK: SortedWordIndex = SortedWordIndex(b"")
dicts_loaded = False


class DictionaryLoader:
    """
    Loads dictionaries in background thread.

    English: frozenset (small, ~370K words)
    Ukrainian: SortedWordIndex (large, ~3.8M words)
    """

    def __init__(
        self,
        data_dir: str,
        on_loaded: Callable[[bool], None] | None = None,
    ):
        self.data_dir = data_dir
        self.on_loaded = on_loaded
        self.dict_en: frozenset[str] = frozenset()
        self.dict_uk: SortedWordIndex = SortedWordIndex(b"")
        self.dicts_loaded = False
        self._thread: threading.Thread | None = None

    def _resource_path(self, name: str) -> str:
        """Resolve path for both script and PyInstaller frozen exe."""
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, "data", name)

    def _load_frozenset(self, name: str) -> frozenset[str]:
        try:
            with gzip.open(self._resource_path(name), "rt", encoding="utf-8") as f:
                return frozenset(line.strip() for line in f if line.strip())
        except OSError:
            return frozenset()

    def _load_index(self, name: str) -> SortedWordIndex:
        try:
            with gzip.open(self._resource_path(name), "rb") as f:
                return SortedWordIndex(f.read())
        except OSError:
            return SortedWordIndex(b"")

    def load(self) -> None:
        """Synchronous load (for tests)."""
        self.dict_en = self._load_frozenset("words_en.txt.gz")
        self.dict_uk = self._load_index("words_uk.txt.gz")
        self.dicts_loaded = bool(len(self.dict_en) and len(self.dict_uk))
        # Update module-level variables for backward compatibility
        global DICT_EN, DICT_UK, dicts_loaded
        DICT_EN = self.dict_en
        DICT_UK = self.dict_uk
        dicts_loaded = self.dicts_loaded

    def load_async(self) -> None:
        """Start background loading."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._load_worker, daemon=True)
        self._thread.start()

    def _load_worker(self) -> None:
        self.dict_en = self._load_frozenset("words_en.txt.gz")
        self.dict_uk = self._load_index("words_uk.txt.gz")
        self.dicts_loaded = bool(len(self.dict_en) and len(self.dict_uk))
        # Update module-level variables
        global DICT_EN, DICT_UK, dicts_loaded
        DICT_EN = self.dict_en
        DICT_UK = self.dict_uk
        dicts_loaded = self.dicts_loaded
        if self.on_loaded:
            self.on_loaded(self.dicts_loaded)

    def wait_loaded(self, timeout: float | None = None) -> bool:
        """Wait for background load to complete."""
        if self._thread:
            self._thread.join(timeout)
        return self.dicts_loaded
