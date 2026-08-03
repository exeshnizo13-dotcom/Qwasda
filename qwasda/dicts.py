"""Persistent, memory-mapped dictionary indexes."""

from __future__ import annotations

import ctypes
import gzip
import json
import logging
import mmap
import os
import struct
import sys
import threading
import time
from array import array
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .custom_dicts import CustomDictionaryManager, CustomDictionaryRecord


def _set_background_thread_priority() -> None:
    """Lower CPU and I/O priority for the dictionary builder on Windows."""
    if os.name != "nt":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), 0x00010000)  # BACKGROUND_BEGIN
    except (AttributeError, OSError):
        pass


class SortedWordIndex:
    """Binary-search index over newline-delimited sorted UTF-8 words."""

    __slots__ = ("_data", "_offs", "_count", "_data_owner", "_offs_owner")

    def __init__(
        self,
        data: bytes | mmap.mmap,
        offsets: memoryview | None = None,
        count: int | None = None,
        *,
        data_owner: object | None = None,
        offsets_owner: object | None = None,
    ):
        if offsets is None:
            if b"\r" in data:
                data = data.replace(b"\r", b"")
            if data and not data.endswith(b"\n"):
                data += b"\n"
            if len(data) >= 2**32:
                raise ValueError("Dictionary too large for 32-bit offsets")
            built_offsets = array("I", [0])
            find = data.find
            position = find(b"\n")
            while position != -1:
                built_offsets.append(position + 1)
                position = find(b"\n", position + 1)
            offsets = memoryview(built_offsets)
            count = len(built_offsets) - 1
        elif count is None or len(offsets) != count + 1:
            raise ValueError("Invalid dictionary offsets")
        self._data = data
        self._offs = offsets
        self._count = count or 0
        self._data_owner = data_owner
        self._offs_owner = offsets_owner

    def __len__(self) -> int:
        return self._count

    def __contains__(self, word: str) -> bool:
        key = word.encode("utf-8")
        lo, hi = 0, self._count
        while lo < hi:
            mid = (lo + hi) >> 1
            cur = self._data[self._offs[mid] : self._offs[mid + 1] - 1]
            if cur < key:
                lo = mid + 1
            elif cur > key:
                hi = mid
            else:
                return True
        return False

    def close(self) -> None:
        """Release mapped buffers; indexes built from bytes need no cleanup."""
        try:
            self._offs.release()
        except (AttributeError, ValueError):
            pass
        for owner in (self._offs_owner, self._data_owner):
            try:
                if isinstance(owner, mmap.mmap):
                    owner.close()
            except (BufferError, OSError):
                pass
        self._data_owner = None
        self._offs_owner = None


# Module-level variables retained for compatibility with existing callers/tests.
DICT_EN: SortedWordIndex = SortedWordIndex(b"")
DICT_UK: SortedWordIndex = SortedWordIndex(b"")
dicts_loaded = False


class DictionaryLoader:
    """Load built-in dictionaries from a persistent mmap cache."""

    CACHE_FORMAT_VERSION = 1
    _MANIFEST_NAME = "dictionary-manifest.json"
    _DEFAULT_MANIFEST: dict[str, dict[str, object]] = {
        "words_en.txt.gz": {
            "sha256": "47257c3940e0363f3b09e976818e3eb4e93f467f00441e5b27e9c8665f311470",
            "words": 370104,
        },
        "words_uk.txt.gz": {
            "sha256": "9ea6d864faf5c8fba74132e4d51d4f5d45f69cad5aac01a1be62fd233f9b71df",
            "words": 3817543,
        },
    }

    def __init__(
        self,
        data_dir: str,
        on_loaded: Callable[[bool], None] | None = None,
    ):
        self.data_dir = data_dir
        self.on_loaded = on_loaded
        self.dict_en = SortedWordIndex(b"")
        self.dict_uk = SortedWordIndex(b"")
        self.dicts_loaded = False
        self.state = "idle"
        self.custom = CustomDictionaryManager(data_dir)
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._logger = logging.getLogger("qwasda.dicts")

    def _resource_candidates(self, name: str) -> list[str]:
        module_dir = os.path.dirname(os.path.abspath(__file__))
        candidates: list[str] = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(os.path.join(meipass, "data", name))
        if self.data_dir:
            candidates.extend(
                [os.path.join(self.data_dir, name), os.path.join(self.data_dir, "data", name)]
            )
        candidates.extend(
            [os.path.join(module_dir, "data", name), os.path.join(os.path.dirname(module_dir), "data", name)]
        )
        seen: set[str] = set()
        unique: list[str] = []
        for path in candidates:
            normalized = os.path.normpath(path)
            if normalized not in seen:
                seen.add(normalized)
                unique.append(normalized)
        return unique

    def _resource_path(self, name: str) -> str:
        candidates = self._resource_candidates(name)
        for path in candidates:
            if os.path.exists(path):
                return path
        return candidates[0]

    def _manifest(self) -> dict[str, dict[str, object]]:
        for path in self._resource_candidates(self._MANIFEST_NAME):
            try:
                with open(path, encoding="utf-8") as handle:
                    value = json.load(handle)
                dictionaries = value.get("dictionaries") if isinstance(value, dict) else None
                if isinstance(dictionaries, dict):
                    return dictionaries
            except (OSError, json.JSONDecodeError):
                continue
        return self._DEFAULT_MANIFEST

    def _cache_dir(self) -> Path:
        root = Path(os.environ.get("LOCALAPPDATA", self.data_dir or Path.home())) / "Qwasda"
        return root / "cache"

    def _cache_paths(self, name: str) -> tuple[Path, Path, Path]:
        digest = str(self._manifest().get(name, {}).get("sha256", "unknown"))
        stem = f"{Path(name).stem}-{digest[:16]}-v{self.CACHE_FORMAT_VERSION}"
        root = self._cache_dir()
        return root / f"{stem}.data", root / f"{stem}.offs", root / f"{stem}.json"

    def _open_cached(self, name: str) -> SortedWordIndex | None:
        data_path, offs_path, meta_path = self._cache_paths(name)
        data_map: mmap.mmap | None = None
        offs_map: mmap.mmap | None = None
        try:
            with meta_path.open(encoding="utf-8") as handle:
                meta = json.load(handle)
            data_size = int(meta["data_size"])
            count = int(meta["count"])
            if int(meta["format"]) != self.CACHE_FORMAT_VERSION:
                return None
            if data_path.stat().st_size != data_size or offs_path.stat().st_size != (count + 1) * 4:
                return None
            with data_path.open("rb") as data_handle, offs_path.open("rb") as offs_handle:
                data_map = mmap.mmap(data_handle.fileno(), 0, access=mmap.ACCESS_READ)
                offs_map = mmap.mmap(offs_handle.fileno(), 0, access=mmap.ACCESS_READ)
            offsets = memoryview(offs_map).cast("I")
            return SortedWordIndex(data_map, offsets, count, data_owner=data_map, offsets_owner=offs_map)
        except (OSError, KeyError, TypeError, ValueError, BufferError):
            if data_map is not None:
                data_map.close()
            if offs_map is not None:
                offs_map.close()
            return None

    def _build_cache(self, name: str) -> SortedWordIndex:
        source_path = self._resource_path(name)
        data_path, offs_path, meta_path = self._cache_paths(name)
        data_tmp = data_path.with_suffix(data_path.suffix + ".tmp")
        offs_tmp = offs_path.with_suffix(offs_path.suffix + ".tmp")
        count = 0
        offset = 0
        previous: bytes | None = None
        try:
            data_path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(source_path, "rb") as source, data_tmp.open("wb") as data_out, offs_tmp.open("wb") as offs_out:
                offs_out.write(struct.pack("<I", 0))
                for raw_line in source:
                    if self._cancel.is_set():
                        raise InterruptedError("dictionary build cancelled")
                    word = raw_line.rstrip(b"\r\n")
                    if not word:
                        continue
                    if previous is not None and previous > word:
                        raise ValueError(f"Dictionary {name} is not sorted")
                    previous = word
                    data_out.write(word + b"\n")
                    offset += len(word) + 1
                    if offset >= 2**32:
                        raise ValueError("Dictionary too large for 32-bit offsets")
                    offs_out.write(struct.pack("<I", offset))
                    count += 1
            os.replace(data_tmp, data_path)
            os.replace(offs_tmp, offs_path)
            meta_tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
            meta_tmp.write_text(json.dumps({"format": self.CACHE_FORMAT_VERSION, "data_size": offset, "count": count}, sort_keys=True), encoding="utf-8")
            os.replace(meta_tmp, meta_path)
            result = self._open_cached(name)
            if result is None:
                raise OSError(f"Unable to reopen dictionary cache: {name}")
            return result
        except Exception:
            for temporary in (data_tmp, offs_tmp):
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def _load_one(self, name: str) -> SortedWordIndex:
        started = time.perf_counter()
        cached = self._open_cached(name)
        if cached is not None:
            self._logger.info("Dictionary cache hit", extra={"dict_name": name})
            return cached
        self._logger.info("Dictionary cache miss", extra={"dict_name": name})
        result = self._build_cache(name)
        self._logger.info("Dictionary cache built", extra={"dict_name": name, "words": len(result), "seconds": round(time.perf_counter() - started, 3)})
        return result

    def _set_loaded(self, en: SortedWordIndex, uk: SortedWordIndex) -> None:
        self.dict_en, self.dict_uk = en, uk
        self.dicts_loaded = bool(len(en) and len(uk))
        self.state = "ready" if self.dicts_loaded else "failed"
        global DICT_EN, DICT_UK, dicts_loaded
        DICT_EN, DICT_UK, dicts_loaded = en, uk, self.dicts_loaded

    def _load_all(self) -> None:
        started = time.perf_counter()
        try:
            en = self._load_one("words_en.txt.gz")
            uk = self._load_one("words_uk.txt.gz")
            if self._cancel.is_set():
                en.close()
                uk.close()
                self.state = "cancelled"
                return
            self.custom.load()
            self._set_loaded(en, uk)
            if self.on_loaded:
                self.on_loaded(self.dicts_loaded)
            self._logger.info("Dictionaries ready", extra={"seconds": round(time.perf_counter() - started, 3)})
        except InterruptedError:
            self.state = "cancelled"
        except Exception:
            self.state = "failed"
            self._logger.exception("Failed to load dictionaries")

    def load(self) -> None:
        """Synchronous load retained for tests and maintenance tools."""
        self._cancel.clear()
        self.state = "loading"
        self._load_all()

    def load_async(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._cancel.clear()
        self.state = "loading"
        self._thread = threading.Thread(target=self._load_worker, daemon=True, name="qwasda-dicts")
        self._thread.start()

    def load_cached_or_async(self) -> bool:
        """Use a complete mmap cache immediately, otherwise build in background."""
        self._cancel.clear()
        self.state = "loading"
        en = self._open_cached("words_en.txt.gz")
        uk = self._open_cached("words_uk.txt.gz")
        if en is not None and uk is not None:
            self.custom.load()
            self._set_loaded(en, uk)
            if self.on_loaded:
                self.on_loaded(True)
            self._logger.info("Dictionary cache ready synchronously")
            return True
        if en is not None:
            en.close()
        if uk is not None:
            uk.close()
        self.load_async()
        return False

    def _load_worker(self) -> None:
        _set_background_thread_priority()
        self._load_all()

    def wait_loaded(self, timeout: float | None = None) -> bool:
        if self._thread:
            self._thread.join(timeout)
        return self.dicts_loaded

    def cancel(self) -> None:
        self._cancel.set()

    def close(self) -> None:
        self.cancel()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self.dict_en.close()
        self.dict_uk.close()

    def contains_en(self, word: str) -> bool:
        return word in self.dict_en or self.custom.contains_en(word)

    def contains_uk(self, word: str) -> bool:
        return word in self.dict_uk or self.custom.contains_uk(word)

    @property
    def custom_records(self) -> tuple[CustomDictionaryRecord, ...]:
        return self.custom.records

    def reload_custom_dictionaries(self) -> None:
        self.custom.load()
