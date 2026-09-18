# modules/ava_dancers/leave_watch.py
from __future__ import annotations
import threading

import cv2
from PySide6.QtCore import QThread, Signal

from app.core.capture import grab_window, release_window_capture
from app.core.template_match import (FINISH_GOLD, FINISH_SILVER, best_match,
                                     load_template, game_region)

# Once a second is plenty: the line only ever moves once per run, and a
# once-a-second grab has no reason to run faster than the tile detector's
# own poll budget needs.
SEARCH_INTERVAL = 1.0

LEAVE_TEMPLATES = {
    FINISH_GOLD:   "leave_gold.png",
    FINISH_SILVER: "leave_silver.png",
}

# Calibrated by hand (2026-08-04) via "Область по краям" (CalibrationOverlay,
# run once per currency): each reward line sits in a fixed spot, so the
# watch can be pointed at that one small box instead of scanning the whole
# primary monitor for it every poll.
LEAVE_REGIONS: dict[str, dict] = {
    FINISH_GOLD:   {"left": 1917, "top": 381, "width": 316, "height": 120},
    FINISH_SILVER: {"left": 1954, "top": 651, "width": 271, "height": 120},
}

# Scanning the whole monitor, silver sat around 94% and gold around 77%
# well before the round was over — too far apart for one fixed bar to fit
# both. Inside its own calibrated region (LEAVE_REGIONS), an unrendered
# line reads far lower and much closer together for either currency
# (measured live 2026-08-04: silver 13.0%, gold 9.3%), so a single fixed
# bar comfortably clear of both is viable again.
MATCH_THRESHOLD = 0.80


class LeaveWatch(QThread):
    """Watches the chosen reward line and says when it clears the match bar."""
    leave_ready = Signal(str, float)   # display label, score
    error       = Signal(str)

    def __init__(self, game_hwnd: int, target: str = FINISH_GOLD):
        super().__init__()
        self._hwnd       = game_hwnd
        self._target     = target
        self._stop_event = threading.Event()
        self._fired       = False

    def set_target(self, target: str):
        """Switch currency mid-run — re-arms the trigger."""
        self._target = target
        self._fired  = False

    def stop_watch(self):
        self._stop_event.set()

    def run(self):
        self._loop()

    def _loop(self):
        self._stop_event.clear()
        self._fired = False

        try:
            while not self._stop_event.is_set():
                try:
                    # Re-read every poll, not cached before the loop: set_target()
                    # can switch currency mid-run, from another thread, and each
                    # currency has its own region.
                    filename = LEAVE_TEMPLATES[self._target]
                    region = LEAVE_REGIONS.get(self._target) or game_region()
                    template = load_template(filename)
                    if template is None:
                        self.error.emit(f"Не найден шаблон: {filename}")
                    else:
                        gray = cv2.cvtColor(grab_window(self._hwnd, region),
                                            cv2.COLOR_BGR2GRAY)
                        score, _ = best_match(gray, template)
                        self._check_score(score)

                except Exception as exc:
                    self.error.emit(str(exc))

                self._stop_event.wait(SEARCH_INTERVAL)
        finally:
            release_window_capture()

    def _check_score(self, score: float):
        """Fire once the match clears the bar, then stay quiet."""
        if self._fired:
            return
        if score >= MATCH_THRESHOLD:
            self._fired = True
            label = "Серебро" if self._target == FINISH_SILVER else "Золото"
            self.leave_ready.emit(label, score)
