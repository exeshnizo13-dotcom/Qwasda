import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import qwasda
from qwasda import LANG_ENGLISH, LANG_UKRAINIAN, convert_phrase

_ENG_TO_SCAN = {c: s for s, c in qwasda.SCAN_ENG.items()}


def scans(eng_word: str, caps=None):
    if caps is None:
        caps = [False] * len(eng_word)
    return [(_ENG_TO_SCAN[c], caps[i]) for i, c in enumerate(eng_word)]


def test_convert_phrase_preserves_shifted_punctuation():
    phrase = [
        ("w", scans("ghbdsn")),
        ("s", (0x31, True)),
        ("w", scans("rfr")),
    ]
    segments, strip_len, target = convert_phrase(phrase, LANG_ENGLISH)
    assert target == LANG_UKRAINIAN
    assert strip_len == 6 + 1 + 3
    assert segments == [("text", "привіт"), ("sep", (0x31, True)), ("text", "как")]
