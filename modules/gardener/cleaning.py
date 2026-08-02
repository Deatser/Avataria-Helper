# modules/gardener/cleaning.py
"""One cleaning run: mark the litter, then wait for the gardener to clear it.

A click does not remove anything. It marks a piece of litter, and the
character then walks over and deals with it in his own time — so the run is
in two phases, one kind at a time:

  * every piece of that kind is clicked, a moment apart, which queues them
    all up;
  * then the screen is counted over and over until there are none of that
    kind left, and only then does the next kind start.

Nothing is counted because it was clicked. The bar moves when there is one
fewer of that kind on screen than there was — that is the character having
actually done it. Clicking all six kinds at once, which is what this used to
do, sent him round the garden in circles instead.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.input_sender import click_at

# Between two clicks while marking one kind. Only long enough for the game to
# register each of them; nothing is being waited for here.
MARK_MS = 300

# How often the screen is counted while the gardener works. He walks, so
# there is nothing to see for seconds at a time, and a full count of one kind
# costs up to 200 ms of the interface's own time.
WATCH_MS = 1200

# Counts in a row with nothing removed before that kind is considered stuck.
# Roughly twenty seconds — long enough to cross the garden, short enough not
# to sit forever on litter that cannot be reached.
PATIENCE = 16

# How many times the leftovers of a kind are marked again before the run
# gives up on them and moves on. A mark can be lost — clicked while the
# character was mid-animation, or on a piece behind the interface.
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


class CleaningRun(QObject):
    """Marks one kind, waits for it to go, then starts the next."""

    marking   = Signal(str, int, int)   # kind key, how many marked, which round
    cleaned   = Signal(str, int, int)   # kind key, done for it, done overall
    kind_done = Signal(str, int, int)   # kind key, done for it, how many there were
    finished  = Signal(float)           # seconds the run took

    def __init__(self, hwnd: int, jobs: list[Job],
                 recount: Callable[[str], int] | None = None, parent=None):
        super().__init__(parent)
        self._hwnd    = hwnd
        self._recount = recount
        self._started = 0.0

        # Grouped, and kept in the order the jobs arrived: the scan reports
        # kind by kind, so that order is already the order of the bars.
        self._order: list[str] = []
        self._groups: dict[str, list[Job]] = {}
        for job in jobs:
            if job.key not in self._groups:
                self._groups[job.key] = []
                self._order.append(job.key)
            self._groups[job.key].append(job)

        self._kind_at = 0        # which kind is being worked on
        self._mark_at = 0        # how far through marking that kind
        self._idle    = 0        # counts in a row with nothing removed
        self._round   = 0        # how many times this kind has been re-marked
        self._done: dict[str, int] = {}

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._tick)
        self._marking = True

    # ── What it is doing ─────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    @property
    def total(self) -> int:
        return sum(len(group) for group in self._groups.values())

    @property
    def done(self) -> int:
        return sum(self._done.values())

    @property
    def kind(self) -> str | None:
        """The kind being worked on, or None once the run is over."""
        if self._kind_at >= len(self._order):
            return None
        return self._order[self._kind_at]

    def start(self):
        self._kind_at = self._mark_at = self._idle = self._round = 0
        self._done    = {}
        self._marking = True
        self._started = time.monotonic()
        if not self._order:
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
        """Click the next piece of this kind, or start waiting for the lot."""
        key  = self.kind
        jobs = self._groups[key]
        if self._mark_at < len(jobs):
            job = jobs[self._mark_at]
            self._mark_at += 1
            click_at(self._hwnd, job.x, job.y)
            self._timer.start(MARK_MS)
            return

        self.marking.emit(key, len(jobs) - self._done.get(key, 0), self._round)
        self._marking = False
        self._idle    = 0
        self._timer.start(WATCH_MS)

    def _watch(self):
        """Count what is left of this kind and credit whatever has gone."""
        key   = self.kind
        total = len(self._groups[key])

        if self._recount is None:
            self._credit(key, total)      # nothing to look at: take it as done
            self._next_kind()
            return

        try:
            left = self._recount(key)
        except Exception:
            left = None                   # a failed grab says nothing either way

        if left is not None and self._credit(key, total - left):
            self._idle = 0
        else:
            self._idle += 1

        if left is not None and left <= 0:
            self._next_kind()
        elif self._idle >= PATIENCE:
            self._stuck()
        else:
            self._timer.start(WATCH_MS)

    def _credit(self, key: str, gone: int) -> bool:
        """Move that kind's bar, if more of it has gone than last time."""
        gone = max(0, min(gone, len(self._groups[key])))
        if gone <= self._done.get(key, 0):
            return False
        self._done[key] = gone
        self.cleaned.emit(key, gone, self.done)
        return True

    def _stuck(self):
        """Nothing has moved for a while: mark the rest again, or move on."""
        if self._round < MARK_ROUNDS:
            self._round  += 1
            self._mark_at = 0
            self._marking = True
            self._timer.start(MARK_MS)
            return
        self._next_kind()

    def _next_kind(self):
        key = self.kind
        if key is not None:
            self.kind_done.emit(key, self._done.get(key, 0),
                                len(self._groups[key]))
        self._kind_at += 1
        self._mark_at = self._idle = self._round = 0
        self._marking = True
        if self._kind_at >= len(self._order):
            self.stop()
            self.finished.emit(time.monotonic() - self._started)
            return
        self._timer.start(MARK_MS)


def format_duration(seconds: float) -> str:
    """"3 минуты 7 секунд" — the run's own length, read out loud."""
    whole   = int(round(seconds))
    minutes, secs = divmod(whole, 60)
    if minutes and secs:
        return f"{minutes} мин {secs} сек"
    if minutes:
        return f"{minutes} мин"
    return f"{secs} сек"
