# modules/ava_dancers/leave_watch.py
from __future__ import annotations
import threading

import cv2
from PySide6.QtCore import QThread, Signal

from app.core.capture import grab_window
from app.core.template_match import (LEAVE_TEMPLATES, MATCH_THRESHOLD, Match,
                                     FINISH_GOLD, primary_monitor_region,
                                     search_screen)

# Once a second is plenty: this only answers "may the run end yet", a state
# that holds for seconds once it arrives, and the tile detector already owns
# the CPU budget that matters.
SEARCH_INTERVAL = 1.0

class LeaveWatch(QThread):
    """Watches for the end-of-round reward line and says when to bail out.

    Only the chosen currency can fire `leave_ready` — the other one is
    scored too, since search_screen does both in one grab, but its number
    goes nowhere.
    """
    leave_ready  = Signal(str, float)    # display label, score — time to finish
    error        = Signal(str)

    def __init__(self, game_hwnd: int, target: str = FINISH_GOLD):
        super().__init__()
        self._hwnd       = game_hwnd
        self._target     = target
        self._stop_event = threading.Event()
        self._fired      = False

    def set_target(self, target: str):
        """Switch currency mid-run — the settings toggle writes through here.

        Re-arms the trigger: the new target has not fired yet even if the old
        one had.
        """
        self._target = target
        self._fired  = False

    def stop_watch(self):
        self._stop_event.set()

    def run(self):
        self._loop()

    def _loop(self):
        self._stop_event.clear()
        self._fired = False
        region   = primary_monitor_region()

        while not self._stop_event.is_set():
            try:
                gray = cv2.cvtColor(grab_window(self._hwnd, region), cv2.COLOR_BGR2GRAY)
                self._check_target(search_screen(LEAVE_TEMPLATES, screen=gray))

            except Exception as exc:
                self.error.emit(str(exc))

            self._stop_event.wait(SEARCH_INTERVAL)

    def _check_target(self, matches: list[Match]):
        """Fire once per run for the chosen currency, ignore the other one."""
        if self._fired:
            return
        for m in matches:
            if m.key == self._target and m.score >= MATCH_THRESHOLD:
                self._fired = True
                self.leave_ready.emit(m.label, m.score)
                return
