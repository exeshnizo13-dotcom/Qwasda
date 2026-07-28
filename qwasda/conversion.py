"""
Layout conversion logic for Qwasda.

Handles scan-code to text conversion for both layouts,
manual switching, and auto-correction target detection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from .dicts import DictionaryLoader
    from .learning import LearningManager

# Scan code mappings (physical key position -> character)
SCAN_ENG = {
    0x10: "q",
    0x11: "w",
    0x12: "e",
    0x13: "r",
    0x14: "t",
    0x15: "y",
    0x16: "u",
    0x17: "i",
    0x18: "o",
    0x19: "p",
    0x1E: "a",
    0x1F: "s",
    0x20: "d",
    0x21: "f",
    0x22: "g",
    0x23: "h",
    0x24: "j",
    0x25: "k",
    0x26: "l",
    0x2C: "z",
    0x2D: "x",
    0x2E: "c",
    0x2F: "v",
    0x30: "b",
    0x31: "n",
    0x32: "m",
}

SCAN_UKR = {
    0x10: "й",
    0x11: "ц",
    0x12: "у",
    0x13: "к",
    0x14: "е",
    0x15: "н",
    0x16: "г",
    0x17: "ш",
    0x18: "щ",
    0x19: "з",
    0x1A: "х",
    0x1B: "ї",
    0x1E: "ф",
    0x1F: "і",
    0x20: "в",
    0x21: "а",
    0x22: "п",
    0x23: "р",
    0x24: "о",
    0x25: "л",
    0x26: "д",
    0x27: "ж",
    0x28: "є",
    0x2C: "я",
    0x2D: "ч",
    0x2E: "с",
    0x2F: "м",
    0x30: "и",
    0x31: "т",
    0x32: "ь",
    0x33: "б",
    0x34: "ю",
    0x2B: "ґ",
}

# English chars at Ukrainian letter positions (punctuation keys)
ENG_AT_POS = dict(SCAN_ENG)
ENG_AT_POS.update(
    {
        0x1A: "[",
        0x1B: "]",
        0x27: ";",
        0x28: "'",
        0x33: ",",
        0x34: ".",
        0x2B: "\\",
    }
)

LETTER_SCANS = frozenset(SCAN_UKR.keys()) | frozenset(SCAN_ENG.keys())

# Valid single-letter words for auto-correction
UK_SINGLE_WORDS = frozenset("аійоуязєбжв")  # а і й о у я з є б ж в
EN_SINGLE_WORDS = frozenset("ai")  # a, i

# Word break keys
WORD_BREAK_VKS = frozenset({0x20, 0x0D, 0x09})  # Space, Enter, Tab
NAV_CLEAR_VKS = frozenset({0x25, 0x26, 0x27, 0x28, 0x1B, 0x24, 0x23, 0x21, 0x22, 0x2E})
OEM_PUNCT_VKS = frozenset(
    {
        0xBA,
        0xBB,
        0xBC,
        0xBD,
        0xBE,
        0xBF,
        0xC0,
        0xDB,
        0xDC,
        0xDD,
        0xDE,
    }
)

# Default thresholds (can be overridden by config)
MIN_AUTOCORRECT_LEN = 2
MIN_EN_TO_UK = 3

Scan = tuple[int, bool]
WordToken: TypeAlias = tuple[Literal["w"], list[Scan]]
SeparatorValue: TypeAlias = int | tuple[int, bool]
SeparatorToken: TypeAlias = tuple[Literal["s"], SeparatorValue]
PhraseToken: TypeAlias = WordToken | SeparatorToken
TextSegment: TypeAlias = tuple[Literal["text"], str]
SeparatorSegment: TypeAlias = tuple[Literal["sep"], SeparatorValue]
PhraseSegment: TypeAlias = TextSegment | SeparatorSegment

WORD_JOINERS = frozenset({"'", "’"})


@dataclass
class ScanBuffer:
    """Buffer of (scan_code, shifted) pairs for current word."""

    scans: list[Scan]

    def __init__(self) -> None:
        self.scans = []

    def add(self, scan: int, shifted: bool) -> None:
        self.scans.append((scan, shifted))

    def pop(self) -> None:
        if self.scans:
            self.scans.pop()

    def clear(self) -> None:
        self.scans.clear()

    def copy(self) -> list[Scan]:
        return list(self.scans)

    def __len__(self) -> int:
        return len(self.scans)

    def __bool__(self) -> bool:
        return bool(self.scans)


def _read_scans(scans: list[Scan], table: dict[int, str]) -> str:
    """Convert scan codes to string using given layout table."""
    out = []
    for sc, shifted in scans:
        ch = table.get(sc, "")
        out.append(ch.upper() if shifted else ch)
    return "".join(out)


def scans_to_ukr(scans: list[Scan]) -> str:
    return _read_scans(scans, SCAN_UKR)


def scans_to_eng(scans: list[Scan]) -> str:
    return _read_scans(scans, ENG_AT_POS)


def is_word_text(text: str) -> bool:
    """
    Return True for lexical words, including forms with internal apostrophes.

    Apostrophes are allowed only between alphabetic characters, so words like
    "п'ять" and "l'heure" stay whole, while leading/trailing punctuation does not.
    """
    if not text:
        return False

    prev_is_alpha = False
    saw_alpha = False
    pending_joiner = False

    for ch in text:
        if ch.isalpha():
            saw_alpha = True
            prev_is_alpha = True
            pending_joiner = False
            continue
        if ch in WORD_JOINERS and prev_is_alpha:
            prev_is_alpha = False
            pending_joiner = True
            continue
        return False

    return saw_alpha and not pending_joiner


def manual_target(scans: list[Scan], layout: int) -> tuple[str | None, int | None]:
    """
    Manual switch target: read same keys in OTHER layout.
    Returns (converted_text, target_layout) or (None, None).
    """
    from .win32 import LANG_ENGLISH, LANG_UKRAINIAN

    if not scans or layout not in (LANG_UKRAINIAN, LANG_ENGLISH):
        return None, None

    if layout == LANG_UKRAINIAN:
        return scans_to_eng(scans), LANG_ENGLISH
    return scans_to_ukr(scans), LANG_UKRAINIAN


def autocorrect_target(
    scans: list[Scan],
    layout: int,
    dict_loader: DictionaryLoader,
    learning: LearningManager,
    min_autocorrect_len: int,
    min_en_to_uk: int,
) -> tuple[str | None, int | None]:
    """
    Auto-correction target based on dictionaries.
    Returns (converted_text, target_layout) or (None, None).
    """
    from .win32 import LANG_ENGLISH, LANG_UKRAINIAN

    if not dict_loader.dicts_loaded or not scans:
        return None, None
    if layout not in (LANG_UKRAINIAN, LANG_ENGLISH):
        return None, None

    ukr = scans_to_ukr(scans)
    eng = scans_to_eng(scans)
    ukr_l, eng_l = ukr.lower(), eng.lower()

    # Single-letter words: only correct if valid in target layout
    if len(scans) == 1:
        if layout == LANG_UKRAINIAN:
            if ukr_l in learning.block_uk or ukr_l in UK_SINGLE_WORDS:
                return None, None
            if eng_l in EN_SINGLE_WORDS or eng_l in learning.force_en:
                return eng, LANG_ENGLISH
        else:
            if eng_l in learning.block_en or eng_l in EN_SINGLE_WORDS:
                return None, None
            if ukr_l in UK_SINGLE_WORDS or ukr_l in learning.force_uk:
                return ukr, LANG_UKRAINIAN
        return None, None

    if len(scans) < min_autocorrect_len:
        return None, None

    if layout == LANG_UKRAINIAN:
        if ukr_l in learning.block_uk:
            return None, None
        if dict_loader.contains_uk(ukr_l):
            return None, None
        if dict_loader.contains_en(eng_l) or eng_l in learning.force_en:
            return eng, LANG_ENGLISH
    else:
        if eng_l in learning.block_en:
            return None, None
        if dict_loader.contains_en(eng_l):
            return None, None
        if ukr_l in learning.force_uk or (
            len(scans) >= min_en_to_uk and dict_loader.contains_uk(ukr_l)
        ):
            return ukr, LANG_UKRAINIAN

    return None, None


def convert_phrase(
    phrase: list[PhraseToken],
    layout: int,
) -> tuple[list[PhraseSegment], int, int] | tuple[None, int, None]:
    """
    Convert entire phrase for manual switch.
    Returns (segments, strip_len, target_layout) or (None, 0, None).
    segments: list of ("text", str) or ("sep", (vk, shifted))
    """
    from .win32 import LANG_ENGLISH, LANG_UKRAINIAN

    if not phrase or layout not in (LANG_UKRAINIAN, LANG_ENGLISH):
        return None, 0, None

    to_eng = layout == LANG_UKRAINIAN
    target = LANG_ENGLISH if to_eng else LANG_UKRAINIAN
    segments: list[PhraseSegment] = []
    strip_len = 0
    has_word = False

    for tok in phrase:
        if tok[0] == "w":
            scans = tok[1]
            if not scans:
                continue
            text = scans_to_eng(scans) if to_eng else scans_to_ukr(scans)
            segments.append(("text", text))
            strip_len += len(scans)
            has_word = True
        else:  # separator
            segments.append(("sep", tok[1]))
            strip_len += 1

    if not has_word:
        return None, 0, None

    return segments, strip_len, target


def is_word_terminator(vk: int, shifted: bool) -> bool:
    """Check if key is a punctuation terminator that should trigger auto-correct."""
    if vk in OEM_PUNCT_VKS:
        return True
    return 0x30 <= vk <= 0x39 and shifted  # Shift+digit = !@#$%^&*()
