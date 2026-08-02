# modules/gardener/cleaning.py
"""One cleaning run: click every piece of litter that was found, in turn.

Deliberately paced rather than as fast as the loop can go. The game has to
notice each click and play whatever it plays when litter is picked up, and a
burst of clicks lands during animations that swallow them.

Nothing is counted because it was clicked. After each click the screen is
looked at again and that kind is counted: only when there is one fewer of
them than there was does the bar move. A click that misses, or that the game
ignores, is retried rather than credited — otherwise a bar reaches 11/11
while eleven bushes are still standing there.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.input_sender import click_at

# Between one piece of litter and the next.
STEP_MS = 700

# After a click, before counting what is left: the game fades litter out
# rather than removing it, and counting into the fade sees it still there.
VERIFY_MS = 450

# How many times one piece is clicked before the run moves on without it.
# Some are simply not clickable — behind a fence, under the interface — and
# the run should not stall on them.
MAX_TRIES = 3

# A run cannot be started again immediately: the garden refills on its own
# schedule, and hammering it achieves nothing.
COOLDOWN_MINUTES = 60


@dataclass
class Job:
    """One piece of litter to be dealt with."""
    key: str        # which kind it belongs to
    x: int
    y: int


class CleaningRun(QObject):
    """Works through a list of jobs, saying what it has done as it goes."""

    cleaned  = Signal(str, int, int)   # kind key, done for that kind, done overall
    finished = Signal(float)           # seconds the run took

    def __init__(self, hwnd: int, jobs: list[Job],
                 recount: Callable[[str], int] | None = None, parent=None):
        super().__init__(parent)
        self._hwnd    = hwnd
        self._jobs    = list(jobs)
        self._recount = recount
        self._index   = 0
        self._tries   = 0
        self._started = 0.0

        # How many of each kind were there to begin with, and how many of
        # those have since been seen to go.
        self._initial: dict[str, int] = {}
        for job in self._jobs:
            self._initial[job.key] = self._initial.get(job.key, 0) + 1
        self._done: dict[str, int] = {}

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(STEP_MS)
        self._timer.timeout.connect(self._click)

        self._check = QTimer(self)
        self._check.setSingleShot(True)
        self._check.setInterval(VERIFY_MS)
        self._check.timeout.connect(self._verify)

    @property
    def running(self) -> bool:
        return self._timer.isActive() or self._check.isActive()

    @property
    def total(self) -> int:
        return len(self._jobs)

    @property
    def done(self) -> int:
        return sum(self._done.values())

    def start(self):
        self._index   = 0
        self._tries   = 0
        self._done    = {}
        self._started = time.monotonic()
        if not self._jobs:
            self.finished.emit(0.0)
            return
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self._check.stop()

    # ── The loop ─────────────────────────────────────────────────────────────

    def _click(self):
        if self._index >= len(self._jobs):
            self._finish()
            return
        job = self._jobs[self._index]
        click_at(self._hwnd, job.x, job.y)
        self._check.start()

    def _verify(self):
        """Look again. The bar moves only if there is one fewer than there was."""
        job = self._jobs[self._index]

        if self._progressed(job.key):
            self._tries = 0
            self._index += 1
        else:
            self._tries += 1
            if self._tries >= MAX_TRIES:
                self._tries = 0
                self._index += 1     # not going anywhere; leave it and move on

        if self._index >= len(self._jobs):
            self._finish()
        else:
            self._timer.start()

    def _progressed(self, key: str) -> bool:
        """Count that kind on screen and credit whatever has gone since."""
        if self._recount is None:
            gone = self._done.get(key, 0) + 1     # nothing to look at: trust the click
        else:
            try:
                left = self._recount(key)
            except Exception:
                return False        # a failed grab is not evidence of anything
            gone = self._initial.get(key, 0) - left

        gone = max(0, min(gone, self._initial.get(key, 0)))
        if gone <= self._done.get(key, 0):
            return False

        self._done[key] = gone
        self.cleaned.emit(key, gone, self.done)
        return True

    def _finish(self):
        self.stop()
        self.finished.emit(time.monotonic() - self._started)


def format_duration(seconds: float) -> str:
    """"3 минуты 7 секунд" — the run's own length, read out loud."""
    whole   = int(round(seconds))
    minutes, secs = divmod(whole, 60)
    if minutes and secs:
        return f"{minutes} мин {secs} сек"
    if minutes:
        return f"{minutes} мин"
    return f"{secs} сек"
