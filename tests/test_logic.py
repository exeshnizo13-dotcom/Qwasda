"""
Юніт-тести чистої логіки Qwasda (без Win32-вводу).

Покривають конвертацію scan-кодів між розкладками, словниковий
бінарний пошук (SortedWordIndex), автокорекцію та ручне перемикання.
Імпорт qwasda не встановлює клавіатурний хук — він лише в main().
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import qwasda  # noqa: E402
from qwasda import (  # noqa: E402
    LANG_ENGLISH,
    LANG_UKRAINIAN,
    VK_RETURN,
    VK_SPACE,
    CaretGuard,
    DoubleTapDetector,
    SortedWordIndex,
    autocorrect_target,
    convert_phrase,
    is_word_terminator,
    is_word_text,
    learn_block_word,
    learn_valid_word,
    manual_target,
    scans_to_eng,
    scans_to_ukr,
)

# Зворотна мапа «англ. літера → scan-код», щоб будувати буфери з рядків.
_ENG_TO_SCAN = {c: s for s, c in qwasda.SCAN_ENG.items()}


def scans(eng_word: str, caps=None):
    """
    Буфер scan-кодів для англійських літер слова.
    caps — необов'язковий список булів (Shift/Caps) тієї ж довжини.
    """
    if caps is None:
        caps = [False] * len(eng_word)
    return [(_ENG_TO_SCAN[c], caps[i]) for i, c in enumerate(eng_word)]


def wtok(eng_word: str, caps=None):
    """Токен-слово для phrase_tokens."""
    return ["w", scans(eng_word, caps)]


def stok(vk=VK_SPACE, shifted=False):
    """Токен-роздільник для phrase_tokens."""
    return ["s", (vk, shifted) if shifted else vk]


# ───────────────────────── Конвертація розкладок ──────────────────────────


def test_eng_reading_is_identity():
    assert scans_to_eng(scans("hello")) == "hello"


def test_ukr_reading_of_hello():
    # «руддщ» — як виглядає «hello» в укр. розкладці (приклад із README).
    assert scans_to_ukr(scans("hello")) == "руддщ"


def test_ukr_reading_of_pryvit():
    # ghbdsn → привіт (приклад із README).
    assert scans_to_ukr(scans("ghbdsn")) == "привіт"


def test_case_is_preserved():
    caps = [True] + [False] * 4  # перша літера велика
    assert scans_to_eng(scans("hello", caps)) == "Hello"
    assert scans_to_ukr(scans("hello", caps)) == "Руддщ"


def test_punctuation_positions_map_to_ukr_letters():
    # клавіша ';' (scan 0x27) в укр. розкладці — це 'ж'
    assert scans_to_ukr([(0x27, False)]) == "ж"
    assert scans_to_eng([(0x27, False)]) == ";"


def test_is_word_text_accepts_internal_apostrophe():
    assert is_word_text("п'ять") is True
    assert is_word_text("l’heure") is True


def test_is_word_text_rejects_edge_apostrophes():
    assert is_word_text("'слово") is False
    assert is_word_text("слово'") is False


# ───────────────────────── Ручне перемикання ──────────────────────────────


def test_manual_target_uk_to_en():
    conv, target = manual_target(scans("ghbdsn"), LANG_UKRAINIAN)
    assert conv == "ghbdsn"
    assert target == LANG_ENGLISH


def test_manual_target_en_to_uk():
    conv, target = manual_target(scans("ghbdsn"), LANG_ENGLISH)
    assert conv == "привіт"
    assert target == LANG_UKRAINIAN


def test_manual_target_ignores_other_layouts():
    assert manual_target(scans("hello"), 0x0419) == (None, None)  # рос.


def test_manual_target_empty_buffer():
    assert manual_target([], LANG_ENGLISH) == (None, None)


# ───────────────────────── SortedWordIndex ────────────────────────────────


def _index(words):
    # рядки мають бути відсортовані в байтовому порядку UTF-8
    data = "\n".join(sorted(words, key=lambda w: w.encode("utf-8")))
    return SortedWordIndex((data + "\n").encode("utf-8"))


def test_index_membership():
    idx = _index(["привіт", "слово", "тест"])
    assert "привіт" in idx
    assert "тест" in idx
    assert "немає" not in idx


def test_index_len():
    assert len(_index(["a", "b", "c"])) == 3


def test_index_empty():
    idx = SortedWordIndex(b"")
    assert len(idx) == 0
    assert "будь-що" not in idx


def test_index_handles_crlf():
    idx = SortedWordIndex(b"abc\r\nxyz\r\n")
    assert "abc" in idx
    assert "xyz" in idx


def test_index_adds_trailing_newline():
    idx = SortedWordIndex("привіт".encode())  # без \n у кінці
    assert "привіт" in idx


def test_index_rejects_oversized(monkeypatch):
    class _Huge:
        def __contains__(self, _):  # pragma: no cover
            return False

        def endswith(self, _):
            return True

        def __len__(self):
            return 2**32

        def replace(self, *a):
            return self

    with pytest.raises(ValueError):
        SortedWordIndex(_Huge())


# ───────────────────────── Автокорекція ───────────────────────────────────


@pytest.fixture
def dicts(tmp_path, monkeypatch):
    """Підставляє мінімальні словники й вмикає dicts_loaded."""
    # Use temp directory for learned words to avoid loading real data
    monkeypatch.setattr(qwasda, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(qwasda, "LEARNED_PATH", str(tmp_path / "learned.json"))
    monkeypatch.setattr(qwasda, "DICT_EN", frozenset({"hello", "the", "cat"}))
    monkeypatch.setattr(qwasda, "DICT_UK", _index(["привіт", "кіт", "так"]))
    monkeypatch.setattr(qwasda, "dicts_loaded", True)
    monkeypatch.setattr(qwasda, "MIN_AUTOCORRECT_LEN", 2)
    monkeypatch.setattr(qwasda, "MIN_EN_TO_UK", 3)


def _autocorrect_target(scans, layout):
    """Helper to call autocorrect_target with test fixtures."""
    from qwasda.dicts import DictionaryLoader
    from qwasda.learning import LearningManager

    dict_loader = DictionaryLoader(".")
    dict_loader.dict_en = qwasda.DICT_EN
    dict_loader.dict_uk = qwasda.DICT_UK
    dict_loader.dicts_loaded = qwasda.dicts_loaded

    config_manager = qwasda._get_config_manager()
    learning = LearningManager(config_manager)

    return autocorrect_target(
        scans, layout, dict_loader, learning, qwasda.MIN_AUTOCORRECT_LEN, qwasda.MIN_EN_TO_UK
    )


def test_autocorrect_uk_to_en(dicts):
    # На екрані «руддщ» (UK), але це «hello» в англ. читанні.
    conv, target = _autocorrect_target(scans("hello"), LANG_UKRAINIAN)
    assert conv == "hello"
    assert target == LANG_ENGLISH


def test_autocorrect_en_to_uk(dicts):
    # На екрані «ghbdsn» (EN), але це «привіт» в укр. читанні.
    conv, target = _autocorrect_target(scans("ghbdsn"), LANG_ENGLISH)
    assert conv == "привіт"
    assert target == LANG_UKRAINIAN


def test_autocorrect_leaves_valid_en_word(dicts):
    assert _autocorrect_target(scans("hello"), LANG_ENGLISH) == (None, None)


def test_autocorrect_leaves_valid_uk_word(dicts):
    # «привіт» валідне в UK — не чіпати.
    assert _autocorrect_target(scans("ghbdsn"), LANG_UKRAINIAN) == (None, None)


def test_autocorrect_respects_min_len(dicts):
    # «the» → укр. читання коротке; нижче MIN_AUTOCORRECT_LEN взагалі ігнор.
    assert _autocorrect_target(scans("h"), LANG_ENGLISH) == (None, None)


def test_autocorrect_en_to_uk_threshold(dicts, monkeypatch):
    # «ghbdsn» (6 літер) дає валідне укр. «привіт», але поріг MIN_EN_TO_UK=7
    # вищий за довжину слова — корекції бути не повинно.
    monkeypatch.setattr(qwasda, "MIN_EN_TO_UK", 7)
    assert _autocorrect_target(scans("ghbdsn"), LANG_ENGLISH) == (None, None)
    # А зі стандартним порогом 3 — виправляється.
    monkeypatch.setattr(qwasda, "MIN_EN_TO_UK", 3)
    assert _autocorrect_target(scans("ghbdsn"), LANG_ENGLISH) == ("привіт", LANG_UKRAINIAN)


def test_autocorrect_disabled_without_dicts(monkeypatch):
    monkeypatch.setattr(qwasda, "dicts_loaded", False)
    assert _autocorrect_target(scans("ghbdsn"), LANG_ENGLISH) == (None, None)


def test_autocorrect_ignores_other_layouts(dicts):
    assert _autocorrect_target(scans("ghbdsn"), 0x0419) == (None, None)


# ── Однолітерні слова (я, і, з … / a, i) ──


def test_autocorrect_single_uk_letter_en_to_uk(dicts):
    # «z» (EN) → «я» (валідне укр. однолітерне) → перемкнути в UK.
    assert _autocorrect_target(scans("z"), LANG_ENGLISH) == ("я", LANG_UKRAINIAN)
    # «s» (EN) → «і».
    assert _autocorrect_target(scans("s"), LANG_ENGLISH) == ("і", LANG_UKRAINIAN)


def test_autocorrect_single_preserves_case(dicts):
    conv, target = _autocorrect_target(scans("z", caps=[True]), LANG_ENGLISH)
    assert conv == "Я"
    assert target == LANG_UKRAINIAN


def test_autocorrect_single_valid_en_letter_untouched(dicts):
    # «a» та «i» — валідні англ. однолітерні, не чіпати.
    assert _autocorrect_target(scans("a"), LANG_ENGLISH) == (None, None)
    assert _autocorrect_target(scans("i"), LANG_ENGLISH) == (None, None)


def test_autocorrect_single_random_letter_untouched(dicts):
    # «h» (EN) → укр. «р» не є однолітерним словом → не чіпати.
    assert _autocorrect_target(scans("h"), LANG_ENGLISH) == (None, None)


def test_autocorrect_single_valid_uk_letter_untouched(dicts):
    # «я» набране в UK (клавіша «z») — валідне укр. однолітерне, лишити.
    assert _autocorrect_target(scans("z"), LANG_UKRAINIAN) == (None, None)


# ───────────────────────── Подвійний Ctrl (DoubleTapDetector) ─────────────

WIN = 0.4  # вікно подвійного тапу для тестів


def test_double_tap_fires_on_second_release():
    d = DoubleTapDetector()
    # тап 1: down, up @ t=1.0
    d.on_trigger_down()
    assert d.on_trigger_up(1.0, WIN) is False  # перший тап — ще не час
    # тап 2: down, up @ t=1.2 (у межах вікна)
    d.on_trigger_down()
    assert d.on_trigger_up(1.2, WIN) is True  # другий чистий тап — спрацювати


def test_double_tap_too_slow_does_not_fire():
    d = DoubleTapDetector()
    d.on_trigger_down()
    d.on_trigger_up(1.0, WIN)
    d.on_trigger_down()
    assert d.on_trigger_up(1.0 + WIN + 0.01, WIN) is False  # за межами вікна


def test_other_key_between_taps_breaks_chain():
    d = DoubleTapDetector()
    d.on_trigger_down()
    d.on_trigger_up(1.0, WIN)
    d.on_other_key()  # натиснули літеру між тапами
    d.on_trigger_down()
    assert d.on_trigger_up(1.1, WIN) is False


def test_ctrl_plus_key_is_not_a_tap():
    # Ctrl+C: Ctrl down, потім C (інша клавіша), потім Ctrl up — не тап.
    d = DoubleTapDetector()
    d.on_trigger_down()
    d.on_other_key()  # 'C' поки Ctrl утиснутий
    assert d.on_trigger_up(1.0, WIN) is False
    # навіть наступний чистий тап одразу не має спрацювати (ланцюг порожній)
    d.on_trigger_down()
    assert d.on_trigger_up(1.05, WIN) is False


def test_autorepeat_down_is_ignored():
    # Утримання Ctrl шле багато down без up — не має псувати перший тап.
    d = DoubleTapDetector()
    d.on_trigger_down()
    d.on_trigger_down()  # auto-repeat
    d.on_trigger_down()
    assert d.on_trigger_up(1.0, WIN) is False  # завершився перший тап
    d.on_trigger_down()
    assert d.on_trigger_up(1.2, WIN) is True


def test_triple_tap_fires_once_then_needs_new_pair():
    d = DoubleTapDetector()
    d.on_trigger_down()
    d.on_trigger_up(1.0, WIN)
    d.on_trigger_down()
    assert d.on_trigger_up(1.1, WIN) is True  # 2-й тап спрацював
    d.on_trigger_down()
    assert d.on_trigger_up(1.2, WIN) is False  # 3-й — нова пара лише починається


# ───────────────────────── Конфіг (персистентність) ───────────────────────


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    monkeypatch.setattr(qwasda, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(qwasda, "CONFIG_PATH", str(p))
    return p


def test_config_round_trip(cfg_path, monkeypatch):
    monkeypatch.setattr(qwasda, "enabled", False)
    monkeypatch.setattr(qwasda, "auto_correct_enabled", False)
    monkeypatch.setattr(qwasda, "MIN_EN_TO_UK", 5)
    qwasda.save_config()
    # повертаємо «дефолти», тоді читаємо з файлу
    monkeypatch.setattr(qwasda, "enabled", True)
    monkeypatch.setattr(qwasda, "auto_correct_enabled", True)
    monkeypatch.setattr(qwasda, "MIN_EN_TO_UK", 3)
    qwasda.load_config()
    assert qwasda.enabled is False
    assert qwasda.auto_correct_enabled is False
    assert qwasda.MIN_EN_TO_UK == 5


def test_load_config_missing_file_is_noop(cfg_path, monkeypatch):
    monkeypatch.setattr(qwasda, "enabled", True)
    qwasda.load_config()  # файлу нема — нічого не падає
    assert qwasda.enabled is True


def test_load_config_ignores_garbage(cfg_path, monkeypatch):
    cfg_path.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(qwasda, "enabled", True)
    qwasda.load_config()
    assert qwasda.enabled is True


def test_load_config_rejects_bad_types(cfg_path, monkeypatch):
    cfg_path.write_text('{"enabled": "yes", "min_en_to_uk": -1}', encoding="utf-8")
    monkeypatch.setattr(qwasda, "enabled", True)
    monkeypatch.setattr(qwasda, "MIN_EN_TO_UK", 3)
    qwasda.load_config()
    assert qwasda.enabled is True  # рядок замість bool — проігноровано
    assert qwasda.MIN_EN_TO_UK == 3  # від'ємне число — проігноровано


# ───────────────────────── Перемикання цілої фрази ────────────────────────


def test_convert_phrase_en_to_uk_multiword():
    # «ghbdsn ghbdsn» (EN) → дві «привіт» через пробіл, перемикання в UK.
    phrase = [wtok("ghbdsn"), stok(VK_SPACE), wtok("ghbdsn")]
    segments, strip_len, target = convert_phrase(phrase, LANG_ENGLISH)
    assert target == LANG_UKRAINIAN
    assert strip_len == 6 + 1 + 6
    assert segments == [("text", "привіт"), ("sep", VK_SPACE), ("text", "привіт")]


def test_convert_phrase_uk_to_en_single():
    phrase = [wtok("ghbdsn")]
    segments, strip_len, target = convert_phrase(phrase, LANG_UKRAINIAN)
    assert target == LANG_ENGLISH
    assert strip_len == 6
    assert segments == [("text", "ghbdsn")]


def test_convert_phrase_preserves_case_and_newline():
    caps = [True] + [False] * 4
    phrase = [wtok("hello", caps), stok(VK_RETURN), wtok("cat")]
    segments, strip_len, target = convert_phrase(phrase, LANG_ENGLISH)
    assert segments[0] == ("text", "Руддщ")  # регістр збережено
    assert segments[1] == ("sep", VK_RETURN)  # Enter лишається роздільником
    assert strip_len == 5 + 1 + 3


def test_convert_phrase_no_words_returns_none():
    assert convert_phrase([stok(VK_SPACE)], LANG_ENGLISH) == (None, 0, None)
    assert convert_phrase([], LANG_ENGLISH) == (None, 0, None)


def test_convert_phrase_ignores_other_layouts():
    assert convert_phrase([wtok("ghbdsn")], 0x0419) == (None, 0, None)


# ───────────────────── Пунктуація-термінатор автокорекції ──────────────────


def test_word_terminator_oem_punct():
    assert is_word_terminator(0xBF, False) is True  # «/»
    assert is_word_terminator(0xBF, True) is True  # «?»
    assert is_word_terminator(0xBE, False) is True  # «.»


def test_word_terminator_shifted_digit_only():
    assert is_word_terminator(0x31, True) is True  # «!» (Shift+1)
    assert is_word_terminator(0x31, False) is False  # звичайна «1» — частина слова


def test_word_terminator_ignores_non_punct():
    assert is_word_terminator(0x2E, False) is False  # VK_DELETE — не термінатор
    assert is_word_terminator(0x41, False) is False  # «A»


# ───────────────────────── Пам'ять: FORCE / BLOCK ─────────────────────────


@pytest.fixture
def learned(monkeypatch):
    """Свіжі (порожні) множини вивчених слів для кожного тесту."""
    fe, fu, bu, be = set(), set(), set(), set()
    monkeypatch.setattr(qwasda, "FORCE_EN", fe)
    monkeypatch.setattr(qwasda, "FORCE_UK", fu)
    monkeypatch.setattr(qwasda, "BLOCK_UK", bu)
    monkeypatch.setattr(qwasda, "BLOCK_EN", be)
    return fe, fu, bu, be


def test_force_en_enables_autocorrect(dicts, learned):
    # «qwert» (UK-набране) не в жодному словнику → без пам'яті не чіпається.
    assert _autocorrect_target(scans("qwert"), LANG_UKRAINIAN) == (None, None)
    # Але вивчене як валідне EN → перемикається в англійську.
    qwasda.FORCE_EN.add("qwert")
    assert _autocorrect_target(scans("qwert"), LANG_UKRAINIAN) == ("qwert", LANG_ENGLISH)


def test_force_uk_bypasses_len_threshold(dicts, learned, monkeypatch):
    monkeypatch.setattr(qwasda, "MIN_EN_TO_UK", 7)  # звичайні слова не пройшли б
    qwasda.FORCE_UK.add("ко")  # укр. читання «rj»
    conv, target = _autocorrect_target(scans("rj"), LANG_ENGLISH)
    assert conv == "ко"
    assert target == LANG_UKRAINIAN


def test_block_uk_prevents_autocorrect(dicts, learned):
    # «руддщ» (UK) звично перемкнулось би в «hello» (валідне EN).
    assert _autocorrect_target(scans("hello"), LANG_UKRAINIAN) == ("hello", LANG_ENGLISH)
    qwasda.BLOCK_UK.add("руддщ")
    assert _autocorrect_target(scans("hello"), LANG_UKRAINIAN) == (None, None)


def test_block_en_prevents_autocorrect(dicts, learned):
    # «ghbdsn» (EN) звично перемкнулось би в «привіт» (валідне UK).
    assert _autocorrect_target(scans("ghbdsn"), LANG_ENGLISH) == ("привіт", LANG_UKRAINIAN)
    qwasda.BLOCK_EN.add("ghbdsn")
    assert _autocorrect_target(scans("ghbdsn"), LANG_ENGLISH) == (None, None)


def test_learn_valid_word_dedupes(learned):
    fe = qwasda.FORCE_EN
    assert learn_valid_word("qwert", LANG_ENGLISH) is True
    assert learn_valid_word("qwert", LANG_ENGLISH) is False  # повтор — без змін
    assert "qwert" in fe
    assert learn_valid_word("привіт", LANG_UKRAINIAN) is True
    assert "привіт" in qwasda.FORCE_UK


def test_learn_block_word_targets_right_set(learned):
    assert learn_block_word("руддщ", LANG_UKRAINIAN) is True
    assert "руддщ" in qwasda.BLOCK_UK
    assert learn_block_word("ghbdsn", LANG_ENGLISH) is True
    assert "ghbdsn" in qwasda.BLOCK_EN


# ───────────────────────── Пам'ять: персистентність ───────────────────────


@pytest.fixture
def learned_path(tmp_path, monkeypatch):
    p = tmp_path / "learned.json"
    monkeypatch.setattr(qwasda, "APP_DIR", str(tmp_path))
    monkeypatch.setattr(qwasda, "LEARNED_PATH", str(p))
    return p


def test_learned_round_trip(learned, learned_path):
    qwasda.FORCE_EN.update({"qwert", "asdf"})
    qwasda.BLOCK_EN.add("ghbdsn")
    qwasda.save_learned()
    # «забуваємо» в пам'яті, тоді читаємо з файлу
    qwasda.FORCE_EN.clear()
    qwasda.BLOCK_EN.clear()
    qwasda.load_learned()
    assert {"qwert", "asdf"} == qwasda.FORCE_EN
    assert {"ghbdsn"} == qwasda.BLOCK_EN


def test_load_learned_missing_file_is_noop(learned, learned_path):
    qwasda.FORCE_EN.add("keep")
    qwasda.load_learned()  # файлу нема — нічого не падає
    assert "keep" in qwasda.FORCE_EN


def test_load_learned_ignores_garbage(learned, learned_path):
    learned_path.write_text("{ not json", encoding="utf-8")
    qwasda.FORCE_EN.add("keep")
    qwasda.load_learned()
    assert "keep" in qwasda.FORCE_EN


def test_forget_learned_clears_all(learned, learned_path):
    qwasda.FORCE_EN.add("qwert")
    qwasda.BLOCK_UK.add("руддщ")
    qwasda.forget_learned()
    assert not qwasda.FORCE_EN
    assert not qwasda.BLOCK_UK


# ──────────────────────── Редагування: CaretGuard ─────────────────────────


class TestCaretGuard:
    """Детектор редагування — запобігає автокорекції на фрагментах слів."""

    def test_init_not_suppressed(self):
        guard = CaretGuard()
        assert guard.suppressed is False

    def test_on_nav_sets_suppressed(self):
        guard = CaretGuard()
        guard.on_nav()
        assert guard.suppressed is True

    def test_on_word_break_returns_suppressed_state(self):
        guard = CaretGuard()
        guard.on_nav()
        result = guard.on_word_break()
        assert result is True
        assert guard.suppressed is False  # скинулася після повернення

    def test_on_word_break_without_nav_returns_false(self):
        guard = CaretGuard()
        result = guard.on_word_break()
        assert result is False
        assert guard.suppressed is False

    def test_multiple_on_nav_then_one_word_break(self):
        """Кілька nav, потім одна межа слова — результат True один раз."""
        guard = CaretGuard()
        guard.on_nav()
        guard.on_nav()
        result = guard.on_word_break()
        assert result is True

    def test_after_word_break_next_nav_resets(self):
        """Після межи слова, nav знову ставить прапорець."""
        guard = CaretGuard()
        guard.on_nav()
        guard.on_word_break()
        assert guard.suppressed is False
        guard.on_nav()
        assert guard.suppressed is True

    def test_on_focus_change_clears_suppressed(self):
        """Зміна вікна скидає прапорець."""
        guard = CaretGuard()
        guard.on_nav()
        assert guard.suppressed is True
        guard.on_focus_change()
        assert guard.suppressed is False

    def test_focus_change_clears_even_without_nav(self):
        """Зміна вікна — це все одно нап, теоретично без попередньої."""
        guard = CaretGuard()
        guard.on_focus_change()
        assert guard.suppressed is False
