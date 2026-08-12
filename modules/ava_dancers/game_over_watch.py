# modules/ava_dancers/game_over_watch.py
from __future__ import annotations
import threading

import cv2
from PySide6.QtCore import QThread, Signal

from app.core.capture import grab_window, release_window_capture
from app.core.template_match import best_match, load_template, primary_monitor_region

# Once a second is plenty: this only answers "has the round ended yet", a
# state that holds for seconds once it arrives, and the tile detector already
# owns the CPU budget that matters.
SEARCH_INTERVAL = 1.0

# A fixed piece of chrome, but the banner animates in over its own backdrop,
# so a real end-of-round only scores ~0.93 at best — 0.80 leaves room for
# that without letting anything else on screen through (2026-08-12).
MATCH_THRESHOLD = 0.80

GAMEOVER_TEMPLATE = "gameover.png"


class GameOverWatch(QThread):
    """Watches for the "ИГРА ОКОНЧЕНА" banner and says when the round is over.

    Fires once per run — a fresh instance is made for every new round, so
    there is nothing to re-arm between them.
    """
    game_over = Signal(float)   # match score
    error     = Signal(str)

    def __init__(self, game_hwnd: int):
        super().__init__()
        self._hwnd       = game_hwnd
        self._stop_event = threading.Event()
        self._fired       = False

    def stop_watch(self):
        self._stop_event.set()

    def run(self):
        self._loop()

    def _loop(self):
        self._stop_event.clear()
        self._fired = False
        template = load_template(GAMEOVER_TEMPLATE)
        if template is None:
            self.error.emit(f"Не найден шаблон: {GAMEOVER_TEMPLATE}")
            return
        region = primary_monitor_region()

        try:
            while not self._stop_event.is_set():
                try:
                    gray = cv2.cvtColor(grab_window(self._hwnd, region), cv2.COLOR_BGR2GRAY)
                    score, _ = best_match(gray, template)
                    self._check_score(score)

                except Exception as exc:
                    self.error.emit(str(exc))

                self._stop_event.wait(SEARCH_INTERVAL)
        finally:
            release_window_capture()

    def _check_score(self, score: float):
        """Fire once the banner clears the match bar, then stay quiet."""
        if self._fired:
            return
        if score >= MATCH_THRESHOLD:
            self._fired = True
            self.game_over.emit(score)
