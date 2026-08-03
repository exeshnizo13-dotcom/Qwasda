from __future__ import annotations

import gzip
from pathlib import Path

from qwasda.dicts import DictionaryLoader


def _write_dictionary(root: Path, name: str, words: list[str]) -> None:
    with gzip.open(root / name, "wb") as handle:
        handle.write(("\n".join(words) + "\n").encode("utf-8"))


def test_dictionary_loader_builds_and_reuses_mmap_cache(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _write_dictionary(data, "words_en.txt.gz", ["alpha", "hello", "zulu"])
    _write_dictionary(data, "words_uk.txt.gz", ["або", "вода", "привіт"])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    loader = DictionaryLoader(str(data))
    loader.load()
    assert loader.state == "ready"
    assert loader.contains_en("hello")
    assert loader.contains_uk("привіт")
    loader.close()

    cached = DictionaryLoader(str(data))
    cached._build_cache = lambda _name: (_ for _ in ()).throw(AssertionError("cache miss"))  # type: ignore[method-assign]
    cached.load()
    assert cached.state == "ready"
    assert cached.contains_en("hello")
    assert cached.contains_uk("привіт")
    cached.close()
