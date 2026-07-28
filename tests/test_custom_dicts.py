"""Tests for managed custom dictionaries."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import qwasda.custom_dicts as custom_dicts
from qwasda.custom_dicts import CustomDictionaryError, CustomDictionaryManager
from qwasda.dicts import DictionaryLoader, SortedWordIndex


def write_dictionary(path: Path, content: str, *, encoding: str = "utf-8") -> Path:
    path.write_text(content, encoding=encoding)
    return path


def test_import_file_normalizes_bom_duplicates_and_apostrophe(tmp_path: Path) -> None:
    source = write_dictionary(
        tmp_path / "source.txt",
        "\ufeffHello\nhello\nL’Heure\n\n",
    )
    manager = CustomDictionaryManager(tmp_path / "app")

    record = manager.import_file(source, "en", " My dictionary ")

    assert record.name == "My dictionary"
    assert record.word_count == 2
    assert manager.contains_en("hello")
    assert manager.contains_en("l'heure")
    managed = (tmp_path / "app" / "dictionaries" / record.filename).read_text(encoding="utf-8")
    assert managed == "hello\nl'heure\n"


def test_import_file_requires_matching_language(tmp_path: Path) -> None:
    source = write_dictionary(tmp_path / "uk.txt", "привіт\n")
    manager = CustomDictionaryManager(tmp_path / "app")

    with pytest.raises(CustomDictionaryError, match="Рядок 1"):
        manager.import_file(source, "en")

    assert manager.records == ()


def test_import_file_rejects_invalid_utf8(tmp_path: Path) -> None:
    source = tmp_path / "broken.txt"
    source.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(CustomDictionaryError, match="UTF-8"):
        CustomDictionaryManager(tmp_path / "app").import_file(source, "en")


def test_import_file_enforces_size_and_unique_word_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_dictionary(tmp_path / "words.txt", "one\ntwo\nthree\n")
    manager = CustomDictionaryManager(tmp_path / "app")
    monkeypatch.setattr(custom_dicts, "MAX_FILE_SIZE", 2)
    with pytest.raises(CustomDictionaryError, match="25 MiB"):
        manager.import_file(source, "en")

    monkeypatch.setattr(custom_dicts, "MAX_FILE_SIZE", 1024)
    monkeypatch.setattr(custom_dicts, "MAX_UNIQUE_WORDS", 2)
    with pytest.raises(CustomDictionaryError, match="500 000"):
        manager.import_file(source, "en")


def test_manifest_round_trip_enable_rename_and_delete(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    source = write_dictionary(tmp_path / "words.txt", "one\ntwo\n")
    manager = CustomDictionaryManager(app_dir)
    record = manager.import_file(source, "en", "Original")

    manager.rename(record.id, "Renamed")
    manager.set_enabled(record.id, False)
    restored = CustomDictionaryManager(app_dir)
    restored.load()

    assert restored.records[0].name == "Renamed"
    assert restored.records[0].enabled is False
    assert not restored.contains_en("one")

    restored.set_enabled(record.id, True)
    assert restored.contains_en("one")
    restored.delete(record.id)
    assert restored.records == ()
    assert not (app_dir / "dictionaries" / record.filename).exists()


def test_missing_managed_file_is_disabled_without_breaking_load(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    source = write_dictionary(tmp_path / "words.txt", "hello\n")
    manager = CustomDictionaryManager(app_dir)
    record = manager.import_file(source, "en")
    (app_dir / "dictionaries" / record.filename).unlink()

    restored = CustomDictionaryManager(app_dir)
    restored.load()

    assert restored.records[0].enabled is False
    assert restored.records[0].error
    assert not restored.contains_en("hello")


def test_corrupt_manifest_falls_back_to_empty_snapshot(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "dictionaries.json").write_text("{broken", encoding="utf-8")

    manager = CustomDictionaryManager(app_dir)
    manager.load()

    assert manager.records == ()
    assert manager.snapshot.english == frozenset()


def test_manifest_never_allows_managed_path_escape(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    manifest = {
        "version": 1,
        "dictionaries": [
            {
                "id": "safe",
                "name": "Safe",
                "language": "en",
                "filename": "../outside.txt",
                "word_count": 1,
                "enabled": True,
                "error": None,
            }
        ],
    }
    (app_dir / "dictionaries.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_dictionary(app_dir / "outside.txt", "secret\n")

    manager = CustomDictionaryManager(app_dir)
    manager.load()

    assert manager.records[0].filename == "outside.txt"
    assert manager.records[0].enabled is False
    assert not manager.contains_en("secret")


def test_snapshot_reads_remain_consistent_during_reload(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    source = write_dictionary(tmp_path / "words.txt", "alpha\nbeta\n")
    manager = CustomDictionaryManager(app_dir)
    record = manager.import_file(source, "en")
    observed: list[tuple[bool, bool]] = []

    def reader() -> None:
        for _ in range(2_000):
            snapshot = manager.snapshot
            observed.append(("alpha" in snapshot.english, "beta" in snapshot.english))

    thread = threading.Thread(target=reader)
    thread.start()
    manager.set_enabled(record.id, False)
    manager.set_enabled(record.id, True)
    thread.join()

    assert set(observed) <= {(True, True), (False, False)}


def test_dictionary_loader_combines_builtin_and_custom_words(tmp_path: Path) -> None:
    source = write_dictionary(tmp_path / "custom.txt", "custom\n")
    loader = DictionaryLoader(str(tmp_path / "app"))
    loader.dict_en = frozenset({"builtin"})
    loader.dict_uk = SortedWordIndex("привіт\n".encode())
    loader.custom.import_file(source, "en")

    assert loader.contains_en("builtin")
    assert loader.contains_en("custom")
    assert loader.contains_uk("привіт")
