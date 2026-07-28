"""Managed custom dictionaries stored in the user's Qwasda data directory."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, cast

DictionaryLanguage = Literal["en", "uk"]

MAX_FILE_SIZE = 25 * 1024 * 1024
MAX_UNIQUE_WORDS = 500_000
MAX_WORD_LENGTH = 128
APOSTROPHES = frozenset({"'", "’"})
UKRAINIAN_LETTERS = frozenset("абвгґдеєжзиіїйклмнопрстуфхцчшщьюя")
ENGLISH_LETTERS = frozenset("abcdefghijklmnopqrstuvwxyz")


class CustomDictionaryError(ValueError):
    """Raised when a custom dictionary cannot be imported or updated."""


@dataclass(frozen=True)
class CustomDictionaryRecord:
    """Persistent metadata for one managed custom dictionary."""

    id: str
    name: str
    language: DictionaryLanguage
    filename: str
    word_count: int
    enabled: bool = True
    error: str | None = None


@dataclass(frozen=True)
class CustomDictionarySnapshot:
    """Immutable lookup state swapped atomically after a reload."""

    english: frozenset[str] = frozenset()
    ukrainian: frozenset[str] = frozenset()


class CustomDictionaryManager:
    """Import, persist, validate, and query custom dictionaries."""

    MANIFEST_FILENAME = "dictionaries.json"
    MANAGED_DIRECTORY = "dictionaries"
    SCHEMA_VERSION = 1

    def __init__(self, app_dir: str | os.PathLike[str]):
        self.app_dir = Path(app_dir)
        self.manifest_path = self.app_dir / self.MANIFEST_FILENAME
        self.dictionary_dir = self.app_dir / self.MANAGED_DIRECTORY
        self._records: tuple[CustomDictionaryRecord, ...] = ()
        self._snapshot = CustomDictionarySnapshot()
        self._lock = threading.RLock()
        self._logger = logging.getLogger("qwasda.custom_dicts")

    @property
    def records(self) -> tuple[CustomDictionaryRecord, ...]:
        with self._lock:
            return self._records

    @property
    def snapshot(self) -> CustomDictionarySnapshot:
        return self._snapshot

    def contains_en(self, word: str) -> bool:
        return word in self._snapshot.english

    def contains_uk(self, word: str) -> bool:
        return word in self._snapshot.ukrainian

    def load(self) -> None:
        """Load the manifest and atomically rebuild enabled lookup sets."""
        with self._lock:
            records = self._read_manifest()
            loaded_records, snapshot, changed = self._load_records(records)
            self._records = tuple(loaded_records)
            self._snapshot = snapshot
            if changed:
                self._save_manifest()

    def import_file(
        self,
        source: str | os.PathLike[str],
        language: DictionaryLanguage,
        name: str | None = None,
    ) -> CustomDictionaryRecord:
        """Validate a UTF-8 word list and copy its normalized contents into AppData."""
        source_path = Path(source)
        if source_path.suffix.lower() != ".txt":
            raise CustomDictionaryError("Підтримуються лише файли .txt")
        if language not in ("en", "uk"):
            raise CustomDictionaryError("Мова словника має бути en або uk")

        words = self._read_and_validate(source_path, language)
        display_name = self._validate_name(name or source_path.stem)
        record_id = uuid.uuid4().hex
        filename = f"{record_id}.txt"
        record = CustomDictionaryRecord(
            id=record_id,
            name=display_name,
            language=language,
            filename=filename,
            word_count=len(words),
        )

        with self._lock:
            self.dictionary_dir.mkdir(parents=True, exist_ok=True)
            self._atomic_write_text(self.dictionary_dir / filename, "\n".join(words) + "\n")
            self._records = (*self._records, record)
            self._rebuild_snapshot()
            try:
                self._save_manifest()
            except OSError:
                self._records = self._records[:-1]
                self._rebuild_snapshot()
                with contextlib.suppress(OSError):
                    (self.dictionary_dir / filename).unlink()
                raise
        return record

    def rename(self, record_id: str, name: str) -> CustomDictionaryRecord:
        new_name = self._validate_name(name)
        with self._lock:
            index = self._record_index(record_id)
            updated = replace(self._records[index], name=new_name)
            self._replace_record(index, updated)
            return updated

    def set_enabled(self, record_id: str, enabled: bool) -> CustomDictionaryRecord:
        with self._lock:
            index = self._record_index(record_id)
            current = self._records[index]
            if enabled and current.error:
                raise CustomDictionaryError(current.error)
            updated = replace(current, enabled=bool(enabled))
            self._replace_record(index, updated, rebuild=True)
            return updated

    def delete(self, record_id: str) -> None:
        with self._lock:
            index = self._record_index(record_id)
            record = self._records[index]
            old_records = self._records
            self._records = old_records[:index] + old_records[index + 1 :]
            self._rebuild_snapshot()
            try:
                self._save_manifest()
            except OSError:
                self._records = old_records
                self._rebuild_snapshot()
                raise
            with contextlib.suppress(FileNotFoundError):
                (self.dictionary_dir / record.filename).unlink()

    def _replace_record(
        self, index: int, record: CustomDictionaryRecord, *, rebuild: bool = False
    ) -> None:
        old_records = self._records
        records = list(old_records)
        records[index] = record
        self._records = tuple(records)
        if rebuild:
            self._rebuild_snapshot()
        try:
            self._save_manifest()
        except OSError:
            self._records = old_records
            if rebuild:
                self._rebuild_snapshot()
            raise

    def _record_index(self, record_id: str) -> int:
        for index, record in enumerate(self._records):
            if record.id == record_id:
                return index
        raise KeyError(record_id)

    def _read_manifest(self) -> list[CustomDictionaryRecord]:
        try:
            with self.manifest_path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError):
            self._logger.exception("Failed to read custom dictionary manifest")
            return []

        if not isinstance(data, dict) or data.get("version") != self.SCHEMA_VERSION:
            self._logger.error("Unsupported custom dictionary manifest")
            return []
        raw_records = data.get("dictionaries")
        if not isinstance(raw_records, list):
            return []

        records: list[CustomDictionaryRecord] = []
        for raw in raw_records:
            try:
                if not isinstance(raw, dict):
                    continue
                language = raw.get("language")
                if language not in ("en", "uk"):
                    continue
                records.append(
                    CustomDictionaryRecord(
                        id=str(raw["id"]),
                        name=self._validate_name(str(raw["name"])),
                        language=cast(DictionaryLanguage, language),
                        filename=Path(str(raw["filename"])).name,
                        word_count=max(0, int(raw.get("word_count", 0))),
                        enabled=bool(raw.get("enabled", True)),
                        error=str(raw["error"]) if raw.get("error") else None,
                    )
                )
            except (KeyError, TypeError, ValueError):
                self._logger.warning("Skipping invalid custom dictionary record")
        return records

    def _load_records(
        self, records: list[CustomDictionaryRecord]
    ) -> tuple[list[CustomDictionaryRecord], CustomDictionarySnapshot, bool]:
        english: set[str] = set()
        ukrainian: set[str] = set()
        loaded: list[CustomDictionaryRecord] = []
        changed = False
        for record in records:
            path = self.dictionary_dir / record.filename
            try:
                words = self._read_and_validate(path, record.language)
                updated = replace(record, word_count=len(words), error=None)
                if record.enabled:
                    (english if record.language == "en" else ukrainian).update(words)
            except (OSError, UnicodeError, CustomDictionaryError) as exc:
                updated = replace(record, enabled=False, error=str(exc))
                self._logger.warning(
                    "Custom dictionary disabled",
                    extra={"dictionary_id": record.id, "path": str(path), "error": str(exc)},
                )
            changed = changed or updated != record
            loaded.append(updated)
        return loaded, CustomDictionarySnapshot(frozenset(english), frozenset(ukrainian)), changed

    def _rebuild_snapshot(self) -> None:
        records, snapshot, changed = self._load_records(list(self._records))
        self._records = tuple(records)
        self._snapshot = snapshot
        if changed:
            self._save_manifest()

    def _read_and_validate(self, path: Path, language: DictionaryLanguage) -> tuple[str, ...]:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise CustomDictionaryError(f"Файл недоступний: {path.name}") from exc
        if size > MAX_FILE_SIZE:
            raise CustomDictionaryError("Файл перевищує обмеження 25 MiB")
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CustomDictionaryError("Файл має бути в кодуванні UTF-8") from exc

        words: set[str] = set()
        for line_number, raw_word in enumerate(text.splitlines(), 1):
            word = raw_word.strip().lower().replace("’", "'")
            if not word:
                continue
            if len(word) > MAX_WORD_LENGTH:
                raise CustomDictionaryError(f"Рядок {line_number}: слово довше 128 символів")
            if not self._valid_word(word, language):
                raise CustomDictionaryError(
                    f"Рядок {line_number}: слово не відповідає мові {language.upper()}"
                )
            words.add(word)
            if len(words) > MAX_UNIQUE_WORDS:
                raise CustomDictionaryError("Словник містить понад 500 000 унікальних слів")
        if not words:
            raise CustomDictionaryError("Словник не містить валідних слів")
        return tuple(sorted(words))

    @staticmethod
    def _valid_word(word: str, language: DictionaryLanguage) -> bool:
        allowed = ENGLISH_LETTERS if language == "en" else UKRAINIAN_LETTERS
        previous_letter = False
        pending_apostrophe = False
        saw_letter = False
        for character in word:
            if character in allowed:
                previous_letter = True
                pending_apostrophe = False
                saw_letter = True
            elif character in APOSTROPHES and previous_letter:
                previous_letter = False
                pending_apostrophe = True
            else:
                return False
        return saw_letter and not pending_apostrophe

    @staticmethod
    def _validate_name(name: str) -> str:
        normalized = " ".join(name.split())
        if not normalized:
            raise CustomDictionaryError("Назва словника не може бути порожньою")
        if len(normalized) > 100:
            raise CustomDictionaryError("Назва словника довша за 100 символів")
        return normalized

    def _save_manifest(self) -> None:
        self.app_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "version": self.SCHEMA_VERSION,
            "dictionaries": [asdict(record) for record in self._records],
        }
        self._atomic_write_text(
            self.manifest_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        )

    @staticmethod
    def _atomic_write_text(path: Path, content: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(content, encoding="utf-8", newline="\n")
            os.replace(tmp, path)
        except OSError:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
