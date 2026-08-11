from __future__ import annotations
import time
import threading

import win32gui
from PySide6.QtCore import QThread, Signal

from app.core.capture import grab_window
from app.core.input_sender import press_key_after

# What a tile looks like lives in tiles.py, and how a note is followed down
# its lane lives in tracker.py. The tile vocabulary is re-exported here
# because window.py and the tests have always imported it from bot.
from modules.ava_dancers.tiles import (           # noqa: F401
    EMPTY, NICE, BONUS, BAD, DISLIKE, BOMB, PRESSABLE,
    Thresholds, TileReading,
    split_tiles, classify_tile, classify_hsv, decide_kind,
    _IDLE_V, _LIT_V,
)
from modules.ava_dancers.tracker import (DEFAULT_GEOMETRY, LaneTracker, Press,
                                         locate_field)

# Tile index → keyboard key. Lanes run left to right, and the game's own
# target arrows under them read ← ↓ ↑ →.
KEY_MAP: dict[int, str] = {0: "a", 1: "s", 2: "w", 3: "d"}

# ── Pacing ───────────────────────────────────────────────────────────────
# A floor under the loop, not its real rate: a full-window PrintWindow costs
# ~30-40ms on its own (see capture.py's per-thread DC/bitmap cache) and is
# what actually paces this, so asking for less than that buys nothing.
#
# The strip detector this replaced needed every frame it could get, because
# it had exactly one poll's chance to catch a note crossing its strip. The
# tracker does not: it fits a line through a note's whole descent and
# schedules the press ahead of time, so a dropped frame costs a little
# precision rather than the note. That is also why the "boost the poll rate
# around a speed wave" machinery is gone — it saved 1ms out of 42.
_POLL_SLEEP = 0.001

# How often the detector says what it is doing (see AvaBot._report). Without
# it a bad round is unreadable: presses are the only visible output, and
# "pressed at the wrong moment" and "pressed at something that was not a
# note" look identical from outside.
_REPORT_EVERY_S = 5.0

# How far the located playfield may sit from the measured default before it
# is worth mentioning. The border lookup is repeatable to about a pixel.
_FIELD_DRIFT_PX = 4


class AvaBot(QThread):
    """Watches all four lanes and presses each note on a predicted arrival.

    The thread itself does almost nothing: capture a region, hand it to the
    LaneTracker, post whatever keys it decided on. Every judgement about
    what is a note and when it lands belongs to tracker.py.
    """

    # Notes as they land, [(lane, kind), ...] — emitted when the note
    # actually reaches the hit line rather than when it was first spotted
    # half a second earlier, so the readout still lines up with the game.
    tiles_seen    = Signal(list)
    key_pressed   = Signal(str)
    field_located = Signal(str)   # only when the field is not where expected
    diagnostics   = Signal(str)   # periodic "what the detector is doing" line
    error         = Signal(str)

    def __init__(self, game_hwnd: int, thresholds: Thresholds = None):
        super().__init__()
        self._hwnd       = game_hwnd
        self._th         = thresholds or Thresholds()
        self._stop_event = threading.Event()
        self._tracker    = LaneTracker(self._th)
        self._pending: list[tuple[float, int, str]] = []   # (when, lane, kind)
        self._pressed = 0

    def configure(self, thresholds: Thresholds):
        self._th = thresholds
        self._tracker.configure(thresholds)

    def geometry(self):
        """Where the tracker currently thinks the playfield is — whatever
        _calibrate() found, or the measured default before it has run."""
        return self._tracker.geometry

    def stop_bot(self):
        self._stop_event.set()

    def run(self):
        self._loop()

    def _loop(self):
        self._stop_event.clear()
        self._tracker.reset()
        self._pending.clear()
        self._pressed = 0
        self._calibrate()

        region   = self._tracker.geometry.region
        expected = (region["height"], region["width"])
        warned_shape = False
        polls = 0
        started = last_report = time.monotonic()

        while not self._stop_event.is_set():
            try:
                img = grab_window(self._hwnd, region)
                now = time.monotonic()
                if img.shape[:2] != expected:
                    if not warned_shape:
                        warned_shape = True
                        self.error.emit(
                            f"Зона рядов не помещается в окно игры "
                            f"({img.shape[1]}x{img.shape[0]} вместо "
                            f"{expected[1]}x{expected[0]}) — разверни окно")
                    continue

                polls += 1
                for press in self._tracker.update(now, img):
                    self._schedule(press)
                self._flush_pending(now)

                if now - last_report >= _REPORT_EVERY_S:
                    self._report(polls / (now - started))
                    last_report = now

            except Exception as exc:
                self.error.emit(str(exc))

            time.sleep(_POLL_SLEEP)

    def _calibrate(self):
        """Find the playfield before the first poll, rather than trusting a
        constant.

        Every coordinate the detector uses hangs off one rectangle, and that
        rectangle moves the moment the game window is resized — a whole class
        of "it just stopped working" that no amount of threshold tuning
        explains. The playfield draws its own bright cyan border, so it can
        be looked up in one capture. If it is not there yet (the round is
        still loading) the measured defaults stand.
        """
        try:
            left, top, _, _ = win32gui.GetWindowRect(self._hwnd)
            found = locate_field(grab_window(self._hwnd), left, top)
        except Exception as exc:
            self.error.emit(f"Не удалось найти поле: {exc}")
            return
        if found is None:
            return
        self._tracker.set_geometry(found)
        # Only worth a line when the field is somewhere genuinely different.
        # The border lookup lands within a pixel or two of the hand-measured
        # rectangle every time, and saying so once per round is just noise in
        # a log that exists to make real problems visible.
        drift = max(abs(found.left - DEFAULT_GEOMETRY.left),
                    abs(found.top - DEFAULT_GEOMETRY.top),
                    abs(found.width - DEFAULT_GEOMETRY.width),
                    abs(found.height - DEFAULT_GEOMETRY.height))
        if drift > _FIELD_DRIFT_PX:
            self.field_located.emit(
                f"поле {found.width}×{found.height} в ({found.left}, {found.top})")

    def _schedule(self, press: Press):
        """Hand the key to the delayed-call scheduler.

        The whole point of tracking is that the press lands on a time worked
        out from the note's own speed, not on whichever poll happened to see
        it — so it goes through call_later at millisecond resolution rather
        than being fired here, up to one capture late.

        One key per note, once. See press_key_after: repeating it is what
        made the game score doubles.
        """
        key = KEY_MAP.get(press.lane)
        if not key:
            return
        press_key_after(self._hwnd, key, press.delay_s)
        self._tracker.expect_hit(press.lane, press.arrival)
        self._pressed += 1
        self.key_pressed.emit(key)
        self._pending.append((press.arrival, press.lane, press.kind))

    def _flush_pending(self, now: float):
        """Report notes to the UI as they land, not as they were spotted."""
        due = [(lane, kind) for when, lane, kind in self._pending if when <= now]
        if due:
            self._pending = [p for p in self._pending if p[0] > now]
            self.tiles_seen.emit(due)

    def _report(self, hz: float):
        tracker = self._tracker
        refused = ", ".join(f"{why} {n}"
                            for why, n in tracker.refusals.most_common())
        line = (f"{hz:.0f} Гц · нот {tracker.committed} · "
                f"нажато {self._pressed}")
        if tracker.scored or tracker.missed:
            line += f" · попало {tracker.scored}"
            if tracker.missed:
                line += f" · МИМО {tracker.missed}"
        if refused:
            line += f" · отказ: {refused}"
        self.diagnostics.emit(line)
