# modules/gardener/cleaning.py
"""One cleaning run: mark every piece of litter, then let the gardener walk.

A click does not remove anything. It puts a mark on that spot, and the
character then walks over and deals with it in his own time. So a run is in
two parts: everything is marked first, and then the screen is watched until
the marks have been worked through.

The order the marks go down in is the order he walks them, and that order is
purely geographic — left to right, as if a vertical line swept across the
garden. Kind does not come into it. Marking all the dry bushes first and then
all the blue ones sends him back across the whole map for every kind; sweeping
by x means each step is to the next thing along.

Nothing is counted because it was clicked. A bar moves when there is one
fewer of that kind on screen than there was — that is the character having
actually done it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.input_sender import click_at

# Between two marks. Only long enough for the game to register each of them;
# nothing is being waited for here.
MARK_MS = 300

# How often the screen is counted while the gardener works. He walks, so
# there is nothing to see for seconds at a time, and counting one kind costs
# up to 200 ms of the interface's own time.
WATCH_MS = 1200

# Counts in a row with nothing removed before the run decides he is stuck.
# The kinds are counted in turn, so this is a good few passes over all of
# them — long enough to cross the garden, short enough not to sit forever on
# litter that cannot be reached.
PATIENCE = 20

# How many times the leftovers are marked again before the run gives up on
# them. A mark can be lost: clicked while the character was mid-animation, or
# on a piece behind the interface.
MARK_ROUNDS = 1

# A run cannot be started again immediately: the garden refills on its own
# schedule, and hammering it achieves nothing.
COOLDOWN_MINUTES = 60


@dataclass
class Job:
    """One piece of litter to be dealt with."""
    key: str        # which kind it belongs to
    x: int
    y: int


def sweep_order(jobs: list[Job]) -> list[Job]:
    """Left to right, top to bottom within a column — the walking order."""
    return sorted(jobs, key=lambda job: (job.x, job.y))


class CleaningRun(QObject):
    """Marks the whole garden left to right, then waits for it to be cleared."""

    marking   = Signal(int, int)        # how many marks, which round
    cleaned   = Signal(str, int, int)   # kind key, done for it, done overall
    kind_done = Signal(str, int)        # kind key, how many there were
    finished  = Signal(float)           # seconds the run took

    def __init__(self, hwnd: int, jobs: list[Job],
                 recount: Callable[[str], int] | None = None,
                 rescan: Callable[[], list] | None = None, parent=None):
        super().__init__(parent)
        self._hwnd    = hwnd
        self._recount = recount
        self._rescan  = rescan
        self._started = 0.0

        self._jobs = sweep_order(jobs)

        # How many of each kind there were to start with, in the order the
        # scan found them — which is the order of the bars.
        self._initial: dict[str, int] = {}
        for job in jobs:
            self._initial[job.key] = self._initial.get(job.key, 0) + 1
        self._done: dict[str, int] = {}
        self._kinds = list(self._initial)

        self._mark_at = 0     # how far through putting the marks down
        self._look_at = 0     # which kind is counted on this look
        self._idle    = 0     # looks in a row with nothing removed
        self._round   = 0     # how many times the leftovers were marked again
        self._marking = True

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)

    # ── What it is doing ─────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    @property
    def total(self) -> int:
        return sum(self._initial.values())

    @property
    def done(self) -> int:
        return sum(self._done.values())

    def start(self):
        self._mark_at = self._look_at = self._idle = self._round = 0
        self._done    = {}
        self._marking = True
        self._started = time.monotonic()
        if not self._jobs:
            self.finished.emit(0.0)
            return
        self._timer.start(MARK_MS)

    def stop(self):
        self._timer.stop()

    # ── The loop ─────────────────────────────────────────────────────────────

    def _tick(self):
        if self._marking:
            self._mark_one()
        else:
            self._watch()

    def _mark_one(self):
        """Put down the next mark, or start waiting once they are all down."""
        if self._mark_at < len(self._jobs):
            job = self._jobs[self._mark_at]
            self._mark_at += 1
            click_at(self._hwnd, job.x, job.y)
            self._timer.start(MARK_MS)
            return

        self.marking.emit(len(self._jobs), self._round)
        self._marking = False
        self._idle    = 0
        self._timer.start(WATCH_MS)

    def _watch(self):
        """Count one kind — a different one each look — and credit what has gone.

        One kind per look rather than all of them: a full count of every kind
        is half a second of the interface's own time, and there is no hurry.
        Each kind still comes round every few seconds.
        """
        if self._recount is None or not self._kinds:
            self._finish()
            return

        key = self._kinds[self._look_at % len(self._kinds)]
        self._look_at += 1

        try:
            left = self._recount(key)
        except Exception:
            left = None         # a failed grab says nothing either way

        if left is not None and self._credit(key, self._initial[key] - left):
            self._idle = 0
        else:
            self._idle += 1

        if self.done >= self.total:
            self._finish()
        elif self._idle >= PATIENCE:
            self._stuck()
        else:
            self._timer.start(WATCH_MS)

    def _credit(self, key: str, gone: int) -> bool:
        """Move that kind's bar, if more of it has gone than last time."""
        gone = max(0, min(gone, self._initial.get(key, 0)))
        if gone <= self._done.get(key, 0):
            return False
        self._done[key] = gone
        self.cleaned.emit(key, gone, self.done)
        if gone >= self._initial[key]:
            self.kind_done.emit(key, gone)
        return True

    def _stuck(self):
        """Nothing has moved for a while: mark what is left again, or stop."""
        if self._round >= MARK_ROUNDS:
            self._finish()
            return

        self._round  += 1
        self._jobs    = sweep_order(self._rescan()) if self._rescan else self._jobs
        if not self._jobs:
            self._finish()
            return
        self._mark_at = 0
        self._marking = True
        self._timer.start(MARK_MS)

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
