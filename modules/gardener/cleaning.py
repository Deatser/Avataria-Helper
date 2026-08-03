# modules/gardener/cleaning.py
"""One cleaning run: mark every piece of litter once, then wait it out.

A click does not remove anything. It queues a spot, and the game itself sends
the character round to every queued spot in its own time — clicking the same
spot again does not speed that up, and doing it while the character is
already on the way there is just as likely to knock it out of the queue as
help it along. So a run is in two parts: everything is marked, once each,
and then the marks are watched until they have been worked through. Nothing
is ever marked a second time.

The order the marks go down in is the order he walks them, and that order is
purely geographic — left to right, as if a vertical line swept across the
garden. Kind does not come into it. Marking all the dry bushes first and then
all the blue ones sends him back across the whole map for every kind.

Watching is done a mark at a time, several times a second: each one is looked
for in a small box around its own coordinates, which is a fraction of a
millisecond of work, and a mark is credited only once there is nothing under
it. Counting whole kinds off the whole screen, which is what this used to do,
cost 200 ms a look and could not tell *which* bush had gone.

A mark has to come up empty twice running before it counts. The character
walks over the litter constantly, and a bush behind him for one frame has not
been cleared. If some marks never confirm as cleared, the run says so and
stops — it does not go back and click them again.
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

# How often the marks are checked. Fast, because it can afford to be.
WATCH_MS = 250

# Looks in a row with nothing under a mark before it is believed gone.
CONFIRM_LOOKS = 2

# How long a fresh mark is left alone before its "is it gone" reading is
# trusted at all. The game answers a click with a flourish of its own right
# over the spot, and the character takes a moment to even set off — both are
# enough to make the match dip on their own, and two looks 250 ms apart is
# nowhere near long enough to tell that from the real thing being cleared.
SETTLE_S = 3.0

# Looks in a row with no mark going anywhere before the run gives up and
# reports whatever is left as not cleared. About twenty seconds — long
# enough for the character to cross the whole garden on foot.
PATIENCE = 80

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
    """Marks the whole garden left to right, then watches the marks go."""

    marking   = Signal(int)              # how many marks went down
    cleaned   = Signal(str, int, int)   # kind key, done for it, done overall
    kind_done = Signal(str, int)        # kind key, how many there were
    finished  = Signal(float)           # seconds the run took

    def __init__(self, hwnd: int, jobs: list[Job],
                 gone: Callable[[list], list] | None = None, parent=None):
        super().__init__(parent)
        self._hwnd    = hwnd
        self._gone    = gone      # which of these marks have nothing under them
        self._started = 0.0

        self._jobs = sweep_order(jobs)
        self._left = list(self._jobs)       # marks still standing
        self._marked_at: dict[int, float] = {}   # id(job) -> when it was clicked

        self._initial: dict[str, int] = {}
        for job in jobs:
            self._initial[job.key] = self._initial.get(job.key, 0) + 1
        self._done: dict[str, int] = {}
        self._misses: dict[int, int] = {}   # id(job) -> looks it came up empty

        self._mark_at = 0     # how far through putting the marks down
        self._idle    = 0     # looks in a row with nothing credited
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
        self._mark_at   = self._idle = 0
        self._done      = {}
        self._misses    = {}
        self._marked_at = {}
        self._left      = list(self._jobs)
        self._marking   = True
        self._started   = time.monotonic()
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
        """Put down the next mark, or start watching once they are all down."""
        if self._mark_at < len(self._jobs):
            job = self._jobs[self._mark_at]
            self._mark_at += 1
            self._marked_at[id(job)] = time.monotonic()
            click_at(self._hwnd, job.x, job.y)
            self._timer.start(MARK_MS)
            return

        self.marking.emit(len(self._jobs))
        self._marking = False
        self._idle    = 0
        self._timer.start(WATCH_MS)

    def _watch(self):
        """Look at every settled mark and credit the ones now empty.

        A mark that has not had SETTLE_S to itself yet is left out of the
        look entirely — asking about it is how a click's own on-screen
        flourish gets mistaken for the litter being gone.
        """
        if self._gone is None or not self._left:
            self._finish()
            return

        now = time.monotonic()
        ready = [job for job in self._left
                if now - self._marked_at.get(id(job), 0.0) >= SETTLE_S]

        try:
            empty = self._gone(ready) if ready else []
        except Exception:
            empty = []          # a failed grab says nothing either way

        cleared = [job for job in empty if self._confirmed(job)]
        for job in cleared:
            self._left.remove(job)
            self._credit(job.key)
        self._idle = 0 if cleared else self._idle + 1

        if not self._left or self._idle >= PATIENCE:
            self._finish()      # whatever is left stands as not cleared
        else:
            self._timer.start(WATCH_MS)

    def _confirmed(self, job: Job) -> bool:
        """Empty once can be the gardener standing in front of it."""
        self._misses[id(job)] = self._misses.get(id(job), 0) + 1
        return self._misses[id(job)] >= CONFIRM_LOOKS

    def _credit(self, key: str):
        """One more of that kind gone — as seen, not as clicked."""
        self._done[key] = self._done.get(key, 0) + 1
        self.cleaned.emit(key, self._done[key], self.done)
        if self._done[key] >= self._initial.get(key, 0):
            self.kind_done.emit(key, self._done[key])

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
