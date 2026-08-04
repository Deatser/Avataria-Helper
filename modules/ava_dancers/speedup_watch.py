# modules/ava_dancers/speedup_watch.py
from __future__ import annotations
import time
import threading
from pathlib import Path

import cv2
import mss
from PySide6.QtCore import QThread, Signal

from app.core.capture import grab_window

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

# Every mark seen so far is a few seconds' warning before a speed wave that
# always comes (per the person who timed it live: 3-15s later, but always).
# label -> template filename.
MARKS: list[tuple[str, str]] = [
    ("04:00", "timer4min.png"),
    ("06:00", "timer6min.png"),
    ("07:30", "timer730min.png"),
    ("09:30", "timer930min.png"),
    ("11:00", "timer11min.png"),
]

# No known screen coordinates for the timer, so this searches the whole
# primary monitor rather than a fixed box. Each mark only needs to be caught
# once, so this can afford to poll slower than the tile detector.
SEARCH_INTERVAL     = 0.3
MATCH_THRESHOLD     = 0.95   # 0.98 turned out to never hit live — the game's
                             # own render (font smoothing, screen scaling) just
                             # doesn't match the reference crop that closely.
                             # 0.95 is looser but still well clear of a wrong-
                             # mark false positive.
RETRIGGER_COOLDOWN  = 60.0   # a given mark can't reappear within a round; this
                             # just guards against catching it twice in one
                             # on-screen window, per mark, while still re-
                             # arming each one for the next round


def _primary_monitor_region() -> dict:
    with mss.mss() as sct:
        return dict(sct.monitors[1])


class SpeedupWatch(QThread):
    """Watches the screen for the game's own timer to hit any of several
    known marks (04:00, 06:00, 07:30, 09:30, 11:00) — each one a fixed
    warning a few seconds ahead of a sudden speed ramp that otherwise catches
    the bot off guard, so the window can get ready before it hits instead of
    scrambling after.
    """
    # Named watch_started, not started — QThread already has a built-in
    # `started` signal fired by Qt itself right before run(); reusing that
    # name for our own signal meant _on_speedup_watch_started in window.py
    # fired once for Qt's own signal and again for ours, doubling every log
    # line it wrote.
    watch_started = Signal()
    detected      = Signal(str, float)   # mark label, match score
    error         = Signal(str)

    def __init__(self, game_hwnd: int):
        super().__init__()
        self._hwnd        = game_hwnd
        self._stop_event = threading.Event()

    def stop_watch(self):
        self._stop_event.set()

    def run(self):
        templates = []
        for label, filename in MARKS:
            img = cv2.imread(str(_TEMPLATES_DIR / filename), cv2.IMREAD_GRAYSCALE)
            if img is None:
                self.error.emit(f"Не найден шаблон таймера: {filename}")
                continue
            templates.append((label, img))
        if not templates:
            return
        self._loop(templates)

    def _loop(self, templates: list[tuple[str, "cv2.Mat"]]):
        self._stop_event.clear()
        region  = _primary_monitor_region()
        last_match = {label: 0.0 for label, _ in templates}
        self.watch_started.emit()

        while not self._stop_event.is_set():
            try:
                img  = grab_window(self._hwnd, region)
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                now  = time.time()

                for label, template in templates:
                    result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
                    _, score, _, _ = cv2.minMaxLoc(result)
                    if score >= MATCH_THRESHOLD and now - last_match[label] >= RETRIGGER_COOLDOWN:
                        last_match[label] = now
                        self.detected.emit(label, score)

            except Exception as exc:
                self.error.emit(str(exc))

            self._stop_event.wait(SEARCH_INTERVAL)
