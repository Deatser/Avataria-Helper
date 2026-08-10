# modules/hockey/motion.py
"""Where a row's defenders will be, worked out from where they have been.

Each defender is followed as its own track, and each track is predicted from
its own past.

Two earlier designs failed here and both failures shaped this one. The first
separated a single mover from any standers by occupancy and predicted that
one — it could not represent a row holding two movers at all. The second
gave up on individuals and predicted the row as one repeating picture; that
handles any number of them, but only while the picture actually repeats, and
two patrols with unrelated periods produce a picture whose period is their
common multiple, far beyond any buffer. Measured on the rink, such a row
predicted with a mean error of 13-51px and a worst of 202 against a patrol
460 wide (2026-08-10) — wrong, and honestly reported as wrong, but wrong.

So: one track per defender, matched frame to frame, each with its own
period. What makes it work rather than fall apart at every crossing is that
matching is done against where each track's *own periodic model* says it
should be, not against where it was last seen. Two defenders meeting at the
same spot are still told apart, because their models disagree about where
they are going next.

Prediction stays model-free within a track. Nothing assumes a triangle wave,
constant speed, or an instant turn — the very first version assumed all
three, extrapolated an EMA-smoothed velocity, and was least right exactly at
the boards where a shot is hardest to time. A track is treated as periodic
and nothing more:

    find the period T, then "where will it be at t" is "where was it at
    t - kT", read straight out of what was observed.

That handles a triangle, a sine, a patrol that dawdles at the boards and one
that pauses there — and a defender that never moves needs no special case,
being simply a track whose past is a constant.

The other half of the job is knowing whether to believe any of it, because
the game allows one attempt per level: every tick also files the prediction
it just made for the moment it comes true, and compares it against what
actually happened then. That rolling error, in pixels, over exactly the
horizon a shot takes to fly, is the only thing that says whether the bot may
shoot — and it costs nothing to measure, since it never fires anything.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass, field

import numpy as np

# How much history is kept. Two full patrols is the minimum the period
# search can work with at all, and more only makes it sharper, so this is
# generous — a defender crossing the rink and back takes seconds, not tens
# of them.
_BUFFER_S = 30.0

# Samples arrive on a timer, so their spacing wobbles; the period search
# needs an even one. Everything is resampled onto this grid first.
_GRID_S = 0.04

# The band of patrol periods worth looking for. Below the first, nothing in
# this game moves; above the second, a 30s buffer cannot hold the two full
# cycles a period estimate needs.
_MIN_PERIOD_S = 0.5
_MAX_PERIOD_S = 15.0

# Normalised autocorrelation at the winning lag. Deliberately loose, and
# not the real gate: the measured error decides, because it is a fact about
# this track's future rather than a heuristic about its past, and a period
# that is actually wrong shows up there within a second and a half.
_MIN_CONFIDENCE = 0.60

# How close to the tallest autocorrelation peak a shorter lag has to score
# before it is taken as the real period instead — see _Track.refit.
_HARMONIC_MARGIN = 0.05

# A track that never wanders further than this is standing still, and needs
# no period at all.
_STILL_SPAN_PX = 14

# Frames watched before anything is judged.
_MIN_STILL_FRAMES = 40

# How far a detection may sit from where a track expected to be and still be
# that track. Generous enough to survive a defender being hidden for a
# moment, tight enough that two of them do not swap identities in the open.
# The allowance grows while a track is unseen, at the speed it was last
# travelling — a defender who turned round while hidden comes out somewhere
# a coast never pointed at — up to _MAX_MATCH_PX, beyond which "somewhere on
# this row" is not an identification.
_MATCH_PX = 90.0
_MAX_MATCH_PX = 250.0

# A track unseen for this long is gone: the level changed, or it was never
# a defender. Long enough to sit out being hidden behind somebody.
_TRACK_LOST_S = 2.0

# A defender that has stood still this long is treated as furniture: his
# position is known without looking, so a patrol passing in front of him
# costs nothing. Before that, a crossing blinded both of them, the stander
# went unfed for the whole overlap, and the pair could vanish together.
# He outlives an ordinary track by a wide margin because there is nothing to
# lose track of — only a level change should remove him.
_ANCHOR_LOST_S = 5.0

# How many whole periods back the retro-lookup may search for a stretch of
# record that was really seen. The buffer holds 30 seconds, so eight covers
# any patrol worth calling one.
_MAX_LOOKBACKS = 8

# How much of the record a track has to appear in before it counts as a
# defender rather than a stray blob — a neighbouring row's helmet bobbing
# across the boundary, a flicker of red scenery.
_MIN_TRACK_SHARE = 0.35

# The widest hole in a track that may still be interpolated across. A
# defender turns round at the boards in well under this, so a straight line
# drawn over a longer gap can miss the turn entirely — see _Track.at.
_MAX_SAMPLE_GAP_S = 0.30

# How many recent samples a track's speed is measured over. One pair of
# helmet centroids is mostly jitter; half a dozen frames is still short
# enough to catch a turn at the boards.
_SPEED_SAMPLES = 6

# Two defenders expected this close together are inside one blob: the
# detector's overlap suppression has already merged them and returns a
# single centroid somewhere between the two. Roughly a helmet's width.
_AMBIGUOUS_PX = 60.0

# How long a lost track is carried forward on its own speed before its
# periodic model takes over. The coast reflects off the track's own boards,
# so it stays inside the patrol however long it runs.
_COAST_S = 1.0

# The rolling prediction error, and how good it has to be before this row
# counts as predictable. 12px is comfortably inside the clearance a shot is
# planned with, and comfortably outside the jitter of a helmet centroid.
_ERROR_WINDOW = 30
_MAX_ERROR_PX = 12.0
_MIN_CHECKS   = 15      # comparisons needed before the error means anything

# And no single check may be wilder than this. With the confidence gate
# loosened, a period that is simply wrong can still average well for a
# stretch by luck; one prediction landing a third of a patrol away is proof
# it is wrong, however good the mean looks.
_MAX_WORST_PX = 30.0

# Refitting the period every tick would be wasted work — a patrol's period
# does not change between frames, only between levels.
_REFIT_EVERY_S = 0.5

# How far a row's fit may wander and still count as the same fit, so that a
# verdict earned earlier still stands. A patrol's period is a property of the
# level, not of the weather; refits of the same patrol land within a percent
# or two of each other, and anything past this is a different situation.
_FIT_DRIFT = 0.05

# And past this the row is not the row that was measured, whatever its fit
# still says. Twice the threshold: visibility thickens the average, a wrong
# period buries it.
_VERDICT_VOID_PX = 2 * _MAX_ERROR_PX


@dataclass(frozen=True)
class _Verdict:
    """How well a row predicted itself over one window of checks, and the fit
    that was in force while it did."""
    mean: float
    worst: float
    checks: int
    signature: tuple[float, ...]

    @property
    def passes(self) -> bool:
        return self.mean < _MAX_ERROR_PX

    def beats(self, other: "_Verdict") -> bool:
        return self.mean < other.mean

    def describes(self, signature: tuple[float, ...]) -> bool:
        """Whether it still speaks for the row as it is now."""
        if len(signature) != len(self.signature):
            return False
        return all(abs(now - then) <= _FIT_DRIFT * max(now, then, 1.0)
                   for now, then in zip(signature, self.signature))


@dataclass
class RowReport:
    """Everything worth printing about one row's model."""
    index: int
    frames: int
    seen_share: float          # ticks that detected anybody at all
    occupants: int             # how many defenders this row seems to hold
    tracked: int = 0           # and how many of them are actually followed
    settled: bool = False      # watched long enough to classify anybody
    fitted: bool = False       # the mover's patrol was actually pinned down
    standing: list[float] = field(default_factory=list)
    mean_y: float | None = None
    period: float | None = None
    confidence: float = 0.0
    min_x: float | None = None
    max_x: float | None = None
    speed: float | None = None     # px/s, averaged over the patrol
    error: float | None = None     # the verdict this row is judged on, px
    worst: float | None = None     # and the wildest single miss in it
    checks: int = 0
    live_error: float | None = None    # what the last 30 checks say right now
    # Sightings no track claims: how many, and over what stretch of ice.
    loose: tuple[int, float, float] | None = None
    ready: bool = False

    @property
    def empty(self) -> bool:
        return self.occupants == 0

    @property
    def has_mover(self) -> bool:
        return self.period is not None or self.min_x is not None


class _Track:
    """One defender, followed over time and predicted from its own past."""

    def __init__(self, t: float, x: float):
        self._t: list[float] = [t]
        self._x: list[float] = [x]
        self.period: float | None = None
        self.confidence = 0.0
        self.missing = 0.0          # seconds since it was last seen
        self.frozen = False         # period settled; stop refitting it

    # ── Record ───────────────────────────────────────────────────────────

    def see(self, t: float, x: float):
        self._t.append(t)
        self._x.append(x)
        self.missing = 0.0

    def trim(self, now: float):
        cutoff = now - _BUFFER_S
        drop = 0
        while drop < len(self._t) and self._t[drop] < cutoff:
            drop += 1
        if drop:
            del self._t[:drop]
            del self._x[:drop]

    @property
    def samples(self) -> int:
        return len(self._t)

    @property
    def span(self) -> float:
        return (max(self._x) - min(self._x)) if self._x else 0.0

    @property
    def spread(self) -> float:
        """The span with the outliers cut off.

        Plain min-to-max only ever grows: a standing defender whose centroid
        jitters by 3px reads as 19px of travel after twenty seconds, and is
        filed as a patrol. The middle nine tenths of the record does not
        drift with the length of it.
        """
        if len(self._x) < 4:
            return self.span
        low, high = np.percentile(self._x, (5, 95))
        return float(high - low)

    @property
    def bounds(self) -> tuple[float, float] | None:
        return (min(self._x), max(self._x)) if self._x else None

    @property
    def still(self) -> bool:
        return self.spread < _STILL_SPAN_PX

    def freeze(self):
        """Keep the period this track has now, whatever later frames say."""
        self.frozen = True

    @property
    def anchored(self) -> bool:
        """Stood still long enough to be taken on trust. Nothing about him
        has to be worked out again: he is where he has always been."""
        return self.still and len(self._t) >= _MIN_STILL_FRAMES

    @property
    def patrolling(self) -> bool:
        """Whether this track's own bounds are the boards it turns at — the
        test a coast has to pass before it bounces off them.

        A fitted period is the proof, because there is no period without a
        reversal in the record. Time and distance alone are not enough: a
        track a second old has been seen over a fragment of one sweep, and
        folding a coast inside that fragment sent a defender who was at
        1240 backwards to 1048 and lost him (2026-08-10).
        """
        return (not self.still and self.span >= _AMBIGUOUS_PX
                and self.period is not None
                and self.confidence >= _MIN_CONFIDENCE)

    @property
    def last(self) -> float | None:
        return self._x[-1] if self._x else None

    @property
    def last_seen(self) -> float:
        return self._t[-1] if self._t else 0.0

    # ── Reading it back ──────────────────────────────────────────────────

    def at(self, t: float) -> float | None:
        """Where it was at `t`, interpolated — but never across a hole wide
        enough for it to have turned round inside."""
        if len(self._t) < 2 or not self._t[0] <= t <= self._t[-1]:
            return None
        after = min(bisect_right(self._t, t), len(self._t) - 1)
        before = max(0, after - 1)
        if self._t[after] - self._t[before] > _MAX_SAMPLE_GAP_S:
            return None
        return float(np.interp(t, self._t, self._x))

    def predict(self, t: float) -> float | None:
        """Where it will be at `t`, or None when there is no honest answer."""
        if not self._t:
            return None
        if self.still:
            return float(np.mean(self._x[-_ERROR_WINDOW:]))
        if t <= self._t[-1]:
            return self.at(t)
        if self.period is None or self.confidence < _MIN_CONFIDENCE:
            return None
        # One whole period back, or two, or three — whichever first lands on
        # a stretch of record that was actually seen. A row detected in four
        # frames out of five has holes everywhere, and the first lookback
        # falling in one used to mean no answer at all: a row could be
        # calibrated, latched and still refuse every shot with "nothing to
        # check it against" (2026-08-10). Every whole multiple of the period
        # is the same instant of the patrol, so a later one is not a worse
        # answer — only an older reading of the same place.
        steps = math.ceil((t - self._t[-1]) / self.period)
        for back in range(steps, steps + _MAX_LOOKBACKS):
            where = self.at(t - back * self.period)
            if where is not None:
                return where
        return None

    def expect(self, t: float) -> float | None:
        """Where to look for it next.

        Two different answers for two different situations. A track seen a
        moment ago is found by carrying its own speed forward — cheap,
        exact over one frame, and it tells two defenders crossing in
        opposite directions apart, which is the case that matters. Only a
        track that has been *lost* for a while falls back on its periodic
        model to be re-acquired.

        Never the model while the track is being seen: an early fit off two
        seconds of data lands on a spurious short period with respectable
        confidence, and looking for the defender where that says put the
        expectation 96px away from where it actually was, which broke the
        match and killed the track (2026-08-10).
        """
        if not self._t:
            return None
        gap = t - self._t[-1]
        if gap <= _COAST_S:
            return self._coast(gap)
        guess = self.predict(t)
        return guess if guess is not None else self._coast(gap)

    def _speed(self) -> float:
        """Recent px per second, over a few samples rather than two — one
        pair of helmet centroids is mostly jitter."""
        if len(self._t) < 2:
            return 0.0
        back = max(0, len(self._t) - _SPEED_SAMPLES)
        elapsed = self._t[-1] - self._t[back]
        if elapsed <= 0:
            return 0.0
        return (self._x[-1] - self._x[back]) / elapsed

    def reach(self, t: float) -> float:
        """How far from its expectation a detection may sit and still be
        this track.

        A defender who stands still reaches barely at all. He has not moved,
        so a blob half a helmet away is not him — it is the single centroid
        the detector returns for him and the patrol crossing in front, and
        taking it dragged his anchor off and stopped him reading as a
        stander at all.
        """
        if self.anchored:
            return _STILL_SPAN_PX
        gap = max(0.0, t - self.last_seen)
        return min(_MATCH_PX + abs(self._speed()) * gap, _MAX_MATCH_PX)

    def _coast(self, gap: float) -> float:
        """Carry the track forward on its last speed, bouncing off the
        boards it has been seen to use.

        A straight extrapolation is right for a frame or two and absurd
        after that — a second of it puts a fast defender several hundred
        pixels past a wall. Folding the line back at the track's own bounds
        is the patrol's actual shape, so a coast stays sane however long a
        defender is hidden.
        """
        if not self.patrolling:
            # Nothing known to bounce off yet, so do not run far: a straight
            # line is honest for a frame or two and nonsense after that.
            return self._x[-1] + self._speed() * min(gap, _MAX_SAMPLE_GAP_S)
        low, high = self.bounds            # patrolling implies real bounds
        width = high - low
        folded = (self._x[-1] + self._speed() * gap - low) % (2 * width)
        return low + (folded if folded <= width else 2 * width - folded)

    # ── Its period ───────────────────────────────────────────────────────

    def refit(self):
        """Normalised autocorrelation of this track alone: the lag at which
        it lines up on itself best is its period.

        Each lag is normalised by the energy of the two stretches compared
        rather than by the whole series, so a long lag is not penalised for
        overlapping less and the peak height is a real 0..1 confidence.

        A frozen track is left alone: its period has already been checked
        against reality and passed, and a defender's speed does not change
        while the level does not.
        """
        if self.frozen:
            return
        series = self._resample()
        if series is None:
            return
        centred = series - series.mean()
        count = len(centred)
        low  = int(_MIN_PERIOD_S / _GRID_S)
        high = min(int(_MAX_PERIOD_S / _GRID_S), count // 2)
        if high <= low:
            return

        correlation = np.correlate(centred, centred, mode="full")[count - 1:]
        energy = np.cumsum(centred ** 2)
        if energy[-1] <= 0:
            return
        lags  = np.arange(low, high + 1)
        head  = energy[count - lags - 1]
        tail  = energy[-1] - energy[lags - 1]
        denom = np.sqrt(head * tail)
        valid = denom > 0
        if not valid.any():
            return
        scores = np.where(valid, correlation[lags] / np.where(valid, denom, 1),
                          -1.0)

        # Every whole multiple of the period lines up just as well, so the
        # tallest peak is as likely to be 2T as T and floating-point noise
        # decides between them. Take the first peak, not the highest.
        peak = float(scores.max())
        reached = np.flatnonzero(scores >= peak - _HARMONIC_MARGIN)
        run_end = 1
        while (run_end < len(reached)
               and reached[run_end] == reached[run_end - 1] + 1):
            run_end += 1
        run = reached[:run_end]
        first = int(run[int(np.argmax(scores[run]))])
        self.period = float(self._sharpen(scores, first) * _GRID_S)
        self.confidence = float(scores[first])

    @staticmethod
    def _sharpen(scores: np.ndarray, peak: int) -> float:
        """The peak's position in lags, to a fraction of one.

        The grid is 40ms, so a 1.30s patrol is otherwise fitted as 1.28 —
        and the retro-lookup reads the record 20ms off, which at patrol
        speed is a 11px prediction error out of a 12px budget, on data with
        no noise in it at all. Fitting a parabola through the peak and its
        two neighbours recovers the fraction between grid points.
        """
        low  = int(_MIN_PERIOD_S / _GRID_S)
        here = float(low + peak)
        if not 0 < peak < len(scores) - 1:
            return here
        before, top, after = (float(scores[peak - 1]), float(scores[peak]),
                              float(scores[peak + 1]))
        curve = before - 2 * top + after
        if curve >= 0:          # not a peak after all, leave it on the grid
            return here
        shift = 0.5 * (before - after) / curve
        return here + shift if abs(shift) <= 0.5 else here

    def _resample(self) -> np.ndarray | None:
        if len(self._t) < 4:
            return None
        if self._t[-1] - self._t[0] < 2 * _MIN_PERIOD_S:
            return None
        grid = np.arange(self._t[0], self._t[-1], _GRID_S)
        return np.interp(grid, self._t, self._x)


class RowMotion:
    """One row's defenders, each followed as its own track."""

    def __init__(self, horizon_s: float, left: float = 0.0,
                 width: float = 2000.0):
        self._horizon = float(horizon_s)
        self._tracks: list[_Track] = []
        self._y: deque[float] = deque(maxlen=400)
        self._pending: deque[tuple[float, tuple[float, ...]]] = deque()
        self._errors: deque[float] = deque(maxlen=_ERROR_WINDOW)
        self._best: _Verdict | None = None
        self._green = False
        self._refit_at = 0.0
        self._ticks = 0
        self._seen  = 0
        self._counts: deque[int] = deque(maxlen=60)
        self._sightings: deque[tuple[float, ...]] = deque(maxlen=60)

    # ── Taking observations ──────────────────────────────────────────────

    def feed(self, t: float, xs: list[float], ys: list[float] | None = None):
        """One tick's detections for this row. An empty list is a tick where
        nobody was seen — an occluded helmet is a gap in the record, not a
        defender at zero."""
        self._ticks += 1
        if xs:
            self._seen += 1
        self._counts.append(len(xs))
        self._sightings.append(tuple(float(x) for x in xs))
        for y in (ys or ()):
            self._y.append(float(y))

        self._match(t, [float(x) for x in xs])
        for track in self._tracks:
            track.trim(t)
        self._tracks = [
            track for track in self._tracks
            if track.samples and track.missing <= (
                _ANCHOR_LOST_S if track.anchored or track.frozen
                else _TRACK_LOST_S)]

        self._settle(t)
        self._remember()
        if t - self._refit_at >= _REFIT_EVERY_S:
            self._refit_at = t
            for track in self._tracks:
                track.refit()

        predicted = self.positions(t + self._horizon)
        if predicted:
            self._pending.append((t + self._horizon, tuple(predicted)))

    def _match(self, t: float, xs: list[float]):
        """Hand each detection to the track that expected to be there.

        Closest claim first, one detection per track: two defenders meeting
        at the same spot are told apart by where each was heading, which is
        what _Track.expect carries forward. Anything nobody claims starts a
        track of its own; a track nobody feeds ages out in feed().
        """
        free = list(xs)
        expected = [(index, track.expect(t))
                    for index, track in enumerate(self._tracks)]
        expected = [(index, where) for index, where in expected
                    if where is not None]
        blind = self._merging(t, expected, free)

        claims = []
        for index, where in expected:
            if index in blind:
                continue
            for x in free:
                claims.append((abs(x - where), index, x))
        claims.sort()

        taken_tracks: set[int] = set()
        taken_x: set[float] = set()
        for distance, index, x in claims:
            if (distance > self._tracks[index].reach(t)
                    or index in taken_tracks or x in taken_x):
                continue
            self._tracks[index].see(t, x)
            taken_tracks.add(index)
            taken_x.add(x)

        for index, track in enumerate(self._tracks):
            if index not in taken_tracks:
                # From the clock rather than counted in ticks: the scan rate
                # is whatever the machine manages, and a track that went
                # unseen through a slow stretch was still gone that long.
                track.missing = max(0.0, t - track.last_seen)

        for x in free:
            if x in taken_x:
                continue
            # The blob two hidden defenders share is theirs; it must not
            # hatch a third track that then competes with both of them.
            if any(abs(x - where) < self._tracks[index].reach(t)
                   for index, where in expected if index in blind):
                continue
            self._tracks.append(_Track(t, x))

    def _merging(self, t: float, expected: list[tuple[int, float]],
                 xs: list[float]) -> set[int]:
        """Tracks that have run into each other and cannot be told apart.

        The detector suppresses overlapping helmets, so two defenders that
        close arrive as one centroid between them. Giving it to whichever
        track is nearer drags that one off its patrol by half the gap and
        hands it back to the wrong defender as they part — which is how a
        two-mover row ended up with six tracks and a 260px error. Neither
        takes anything while they are merged; both coast through it.

        Two tests, and both must hold. The pair has to have converged, and
        the detector has to have actually lost one of them: if two blobs
        arrive where two are expected, they were resolved, and blinding a
        pair that is merely passing wrecks the row it was meant to save.

        A pair including a defender who stands still is exempt. He is known
        without looking, so there is nothing for the blinding to protect,
        and blinding a patrol every time it passes in front of him took both
        of them out at once — which is the row emptying itself exactly where
        a shot most needs to know better.
        """
        blind: set[int] = set()
        for first in range(len(expected)):
            one, here = expected[first]
            for other, there in expected[first + 1:]:
                if abs(here - there) >= _AMBIGUOUS_PX:
                    continue
                if (self._tracks[one].anchored
                        or self._tracks[other].anchored):
                    continue
                near = [x for x in xs
                        if min(abs(x - here), abs(x - there))
                        <= max(self._tracks[one].reach(t),
                               self._tracks[other].reach(t))]
                if len(near) >= 2:
                    continue        # the detector told them apart, take it
                blind.update((one, other))
        return blind

    def _settle(self, now: float):
        """Compare predictions whose moment has arrived against what really
        happened. This is the whole verification story: no shot is fired to
        find out whether the model works."""
        while self._pending and self._pending[0][0] <= now:
            due, predicted = self._pending[0]
            latest = max((track._t[-1] for track in self._tracks
                          if track.samples), default=None)
            if latest is not None and due > latest:
                return   # the record has not reached that moment yet
            self._pending.popleft()
            actual = self._seen_at(due)
            if not actual or not predicted:
                continue
            # For each defender that really was there, how close the nearest
            # prediction came to him. This way round, and not the other:
            # scoring predictions against the nearest *sighting* punishes
            # the model for a defender being hidden — the guess for a
            # patrol at 1200 gets measured against the only helmet visible,
            # a stander at 700, and reads as a 500px failure when nothing
            # was wrong. What a shot actually needs to know is whether
            # anybody turned up somewhere unforeseen.
            self._errors.append(float(np.mean(
                [min(abs(seen - guess) for guess in predicted)
                 for seen in actual])))

    def _remember(self):
        """Keep the best window of checks this row has ever managed.

        A patrol's speed is a property of the level and does not drift, so a
        row that once predicted itself to 3px is a row that can be predicted
        to 3px; what moves the rolling average around is how much of the ice
        the camera happened to show — a defender behind another, a run of
        ticks with no detection. Judging on the live window alone meant rows
        took turns being trustworthy and the model never declared itself
        ready with all of them at once (2026-08-10).

        Only the average is remembered. The worst single miss is read live,
        in ready(), because those two numbers answer different questions: a
        thickened average is poor visibility, and one prediction landing a
        third of a patrol away is a wrong period. Remembering the second
        would be the model forgiving itself the one mistake that costs a
        level.

        Dropped outright when the live average goes past twice the
        threshold. The period is a lagging witness — a 30-second buffer
        still reads 4s for half a minute after the level handed the row a
        different patrol — so the fit alone cannot say "this is a different
        row" in time, and the misses can.
        """
        if len(self._errors) < _MIN_CHECKS:
            return
        signature = self._signature()
        now = _Verdict(mean=float(np.mean(self._errors)),
                       worst=max(self._errors),
                       checks=len(self._errors),
                       signature=signature)
        void = (self._best is None
                or not self._best.describes(signature)
                or now.mean > _VERDICT_VOID_PX)
        if void or now.beats(self._best):
            self._best = now

    def _signature(self) -> tuple[float, ...]:
        """What this row currently looks like: one number per defender — its
        period, or zero for one that stands. Both the count and the periods
        matter, so a defender appearing or a patrol changing speed shows up
        as a different row."""
        return tuple(sorted(0.0 if track.still else (track.period or -1.0)
                            for track in self._real_tracks()))

    def verdict(self) -> _Verdict | None:
        """The judgement this row is trusted on: its best, while the fit that
        earned it still stands."""
        if self._best is not None and self._best.describes(self._signature()):
            return self._best
        return None

    def _seen_at(self, t: float) -> list[float]:
        found = []
        for track in self._tracks:
            where = track.at(t)
            if where is not None:
                found.append(where)
        return found

    # ── Reading them back ────────────────────────────────────────────────

    def unfollowed(self) -> tuple[int, float, float] | None:
        """The recent sightings no track claims: how many, and the stretch
        of ice they are spread over.

        A row that refuses because somebody is there and is not being
        followed has to say what it is looking at. A cluster 20px wide is a
        defender the tracker keeps losing; sightings scattered over the
        whole row are something else being read as helmets, and the two
        want opposite fixes.
        """
        claimed = [track.last for track in self._real_tracks()
                   if track.last is not None]
        loose = [x for frame in self._sightings for x in frame
                 if not any(abs(x - where) <= _MATCH_PX for where in claimed)]
        if not loose:
            return None
        return len(loose), min(loose), max(loose)

    def tracks(self) -> list["_Track"]:
        """Every track being followed, settled or not — the detector needs
        the ones still earning their place too, since those are exactly the
        ones a missed frame kills."""
        return list(self._tracks)

    def standing(self) -> list[float]:
        return sorted(track.last for track in self._real_tracks()
                      if track.still and track.last is not None)

    def _real_tracks(self) -> list[_Track]:
        """Tracks seen often enough to be defenders rather than stray blobs
        — a neighbouring row's helmet bobbing over the boundary, a flicker
        of red scenery. One of those cannot be fitted to any patrol, so a
        row containing one would never make a prediction and never gather a
        single check."""
        if self._ticks < _MIN_STILL_FRAMES:
            return list(self._tracks)
        needed = _MIN_TRACK_SHARE * min(self._ticks, _BUFFER_S / _GRID_S)
        return [track for track in self._tracks if track.samples >= needed]

    def has_mover(self) -> bool:
        return any(not track.still for track in self._real_tracks())

    def occupants(self) -> int:
        """How many defenders this row seems to hold — the most it shows in
        a decent share of recent ticks.

        Not the most it has ever shown. A single blob anywhere in the last
        two seconds used to be enough, so a puck crossing an empty row, or
        the flash of a goal, left the row "occupied" for as long as any
        stray red kept trickling through it. Nor the most it shows in
        *every* tick: a real defender is missed constantly, and the rows in
        this game run at 63–89% detection.
        """
        if not self._counts:
            return 0
        needed = max(1, int(_MIN_TRACK_SHARE * len(self._counts)))
        for count in range(max(self._counts), 0, -1):
            if sum(seen >= count for seen in self._counts) >= needed:
                return count
        return 0

    def positions(self, t_future: float) -> list[float] | None:
        """Everyone in this row at `t_future`, or None when any of them
        cannot be predicted — which the caller must treat as "do not shoot"
        rather than as "the row is clear"."""
        if self._ticks < _MIN_STILL_FRAMES:
            # A helmet seen once is not a prediction: it may be parked or it
            # may be crossing the row at speed, and nothing here can tell
            # yet. Until the row has settled there is no answer to give.
            return None
        tracks = self._real_tracks()
        if not self._green and len(tracks) < self.occupants():
            # Fewer followed than are actually there. "[]" would read as a
            # clear row and put a shot through whoever is not being tracked.
            #
            # Only before the row is calibrated. Afterwards its crew is a
            # settled fact and a stray blob drifting through is not a sixth
            # defender — treating one as proof the row had gone unreadable
            # took a calibrated row back off the board.
            return None
        if not tracks:
            return []
        found = []
        for track in tracks:
            where = track.predict(t_future)
            if where is None:
                return None
            found.append(where)
        return found

    def split_positions(self, t_future: float
                        ) -> tuple[list[float], list[float]] | None:
        """The same answer as positions(), told apart: those who stand and
        those who will have moved. Drawn differently, because they mean
        different things — one is a measurement, the other a prediction that
        could be wrong."""
        places = self.positions(t_future)
        if places is None:
            return None
        fixed = [track.last for track in self._real_tracks()
                 if track.anchored and track.last is not None]
        moving = [x for x in places
                  if not any(abs(x - spot) < 1.0 for spot in fixed)]
        return sorted(fixed), sorted(moving)

    def predict(self, t_future: float) -> float | None:
        """Where *the* moving defender will be, when there is exactly one.

        A convenience over positions() for the common single-patrol row and
        for saying so in the report. None when the row holds no mover, or
        more than one — then only the whole picture means anything.
        """
        moving = [track for track in self._real_tracks() if not track.still]
        if len(moving) != 1:
            return None
        return moving[0].predict(t_future)

    # ── Verdict ──────────────────────────────────────────────────────────

    def error(self) -> float | None:
        """The error this row is judged on — its best under the present fit,
        falling back to the live window while it has yet to earn one."""
        verdict = self.verdict()
        if verdict is not None:
            return verdict.mean
        return self.live_error()

    def live_error(self) -> float | None:
        return float(np.mean(self._errors)) if self._errors else None

    def ready(self) -> bool:
        """Whether this row may be planned against.

        Latching: once a row has proved itself, it stays proved and its
        patrol's period is frozen at the fit that did it. A defender's speed
        is a property of the level and does not change, so a row going
        green, then amber, then red, then green again is the *measurement*
        wobbling with visibility, not the rink. Re-deciding every five
        seconds meant a row could be lost right after the model announced it
        had converged, and no shot was ever available at the same moment on
        all five rows (2026-08-10).

        The latch is released only where the situation genuinely changed —
        see relearn(), which the level change calls.
        """
        if self._green:
            return True
        if self._ticks < _MIN_STILL_FRAMES:
            return False
        if self.occupants() == 0:
            return True          # nothing to dodge, and nothing to latch
        if len(self._real_tracks()) < self.occupants():
            # Somebody is in this row in most frames and is not being
            # followed. Falling through would reach has_mover(), find no
            # mover among no tracks, and call the row ready with nothing to
            # predict — which is how the model announced it had converged
            # while two rows had never been modelled at all (2026-08-10).
            return False
        if not self.has_mover():
            return self._latch()  # nothing that moves, so nothing to prove
        verdict = self.verdict()
        if verdict is None or not verdict.passes:
            return False
        # The average is the row's best; the wildest miss is always the one
        # that just happened.
        if not self._errors or max(self._errors) >= _MAX_WORST_PX:
            return False
        return self._latch()

    def _latch(self) -> bool:
        """Take the row as proved, and freeze every patrol in it at the fit
        that proved it. Nothing is refitted afterwards: a later fit can only
        be a worse reading of the same unchanging speed."""
        self._green = True
        for track in self._tracks:
            track.freeze()
        return True

    def relearn(self):
        """Forget the latch. For a new level, where the defenders really are
        different and everything known about the old ones is a lie."""
        self._green = False
        self._best = None
        self._errors.clear()
        self._pending.clear()
        self._tracks.clear()
        self._counts.clear()
        self._sightings.clear()
        self._ticks = 0
        self._seen = 0

    def census(self) -> tuple[int, int]:
        """(standing, moving) — what this row holds, for the head count the
        watch takes before it tries to calibrate anything."""
        tracks = self._real_tracks()
        still = sum(1 for track in tracks if track.still)
        return still, len(tracks) - still

    def report(self, index: int) -> RowReport:
        tracks = self._real_tracks()
        verdict = self.verdict()
        movers = [track for track in tracks if not track.still]
        # The widest patrol is the one worth naming when there are several.
        lead = max(movers, key=lambda track: track.span, default=None)
        bounds = lead.bounds if lead is not None else None
        speed = None
        if lead is not None and lead.period and bounds is not None:
            speed = 2 * (bounds[1] - bounds[0]) / lead.period
        return RowReport(
            index=index,
            frames=self._ticks,
            seen_share=self._seen / self._ticks if self._ticks else 0.0,
            # What is seen, not what is followed: a row nobody is following
            # has to stay on screen and say so, and `empty` is what decides
            # whether it is printed at all.
            occupants=self.occupants(),
            tracked=len(tracks),
            settled=self._ticks >= _MIN_STILL_FRAMES,
            fitted=(lead is None or (lead.period is not None
                                     and lead.confidence >= _MIN_CONFIDENCE)),
            standing=self.standing(),
            mean_y=float(np.mean(self._y)) if self._y else None,
            period=lead.period if lead is not None else None,
            confidence=lead.confidence if lead is not None else 0.0,
            min_x=bounds[0] if bounds else None,
            max_x=bounds[1] if bounds else None,
            speed=speed,
            error=self.error(),
            worst=max(self._errors) if self._errors else None,
            checks=(verdict.checks if verdict is not None
                    else len(self._errors)),
            live_error=self.live_error(),
            loose=self.unfollowed(),
            ready=self.ready(),
        )


class MotionModel:
    """One RowMotion per row, fed straight from a scan."""

    def __init__(self, geom, horizon_s: float):
        self._horizon = horizon_s
        self._rows = {lane.index: RowMotion(horizon_s, geom.left, geom.width)
                      for lane in geom.lanes}

    @property
    def horizon(self) -> float:
        return self._horizon

    def feed(self, found: dict, now: float):
        for index, row in self._rows.items():
            hits = found.get(index) or []
            row.feed(now, [hit.x for hit in hits], [hit.y for hit in hits])

    def positions(self, index: int, t_future: float) -> list[float] | None:
        row = self._rows.get(index)
        return row.positions(t_future) if row is not None else None

    def split_positions(self, index: int, t_future: float
                        ) -> tuple[list[float], list[float]] | None:
        row = self._rows.get(index)
        return row.split_positions(t_future) if row is not None else None

    def census(self) -> list[tuple[int, int, int]]:
        """(row index, standing, moving) for every row."""
        return [(index, *row.census())
                for index, row in sorted(self._rows.items())]

    def relearn(self):
        for row in self._rows.values():
            row.relearn()

    def expectations(self, t: float) -> dict[int, list[float]]:
        """Where every followed defender should be right now, by row — what
        the detector uses to hold a helmet it is already expecting to a
        lower bar than one appearing out of nowhere."""
        return {index: [where for where in
                        (track.expect(t) for track in row.tracks())
                        if where is not None]
                for index, row in self._rows.items()}

    def ready(self) -> bool:
        """Every row predictable. Not "most" — a shot has to clear all of
        them, so one unpredictable row makes the whole plan a guess."""
        return bool(self._rows) and all(row.ready()
                                        for row in self._rows.values())

    def reports(self) -> list[RowReport]:
        return [row.report(index) for index, row in sorted(self._rows.items())]
