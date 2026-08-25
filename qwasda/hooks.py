"""
Low-level keyboard and mouse hooks for Qwasda.

Implements:
- Keyboard LL hook: captures keystrokes, manages buffers, triggers corrections
- Mouse LL hook: clears buffers on click (focus change)
- DoubleTapDetector: recognizes double-Ctrl tap for manual switch
- CaretGuard: suppresses auto-correct after navigation keys
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import Config
    from .dicts import DictionaryLoader
    from .learning import LearningManager
    from .statistics import StatisticsManager

from .conversion import LETTER_SCANS, PhraseSegment, PhraseToken, Scan
from .win32 import (
    ALT_VKS,
    CTRL_VKS,
    DOWN_MSGS,
    HOOKPROC,
    KBDLLHOOKSTRUCT,
    LANG_ENGLISH,
    LANG_UKRAINIAN,
    LLKHF_INJECTED,
    LLMHF_INJECTED,
    MODIFIER_VKS,
    MOUSE_DOWN_MSGS,
    MSLLHOOKSTRUCT,
    NAV_CLEAR_VKS,
    UP_MSGS,
    VK_BACK,
    VK_CAPITAL,
    VK_RETURN,
    VK_SHIFT,
    VK_SPACE,
    VK_TAB,
    WIN_VKS,
    WH_KEYBOARD_LL,
    WH_MOUSE_LL,
    WORD_BREAK_VKS,
    send_backspaces,
    send_key,
    send_key_shifted,
    send_unicode_string,
    set_foreground_layout,
    user32,
)


def _shortcut_modifier_family(vk: int) -> str | None:
    """Normalize left/right modifier variants for reliable key-up tracking."""
    if vk in CTRL_VKS:
        return "ctrl"
    if vk in ALT_VKS:
        return "alt"
    if vk in WIN_VKS:
        return "win"
    return None


def _call_next_hook(hook: ctypes.c_void_p | None, n_code: int, w_param: int, l_param: int) -> int:
    """Call the next Windows hook and normalize the untyped ctypes result."""
    return int(user32.CallNextHookEx(hook, n_code, w_param, l_param))


# =============================================================================
# Double-Tap Detector
# =============================================================================


class DoubleTapDetector:
    """
    Detects double-tap of trigger key (Ctrl).
    Clean tap = key down + up with NO other keys pressed in between.
    Fires on SECOND key UP (so physical key is released before correction).
    """

    __slots__ = ("_last_tap", "_down", "_interrupted", "_trigger_vks")

    def __init__(self, trigger_vks: frozenset[int] = CTRL_VKS):
        self._last_tap = 0.0
        self._down = False
        self._interrupted = True
        self._trigger_vks = trigger_vks

    def on_trigger_down(self) -> None:
        if not self._down:
            self._down = True
            self._interrupted = False

    def on_trigger_up(self, now: float, window: float) -> bool:
        """
        Returns True if this is the second clean tap within window.
        """
        self._down = False
        if self._interrupted:
            self._last_tap = 0.0
            return False
        if self._last_tap and now - self._last_tap < window:
            self._last_tap = 0.0
            return True
        self._last_tap = now
        return False

    def on_other_key(self) -> None:
        """Any other key dirties the current tap."""
        self._interrupted = True
        self._last_tap = 0.0

    def is_trigger(self, vk: int) -> bool:
        return vk in self._trigger_vks

    def set_trigger_vks(self, trigger_vks: frozenset[int]) -> None:
        """Replace the configured double-tap modifiers and reset partial state."""
        self._trigger_vks = trigger_vks
        self._last_tap = 0.0
        self._down = False
        self._interrupted = True


# =============================================================================
# Caret Guard (suppresses auto-correct after navigation)
# =============================================================================


class CaretGuard:
    """
    After navigation keys (arrows, Home, End, etc.), the next word fragment
    might be editing existing text. Suppress auto-correct until word boundary.
    """

    __slots__ = ("_suppressed",)

    def __init__(self) -> None:
        self._suppressed = False

    def on_nav(self) -> None:
        self._suppressed = True

    def on_word_break(self) -> bool:
        """Returns True if suppression was active; clears it."""
        result = self._suppressed
        self._suppressed = False
        return result

    def on_focus_change(self) -> None:
        self._suppressed = False

    @property
    def suppressed(self) -> bool:
        return self._suppressed


# =============================================================================
# Phrase Buffer (for manual multi-word conversion)
# =============================================================================


class PhraseBuffer:
    """
    Accumulates words and separators for full-phrase manual conversion.
    Survives word boundaries; cleared on auto-correct, navigation, hotkeys.
    """

    def __init__(self, max_tokens: int = 400):
        self.tokens: list[PhraseToken] = []
        self.max_tokens = max_tokens

    def add_letter(self, scan: int, shifted: bool) -> None:
        if self.tokens and self.tokens[-1][0] == "w":
            self.tokens[-1][1].append((scan, shifted))
        else:
            self.tokens.append(("w", [(scan, shifted)]))
        self._trim()

    def add_sep(self, vk: int, shifted: bool = False) -> None:
        if self.tokens:
            self.tokens.append(("s", (vk, shifted)))

    def backspace(self) -> None:
        if not self.tokens:
            return
        last = self.tokens[-1]
        if last[0] == "w":
            if last[1]:
                last[1].pop()
            if not last[1]:
                self.tokens.pop()
        else:
            self.tokens.pop()

    def clear(self) -> None:
        self.tokens.clear()

    def copy(self) -> list[PhraseToken]:
        copied: list[PhraseToken] = []
        for token in self.tokens:
            if token[0] == "w":
                copied.append(("w", list(token[1])))
            else:
                copied.append(("s", token[1]))
        return copied

    def has_words(self) -> bool:
        return any(t[0] == "w" and t[1] for t in self.tokens)

    def get_last_word_scans(self) -> list[Scan] | None:
        """Return the scans of the last word token, or None."""
        if self.tokens and self.tokens[-1][0] == "w":
            return list(self.tokens[-1][1])
        return None

    def _trim(self) -> None:
        if len(self.tokens) > self.max_tokens:
            del self.tokens[: self.max_tokens // 2]


# =============================================================================
# Correction Worker (runs off hook thread)
# =============================================================================


@dataclass
class CorrectionTask:
    """Task for background correction worker."""

    scans: list[tuple[int, bool]]
    layout: int
    sep_vk: int
    sep_shifted: bool
    seq: int
    is_manual: bool = False
    phrase: list[PhraseToken] | None = None
    input_reserved: bool = False
    separator_suppressed: bool = False


class CorrectionWorker:
    """
    Serializes all SendInput operations off the LL hook thread.
    Hook only enqueues tasks; worker processes them sequentially.
    """

    def __init__(
        self,
        dict_loader: DictionaryLoader,
        learning: LearningManager,
        config: Config,
        get_layout_func: Callable[[bool], int],
        statistics: StatisticsManager | None = None,
    ):
        self.dict_loader = dict_loader
        self.learning = learning
        self.config = config
        self.get_layout = get_layout_func
        self.statistics = statistics

        self._queue: queue.Queue[CorrectionTask | None] = queue.Queue()
        # Correction paths update undo state while already holding this lock.
        # An RLock keeps those nested state transitions from deadlocking the
        # worker after the first batched or manual conversion.
        self._lock = threading.RLock()
        # Once a real word boundary is queued, hold subsequent physical input
        # until that word has been checked.  This makes the boundary a strict
        # ordering point: the second word cannot overtake correction of the
        # first one merely because the background thread was scheduled late.
        self._input_gate = threading.Lock()
        self._correcting = False
        self._input_seq = 0
        self._pending_corrections: list[tuple[int, str, int, int, bool]] = []
        # A phrase may begin with a word that is valid in both layouts (for
        # example ``ye`` in English and ``ну`` in Ukrainian).  Keep that
        # unresolved prefix until a later unambiguous correction or an actual
        # layout change supplies context, then rewrite the prefix as one batch.
        self._deferred_prefix: list[tuple[int, str, int, int, bool]] = []
        self._deferred_source_layout: int | None = None
        self._deferred_target_layout: int | None = None
        self._deferred_has_source_mismatch = False
        self._last_autocorrect: tuple[list[Scan], int, int, str, int, bool] | None = None
        self._autocorrect_undo_available = False

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def enqueue(self, task: CorrectionTask) -> None:
        self._queue.put(task)

    def increment_seq(self) -> int:
        with self._input_gate:
            with self._lock:
                self._input_seq += 1
                return self._input_seq

    def reserve_input(self) -> None:
        """Pause later physical key-down events until a boundary task finishes."""
        self._input_gate.acquire()

    def release_input(self) -> None:
        """Release a boundary reservation acquired by the keyboard hook."""
        self._input_gate.release()

    def get_seq(self) -> int:
        with self._lock:
            return self._input_seq

    def clear_autocorrect_undo(self) -> None:
        with self._lock:
            self._autocorrect_undo_available = False
            self._last_autocorrect = None

    def clear_pending(self) -> None:
        """Discard confirmed corrections and unresolved phrase context."""
        with self._lock:
            self._pending_corrections.clear()
            self._clear_deferred_prefix()

    def _clear_deferred_prefix(self) -> None:
        self._deferred_prefix.clear()
        self._deferred_source_layout = None
        self._deferred_target_layout = None
        self._deferred_has_source_mismatch = False

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:  # Shutdown sentinel
                break
            try:
                if task.is_manual:
                    self._do_manual(task)
                else:
                    self._do_auto(task)
            except Exception:
                pass  # Log in production
            finally:
                try:
                    if task.input_reserved:
                        self.release_input()
                finally:
                    self._queue.task_done()

    def shutdown(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=1.0)

    # -------------------------------------------------------------------------
    # Auto-correction
    # -------------------------------------------------------------------------

    def _do_auto(self, task: CorrectionTask) -> None:
        from .conversion import autocorrect_replacement

        with self._lock:
            if self._correcting:
                return
            self._correcting = True

            # A task with a word is queued from the punctuation/space key-down,
            # so its separator still needs a moment to reach the target app.
            # An empty task represents a following separator (for example the
            # space after ``Здається,``); it is already visible by the time the
            # worker reaches it.  Waiting again let the second word start and
            # unnecessarily postponed correction until that word's boundary.
            if task.scans and not task.separator_suppressed:
                time.sleep(0.03)

            # Use the source layout captured by the hook at the word boundary.
            # The user can switch layouts while this task waits in the queue;
            # probing here would then reinterpret the already-visible word as
            # if it had been typed in the new layout and skip its correction.
            layout = task.layout

            converted, target = autocorrect_replacement(
                task.scans,
                layout,
                self.dict_loader,
                self.learning,
                self.config.min_autocorrect_len,
                self.config.min_en_to_uk,
            )

            if self._defer_or_confirm_prefix(task, converted, target):
                self._correcting = False
                return

            if not converted and not self._pending_corrections:
                if task.separator_suppressed:
                    self._send_sep(task.sep_vk, task.sep_shifted)
                self._correcting = False
                return

            # Race check: did user type more?
            if self._input_seq != task.seq:
                if converted is not None and target is not None:
                    self._pending_corrections.append(
                        (len(task.scans), converted, target, task.sep_vk, task.sep_shifted)
                    )
                elif self._pending_corrections:
                    # Keep the pending range contiguous.  Once the first word
                    # establishes the intended layout, an unknown/short word
                    # between it and the eventual pause still has to be
                    # included in the later batch replacement.  Dropping the
                    # range here used to leave text such as ``ntgth ws`` on
                    # screen and only switch the layout for the next word.
                    pending_target = self._pending_corrections[-1][2]
                    passthrough = self._scans_to_layout(task.scans, pending_target)
                    self._pending_corrections.append(
                        (
                            len(task.scans),
                            passthrough,
                            pending_target,
                            task.sep_vk,
                            task.sep_shifted,
                        )
                    )
                else:
                    self._pending_corrections.clear()
                self._correcting = False
                return

            sep_len = 1 if task.sep_vk and not task.separator_suppressed else 0
            pending = list(self._pending_corrections)
            self._pending_corrections.clear()

            # Clear phrase buffer - auto-correct "settles" the text
            # (handled by engine)

            if converted is None or target is None:
                # Current word OK, but have pending - apply them
                pending_target = pending[-1][2]
                cur_text = self._scans_to_layout(task.scans, pending_target)
                self._replace_batch(
                    pending,
                    len(task.scans),
                    cur_text,
                    pending_target,
                    task.sep_vk,
                    task.sep_shifted,
                    not task.separator_suppressed,
                )
                self.clear_autocorrect_undo()
            elif pending:
                self._replace_batch(
                    pending,
                    len(task.scans),
                    converted,
                    target,
                    task.sep_vk,
                    task.sep_shifted,
                    not task.separator_suppressed,
                )
                self.clear_autocorrect_undo()
                if self.statistics:
                    self.statistics.record_layout_switch()
                    self.statistics.record_autocorrection(len(pending) + 1)
            else:
                self._replace_single(
                    len(task.scans) + sep_len, converted, target, task.sep_vk, task.sep_shifted
                )
                # Enable undo for single auto-correct
                if self.config.learning_enabled:
                    self._last_autocorrect = (
                        list(task.scans),
                        layout,
                        target,
                        converted,
                        task.sep_vk,
                        task.sep_shifted,
                    )
                    self._autocorrect_undo_available = True
                else:
                    self.clear_autocorrect_undo()
                if self.statistics:
                    self.statistics.record_layout_switch()
                    self.statistics.record_autocorrection()

            self._correcting = False

    def _replace_single(
        self, strip_len: int, text: str, target_layout: int, sep_vk: int, sep_shifted: bool
    ) -> None:
        send_backspaces(strip_len)
        time.sleep(0.02)
        set_foreground_layout(target_layout)
        time.sleep(0.02)
        send_unicode_string(text)
        self._send_sep(sep_vk, sep_shifted)

    def _replace_batch(
        self,
        pending: list[tuple[int, str, int, int, bool]],
        cur_len: int,
        cur_text: str,
        cur_target: int,
        cur_sep_vk: int,
        cur_sep_shifted: bool,
        cur_sep_visible: bool = True,
    ) -> None:
        total_bs = (1 if cur_sep_vk and cur_sep_visible else 0) + cur_len
        for orig_len, _, _, psep_vk, _ in pending:
            total_bs += orig_len + (1 if psep_vk else 0)

        send_backspaces(total_bs)
        time.sleep(0.02)
        set_foreground_layout(cur_target)
        time.sleep(0.02)

        for _, ptext, _, psep_vk, psep_shifted in pending:
            send_unicode_string(ptext)
            self._send_sep(psep_vk, psep_shifted)

        send_unicode_string(cur_text)
        self._send_sep(cur_sep_vk, cur_sep_shifted)

    def _send_sep(self, vk: int, shifted: bool) -> None:
        if vk == 0x20:  # Space
            send_unicode_string(" ")
        elif vk in (0x0D, 0x09):  # Enter, Tab
            time.sleep(0.005)
            send_key(vk)
        elif vk:
            time.sleep(0.005)
            send_key_shifted(vk, shifted)

    def _scans_to_layout(self, scans: list[tuple[int, bool]], layout: int) -> str:
        from .conversion import scans_to_eng, scans_to_ukr

        return scans_to_ukr(scans) if layout == LANG_UKRAINIAN else scans_to_eng(scans)

    # -------------------------------------------------------------------------
    # Manual conversion
    # -------------------------------------------------------------------------

    def _do_manual(self, task: CorrectionTask) -> None:
        from .conversion import convert_phrase

        with self._lock:
            if self._correcting:
                return
            self._correcting = True
            self.clear_pending()

            # Auto-correct undo takes precedence over the phrase buffer.  The
            # buffer deliberately spans word boundaries and therefore still
            # contains the original scans after an auto-correction.  Converting
            # it first would rewrite the whole phrase instead of restoring the
            # last corrected word.
            if self._autocorrect_undo_available and self._last_autocorrect:
                self._undo_autocorrect()
                self._correcting = False
                return

            layout = self.get_layout(True)
            phrase = task.phrase or []

            if phrase:
                result = convert_phrase(phrase, layout)
                if result[0] is not None:
                    segments, strip_len, target = result
                    self._replace_phrase(segments, strip_len, target)
                    self.clear_autocorrect_undo()
                    if self.statistics:
                        self.statistics.record_layout_switch()
                        self.statistics.record_manual_conversion()
                    # Learn words if enabled
                    if self.config.learning_enabled:
                        self._learn_from_phrase(phrase, layout, target)
                    self._correcting = False
                    return

            self._correcting = False

    def _defer_or_confirm_prefix(
        self,
        task: CorrectionTask,
        converted: str | None,
        target: int | None,
    ) -> bool:
        """Resolve ambiguous phrase starts using later layout context.

        Returns True when the task was stored as unresolved context and needs
        no immediate replacement.  On confirmation, the stored prefix is
        promoted to the normal batch-replacement path and False is returned.
        """
        from .conversion import scans_to_eng, scans_to_ukr

        # Confirmed/race-delayed corrections already define the direction;
        # let the existing contiguous batch logic handle the current task.
        if self._pending_corrections and not self._deferred_prefix:
            return False

        if self._deferred_prefix:
            source_layout = self._deferred_source_layout
            target_layout = self._deferred_target_layout
            if source_layout is None or target_layout is None:
                self._clear_deferred_prefix()
                return False

            # A real word in the opposite layout confirms how the unresolved
            # leading fragment should be read.  An unambiguous auto-correction
            # in that same direction is equally strong evidence.
            # A layout change alone can be an intentional mixed-language
            # phrase (``hello, світ``).  Only use it as confirmation if the
            # retained prefix also contains a word invalid in its source
            # layout.  An unambiguous autocorrection is sufficient by itself.
            layout_confirms = (
                task.layout == target_layout
                and bool(task.scans)
                and self._deferred_has_source_mismatch
            )
            correction_confirms = converted is not None and target == target_layout
            if layout_confirms or correction_confirms:
                self._pending_corrections = (
                    list(self._deferred_prefix) + self._pending_corrections
                )
                self._clear_deferred_prefix()
                return False

            # Separators after a physical layout switch still lie between the
            # prefix and the confirming word, so keep them in the replacement
            # span.  The next real target-layout word will confirm it.
            if task.layout == target_layout and not task.scans and task.sep_vk:
                self._deferred_prefix.append((0, "", target_layout, task.sep_vk, task.sep_shifted))
                return True

            if task.layout == source_layout and converted is None:
                if task.sep_vk in (VK_RETURN, VK_TAB):
                    self._clear_deferred_prefix()
                    return False
                text = self._scans_to_layout(task.scans, target_layout)
                if task.scans:
                    if source_layout == LANG_ENGLISH:
                        source = scans_to_eng(task.scans).lower()
                        source_is_valid = self.dict_loader.contains_en(source)
                    else:
                        source = scans_to_ukr(task.scans).lower()
                        source_is_valid = self.dict_loader.contains_uk(source)
                    self._deferred_has_source_mismatch |= not source_is_valid
                self._deferred_prefix.append(
                    (len(task.scans), text, target_layout, task.sep_vk, task.sep_shifted)
                )
                # Bound both retained text and eventual backspace range.
                if len(self._deferred_prefix) > 64:
                    self._clear_deferred_prefix()
                return True

            self._clear_deferred_prefix()
            return False

        if converted is not None or not task.scans or task.sep_vk in (VK_RETURN, VK_TAB):
            return False
        if not self.dict_loader.dicts_loaded:
            return False

        if task.layout == LANG_ENGLISH:
            source = scans_to_eng(task.scans).lower()
            alternative = scans_to_ukr(task.scans).lower()
            if source in self.learning.block_en:
                return False
            ambiguous = self.dict_loader.contains_en(source) and self.dict_loader.contains_uk(
                alternative
            )
            target_layout = LANG_UKRAINIAN
        elif task.layout == LANG_UKRAINIAN:
            source = scans_to_ukr(task.scans).lower()
            alternative = scans_to_eng(task.scans).lower()
            if source in self.learning.block_uk:
                return False
            ambiguous = self.dict_loader.contains_uk(source) and self.dict_loader.contains_en(
                alternative
            )
            target_layout = LANG_ENGLISH
        else:
            return False

        if not ambiguous:
            return False

        self._deferred_source_layout = task.layout
        self._deferred_target_layout = target_layout
        self._deferred_prefix.append(
            (
                len(task.scans),
                self._scans_to_layout(task.scans, target_layout),
                target_layout,
                task.sep_vk,
                task.sep_shifted,
            )
        )
        return True

    def _replace_phrase(self, segments: list[PhraseSegment], strip_len: int, target: int) -> None:
        send_backspaces(strip_len)
        time.sleep(0.02)
        set_foreground_layout(target)
        time.sleep(0.02)
        for kind, val in segments:
            if kind == "text":
                assert isinstance(val, str)
                send_unicode_string(val)
            else:
                if isinstance(val, tuple):
                    self._send_sep(*val)
                else:
                    assert isinstance(val, int)
                    self._send_sep(val, False)

    def _learn_from_phrase(
        self, phrase: list[PhraseToken], from_layout: int, to_layout: int
    ) -> None:
        from .conversion import scans_to_eng, scans_to_ukr

        changed = False
        for tok in phrase:
            if tok[0] != "w" or len(tok[1]) < self.config.min_autocorrect_len:
                continue
            scans = tok[1]
            if from_layout == LANG_UKRAINIAN:
                tgt = scans_to_eng(scans).lower()
            else:
                tgt = scans_to_ukr(scans).lower()
            from .conversion import is_word_text

            if not is_word_text(tgt):
                continue
            if self.learning.learn_valid_word(tgt, to_layout):
                changed = True
        if changed:
            self.learning.save()

    def _undo_autocorrect(self) -> None:
        info = self._last_autocorrect
        if not info:
            return
        orig_scans, from_layout, to_layout, converted, sep_vk, sep_shifted = info
        from .conversion import scans_to_eng, scans_to_ukr

        orig_text = (
            scans_to_ukr(orig_scans) if from_layout == LANG_UKRAINIAN else scans_to_eng(orig_scans)
        )

        send_backspaces(len(converted) + (1 if sep_vk else 0))
        time.sleep(0.02)
        set_foreground_layout(from_layout)
        time.sleep(0.02)
        send_unicode_string(orig_text)
        self._send_sep(sep_vk, sep_shifted)

        if self.config.learning_enabled:
            self.learning.learn_block_word(orig_text.lower(), from_layout)

        self.clear_autocorrect_undo()


# =============================================================================
# Keyboard Hook
# =============================================================================


class KeyboardHook:
    """
    Low-level keyboard hook (WH_KEYBOARD_LL).
    Runs on dedicated thread; must return quickly.
    """

    def __init__(
        self,
        worker: CorrectionWorker,
        phrase_buffer: PhraseBuffer,
        caret_guard: CaretGuard,
        double_tap: DoubleTapDetector,
        config: Config,
        enabled_flag: threading.Event,
        get_layout_func: Callable[[bool], int],
    ):
        self.worker = worker
        self.phrase_buffer = phrase_buffer
        self.caret_guard = caret_guard
        self.double_tap = double_tap
        self.config = config
        self.enabled_flag = enabled_flag
        self.get_layout = get_layout_func

        self._hook: ctypes.c_void_p | None = None
        self._callback: Any = None
        self._typed_scans: list[Scan] = []
        self._last_hwnd: int | None = None
        self._cached_layout = LANG_ENGLISH
        self._cached_layout_time = 0.0
        self._word_layout: int | None = None
        self._shortcut_modifiers_down: set[str] = set()

    def install(self, hinst: int) -> bool:
        self._callback = HOOKPROC(self._hook_proc)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._callback, hinst, 0)
        return self._hook is not None

    def uninstall(self) -> None:
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        self._callback = None

    def is_installed(self) -> bool:
        """Check if hook is installed."""
        return self._hook is not None

    def clear_typed_scans(self) -> None:
        """Discard the partial word when the caret moves outside this hook."""
        self._typed_scans.clear()
        self._word_layout = None

    def _enqueue_auto(
        self,
        scans: list[Scan],
        layout: int,
        sep_vk: int,
        sep_shifted: bool,
        suppress_separator: bool = False,
    ) -> None:
        """Queue a boundary check and keep later input behind a real word."""
        reserved = bool(scans)
        if reserved:
            self.worker.reserve_input()
        try:
            self.worker.enqueue(
                CorrectionTask(
                    scans=scans,
                    layout=layout,
                    sep_vk=sep_vk,
                    sep_shifted=sep_shifted,
                    seq=self.worker._input_seq,
                    is_manual=False,
                    input_reserved=reserved,
                    separator_suppressed=suppress_separator,
                )
            )
        except BaseException:
            if reserved:
                self.worker.release_input()
            raise

    def _hook_proc(self, nCode: int, wParam: int, lParam: int) -> int:
        if nCode < 0:
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        is_down = wParam in DOWN_MSGS
        is_up = wParam in UP_MSGS
        if not (is_down or is_up):
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        flags = kb.flags
        vk = kb.vkCode
        sc = kb.scanCode

        # Ignore injected events (our own SendInput)
        if flags & LLKHF_INJECTED:
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        modifier_family = _shortcut_modifier_family(vk)
        if is_down and modifier_family:
            self._shortcut_modifiers_down.add(modifier_family)
        elif is_up and modifier_family:
            self._shortcut_modifiers_down.discard(modifier_family)

        # Key UP handling
        if (
            is_up
            and self.double_tap.is_trigger(vk)
            and self.double_tap.on_trigger_up(time.time(), self.config.double_tap_window)
            and self.enabled_flag.is_set()
        ):
            self.worker.enqueue(
                CorrectionTask(
                    scans=[],
                    layout=0,
                    sep_vk=0,
                    sep_shifted=False,
                    seq=0,
                    is_manual=True,
                    phrase=self.phrase_buffer.copy(),
                )
            )
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        if is_up:
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        # Key DOWN - real input
        self.worker.increment_seq()

        # If correction in progress, ignore but dirty tap detector
        if self.worker._correcting:
            self.double_tap.on_other_key()
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        # Window change detection
        if self._foreground_changed():
            self._typed_scans.clear()
            self._word_layout = None
            self.worker.clear_pending()
            self.worker.clear_autocorrect_undo()
            self.phrase_buffer.clear()
            self.caret_guard.on_focus_change()

        # Trigger key (Ctrl) down
        if self.double_tap.is_trigger(vk):
            self.double_tap.on_trigger_down()
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        # Any other key dirties the tap
        self.double_tap.on_other_key()

        # Real typing clears undo availability
        self.worker.clear_autocorrect_undo()

        # Modifiers only - don't process
        if vk in MODIFIER_VKS:
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        # Other modifiers down (Ctrl/Alt/Win) - hotkey, don't process
        if self._shortcut_modifiers_down:
            is_layout_switch_hotkey = vk == VK_SPACE and "win" in self._shortcut_modifiers_down
            self._typed_scans.clear()
            self._word_layout = None
            self.phrase_buffer.clear()
            if not is_layout_switch_hotkey:
                self.worker.clear_pending()
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        if not self.enabled_flag.is_set():
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        # Backspace
        if vk == VK_BACK:
            if self._typed_scans:
                self._typed_scans.pop()
            self.phrase_buffer.backspace()
            self.worker.clear_pending()
            if not self._typed_scans:
                self._word_layout = None
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        # Navigation keys - clear buffers, set caret guard
        if vk in NAV_CLEAR_VKS:
            self._typed_scans.clear()
            self._word_layout = None
            self.phrase_buffer.clear()
            self.worker.clear_pending()
            self.caret_guard.on_nav()
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        # Word break (Space, Enter, Tab)
        if vk in WORD_BREAK_VKS:
            suppressed = self.caret_guard.on_word_break()
            suppress_separator = False
            if self.config.auto_correct_enabled and not suppressed:
                scans = list(self._typed_scans)
                layout = self._word_layout or self.get_layout(True)
                suppress_separator = bool(scans) and vk in (VK_RETURN, VK_TAB)
                self._enqueue_auto(scans, layout, vk, False, suppress_separator)
            # Enter can submit a message.  Never keep it in the manual
            # conversion buffer: replaying a stale phrase would replay Enter
            # and could send the preceding message again.
            if vk == VK_RETURN:
                self.phrase_buffer.clear()
            else:
                self.phrase_buffer.add_sep(vk)
            self._typed_scans.clear()
            self._word_layout = None
            if suppress_separator:
                return 1
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        # Letter keys (by scan code)
        if sc in LETTER_SCANS:
            if not self._typed_scans:
                self._word_layout = self.get_layout(True)
            shifted = bool(user32.GetKeyState(VK_SHIFT) & 0x8000) ^ bool(
                user32.GetKeyState(VK_CAPITAL) & 0x0001
            )
            self._typed_scans.append((sc, shifted))
            if len(self._typed_scans) > 100:
                del self._typed_scans[:-50]
            self.phrase_buffer.add_letter(sc, shifted)
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        # Punctuation / other - word boundary
        term_shifted = bool(user32.GetKeyState(VK_SHIFT) & 0x8000)
        from .conversion import is_word_terminator

        if (
            self.config.auto_correct_enabled
            and self._typed_scans
            and is_word_terminator(vk, term_shifted)
            and not self.caret_guard.on_word_break()
        ):
            scans = list(self._typed_scans)
            layout = self._word_layout or self.get_layout(True)
            self._enqueue_auto(scans, layout, vk, term_shifted)
        else:
            self.caret_guard.on_word_break()

        self._typed_scans.clear()
        self._word_layout = None
        if is_word_terminator(vk, term_shifted):
            self.phrase_buffer.add_sep(vk, term_shifted)
        else:
            self.phrase_buffer.clear()
            self.worker.clear_pending()
        return _call_next_hook(self._hook, nCode, wParam, lParam)

    def _foreground_changed(self) -> bool:
        hwnd = user32.GetForegroundWindow()
        if hwnd != self._last_hwnd:
            self._last_hwnd = hwnd
            return True
        return False


# =============================================================================
# Mouse Hook
# =============================================================================


class MouseHook:
    """Low-level mouse hook to clear buffers on click."""

    def __init__(
        self,
        phrase_buffer: PhraseBuffer,
        worker: CorrectionWorker,
        keyboard_hook: KeyboardHook,
        caret_guard: CaretGuard,
    ):
        self.phrase_buffer = phrase_buffer
        self.worker = worker
        self.keyboard_hook = keyboard_hook
        self.caret_guard = caret_guard
        self._hook: ctypes.c_void_p | None = None
        self._callback: Any = None

    def install(self, hinst: int) -> bool:
        self._callback = HOOKPROC(self._hook_proc)
        self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._callback, hinst, 0)
        return self._hook is not None

    def uninstall(self) -> None:
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        self._callback = None

    def is_installed(self) -> bool:
        """Check if hook is installed."""
        return self._hook is not None

    def _hook_proc(self, nCode: int, wParam: int, lParam: int) -> int:
        if nCode < 0 or wParam not in MOUSE_DOWN_MSGS:
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        if ms.flags & LLMHF_INJECTED:
            return _call_next_hook(self._hook, nCode, wParam, lParam)

        self._reset_input_context()
        return _call_next_hook(self._hook, nCode, wParam, lParam)

    def _reset_input_context(self) -> None:
        """Invalidate buffered text after a real click moves the caret."""
        self.worker.increment_seq()
        self.keyboard_hook.clear_typed_scans()
        self.phrase_buffer.clear()
        self.worker.clear_pending()
        self.worker.clear_autocorrect_undo()
        self.caret_guard.on_nav()
