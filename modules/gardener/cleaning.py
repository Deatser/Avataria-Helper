# modules/gardener/cleaning.py
"""One cleaning run: click every piece of litter that was found, in turn.

Deliberately paced rather than as fast as the loop can go. The game has to
notice each click and play whatever it plays when litter is picked up, and a
burst of clicks lands during animations that swallow them. One at a time,
with a gap, is also what makes the bars fill at a readable speed instead of
snapping to full.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.input_sender import click_at

# Between one piece of litter and the next.
STEP_MS = 700

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

    def __init__(self, hwnd: int, jobs: list[Job], parent=None):
        super().__init__(parent)
        self._hwnd    = hwnd
        self._jobs    = list(jobs)
        self._index   = 0
        self._started = 0.0
        self._by_kind: dict[str, int] = {}

        self._timer = QTimer(self)
        self._timer.setInterval(STEP_MS)
        self._timer.timeout.connect(self._step)

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    @property
    def total(self) -> int:
        return len(self._jobs)

    def start(self):
        self._index   = 0
        self._by_kind = {}
        self._started = time.monotonic()
        if not self._jobs:
            self.finished.emit(0.0)
            return
        self._timer.start()

    def stop(self):
        self._timer.stop()

    # ── The loop ─────────────────────────────────────────────────────────────

    def _step(self):
        if self._index >= len(self._jobs):
            self._timer.stop()
            self.finished.emit(time.monotonic() - self._started)
            return

        job = self._jobs[self._index]
        self._index += 1
        click_at(self._hwnd, job.x, job.y)

        self._by_kind[job.key] = self._by_kind.get(job.key, 0) + 1
        self.cleaned.emit(job.key, self._by_kind[job.key], self._index)


def format_duration(seconds: float) -> str:
    """"3 минуты 7 секунд" — the run's own length, read out loud."""
    whole   = int(round(seconds))
    minutes, secs = divmod(whole, 60)
    if minutes and secs:
        return f"{minutes} мин {secs} сек"
    if minutes:
        return f"{minutes} мин"
    return f"{secs} сек"
