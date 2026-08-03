from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path


def test_dictionary_manifest_matches_bundled_sources() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "data" / "dictionary-manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["dictionaries"].items():
        source = root / "data" / name
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        with gzip.open(source, "rt", encoding="utf-8") as handle:
            words = sum(1 for line in handle if line.strip())
        assert digest == expected["sha256"]
        assert words == expected["words"]
