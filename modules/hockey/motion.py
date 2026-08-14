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
from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import dataclass, field

import numpy as np

# Everything here is measured in seconds, never in frames.
#
# It used to be both, and the frame counts were all tuned against the ~13 a
# second PrintWindow managed. Windows Graphics Capture hands over some 40,
# which rescaled every one of them at once and without saying so: the window
# a verdict is earned over shrank from a shot's flight to a third of it, and
# the record a zone reads its boards off stopped holding a whole sweep of a
# slow patrol. A number of frames is not a fact about the rink. How long
# something was watched for is.

# How much history is kept. Two full patrols is the minimum the period
# search can work with at all, and more only makes it sharper, so this is
# generous — a defender crossing the rink and back takes seconds, not tens
# of them.
_BUFFER_S = 30.0

# Samples arrive on a timer, so their spacing wobbles; the period search
# needs an even one. Everything is resampled onto this grid first.
#
# Finer than the old 40ms, which was already coarser than the frames now
# arriving: interpolating real 25ms samples up onto a 40ms grid throws away
# the resolution that was the point of capturing faster. The grid is what
# the period is read off, and _sharpen notes what a coarse one costs.
_GRID_S = 0.025

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

# How long anything has to be watched before it is judged at all — whether a
# row has settled, whether a defender is standing rather than dawdling at a
# board, whether a track is a defender or a stray blob.
_MIN_STILL_S = 3.0

# And the fewest samples a track may claim that on. Detection runs at 63-89%,
# so three seconds is always tens of sightings; this only rules out the
# degenerate track that was seen twice, three seconds apart, and has a span
# to show for it but no record in between.
_MIN_STILL_SAMPLES = 12

# How long a stander's position is averaged over before it is reported. He
# does not move, so this is pure smoothing of the centroid's own jitter.
_STILL_MEAN_S = 2.3

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

# How much of the record a *position* has to be occupied in before somebody
# is taken to be standing there. Half: a defender who never moves is in
# nearly every frame, and a patrol passing through the same spot is there
# for a few frames of each sweep.
_STANDING_SHARE = 0.50

# How far back "the record" reaches — every sighting of this row, whoever it
# was of. Longer than the slowest patrol the fit will look for, because this
# is what a zone's boards get read off and half a sweep gives half a board.
_SIGHTING_WINDOW_S = 16.0

# And how far back the head count looks. Shorter on purpose: how many
# defenders a row holds is a fact about the level, so the recent past is the
# whole of the evidence and a long memory only carries the last one in.
_COUNT_WINDOW_S = 4.5

# How long the defender must be watched for on the way in and on the way out
# before the point between them counts as a turn, and the fewest detections
# that may say so. A patrol too fast for its zone never manages them, and
# then nothing is claimed at all — which is the right answer, not a defect.
#
# Both halves, and for once neither is redundant. The counts alone were what
# this asked for, and they were tuned at the ~13 frames a second PrintWindow
# managed, where four of them are a third of a second of approach; at the
# ~40 Windows Graphics Capture hands over, the same four are a tenth, and a
# centroid wobbling at the deep end of the zone clears them. That is the
# failure this was written against in the first place (2026-08-10: row 5's
# period flickering between 1.21, 1.31 and 3.63 seconds). The durations are
# what those counts used to mean, so the gate stays where it was tuned
# whatever rate the frames arrive at, while the counts still rule out a turn
# read off two lonely detections either side of a long gap.
_MIN_ZONE_SAMPLES = 4
_MIN_RETREAT      = 2
_MIN_APPROACH_S   = 0.30
_MIN_RETREAT_S    = 0.15

# And what the fallback's own predictions may be missing by before its
# timing is thrown away and measured again — three times the gate below. A
# reading this far out is not a rough one, it is a wrong one.
_HOPELESS_PX = 36.0

# How far a turn may sit from a whole number of periods and still be read
# as belonging to that clock. A turn is timed to about a frame, so this is
# already generous — and it has to stay tight, because a loose grid lets one
# patrol's clock swallow the odd turn of another's whenever their multiples
# happen to line up (3.4x5 against 2.08x8 agree to 0.36s, 2026-08-11).
_BOUNCE_SLACK = 0.08

# Turns needed before the gap between them counts as a period. Three is two
# measurements of it, which is the least that can disagree.
_MIN_BOUNCES = 3

# Following a defender across the zone: how far a detection may sit from
# where a chain left off, on top of whatever it was travelling at.
_ZONE_MATCH_PX = 40.0

# How closely two turns have to agree on speed to be the same defender. Two
# patrols sharing a board differ by much more than this; the same one
# differs by much less, since speed is the thing about it that never changes.
_SPEED_TOL = 0.18

# A row holds at most this many patrols, and this many turns are kept to
# work them out from — enough for three cycles of each of three defenders.
_MAX_PATROLS = 3
_TURN_MEMORY = 24

# How far short of the board a turn may be and still count as one. Beyond
# it, the turn was read off two defenders merged into one blob — a centroid
# that turns round where neither of them did.
_TURN_SPREAD_PX = 22.0

# A stretch of ice narrower than this is not worth watching: the patrol
# would be in and out of it inside a couple of frames, and a turn needs to
# be seen approaching and receding to be a turn at all.
_MIN_ZONE_PX = 60.0

# How long a row may go on failing to calibrate before its turns are counted
# alongside, whatever is standing in it. Ten seconds is two or three sweeps
# of a typical patrol — long enough that the trackers have genuinely had
# their chance, short enough not to spend a level waiting.
_ZONE_AFTER_S = 10.0

# And how much of the patrol's own span such a zone takes, at one end of it.
_ZONE_SHARE = 0.35

# The zone has an inner edge and no outer one. Cut it off at the deepest
# point anybody has been seen and the defender turns round just past it: he
# reaches the edge, leaves, and comes back — two chains, neither containing
# a turn, and the row waited for ever on "1 of 3" (2026-08-11).
_ZONE_OPEN_PX = 400.0

# The widest hole in a track that may still be interpolated across. A
# defender turns round at the boards in well under this, so a straight line
# drawn over a longer gap can miss the turn entirely — see _Track.at.
_MAX_SAMPLE_GAP_S = 0.30

# How far back a track's speed is measured over — the one place where a
# count of samples is still the right thing to ask for, alongside a limit in
# seconds rather than instead of it. They bound different mistakes and the
# window is whichever of them is shorter.
#
# Too few samples and the "speed" is the jitter of two helmet centroids. Too
# long a stretch and it is averaged across a turn at the boards, which is
# where it is read most and matters most: a patrol of period 1.3s spends
# 0.65s on a sweep, so half a second of history spans a reversal and reports
# a defender at rest. That second failure is the one the faster capture
# actually cures — six samples is half a second of PrintWindow and 0.15s of
# WGC, so the same six now fit comfortably inside a sweep.
_SPEED_SAMPLES  = 6
_SPEED_WINDOW_S = 0.45

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
_ERROR_WINDOW_S = 2.3
_MAX_ERROR_PX = 12.0

# Comparisons needed before the error means anything, and — the half that
# has to be counted in seconds — how much of the patrol they have to be
# spread over. Either alone is cheap to satisfy and says nothing: fifteen
# checks off consecutive frames at 40 a second all describe the same third
# of a second of one sweep, and a mean over that is not evidence about a
# patrol but about one moment of it.
_MIN_CHECKS       = 15
_MIN_CHECK_SPAN_S = 1.15

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


def _trim(record: deque, now: float, window: float):
    """Drop everything older than `window` from a deque of (t, value).

    What every record in this module is bounded by, in place of a maxlen. A
    maxlen counts frames, and how many frames a second arrive is a fact
    about the capture backend rather than about the rink.
    """
    cutoff = now - window
    while record and record[0][0] < cutoff:
        record.popleft()


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
    # Timed by its bounces off a board instead of by following it: how many
    # turns have been seen, and the period they give.
    bounces: int = 0
    bounce_period: float | None = None
    # (period, speed) for every patrol the zone has timed, fastest last.
    zone_patrols: list = field(default_factory=list)
    # Room a plan has to leave on this row on top of the bodies, because
    # this is how far its own predictions have been known to miss by.
    slack: float = 0.0
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
    def watched(self) -> float:
        """Seconds between this track's first surviving sample and its last.
        Not its age: the record is trimmed to _BUFFER_S behind it."""
        return (self._t[-1] - self._t[0]) if len(self._t) > 1 else 0.0

    def samples_since(self, t: float) -> int:
        """How often this track was seen from `t` onwards — the numerator of
        every "in what share of the record" question asked about it."""
        return len(self._t) - bisect_left(self._t, t)

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
        has to be worked out again: he is where he has always been.

        Long enough is three seconds, not forty frames — at WGC's rate those
        are a second and a half of standing, which a patrol dawdling at a
        board manages without being a stander at all.
        """
        return (self.still and self.watched >= _MIN_STILL_S
                and self.samples >= _MIN_STILL_SAMPLES)

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
            return self._settled_x()
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

    def rough(self, t: float) -> float:
        """Where it will be, on the best evidence there is rather than on
        proof. Its period when it has one — confident or not, since an
        unconfident fit is still the only thing measured about it — and
        otherwise the speed it was last seen at, folded inside its own
        bounds so it stays on the ice."""
        if self.still:
            return self._settled_x()
        if self.period:
            steps = math.ceil((t - self._t[-1]) / self.period)
            for back in range(steps, steps + _MAX_LOOKBACKS):
                where = self.at(t - back * self.period)
                if where is not None:
                    return where
        return self._coast(max(0.0, t - self._t[-1]))

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

    def _settled_x(self) -> float:
        """Where a track that does not move is, its jitter averaged out."""
        return float(np.mean(self._x[self._back_to(_STILL_MEAN_S):]))

    def _back_to(self, window: float) -> int:
        """The first sample no older than `window`, but never the last one
        on its own — two points are what any of this is measured from."""
        if len(self._t) < 2:
            return 0
        cutoff = self._t[-1] - window
        return min(bisect_right(self._t, cutoff), len(self._t) - 2)

    def _speed(self) -> float:
        """Recent px per second, over the shorter of a few samples and a
        fraction of a second — see _SPEED_SAMPLES and _SPEED_WINDOW_S. Both
        are ceilings on how far back to reach, so the later of the two
        starting points wins.
        """
        if len(self._t) < 2:
            return 0.0
        back = max(len(self._t) - _SPEED_SAMPLES,
                   self._back_to(_SPEED_WINDOW_S))
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


class _BoardPatrol:
    """A patrol timed by its bounces off one board.

    For a row too crowded to follow anybody through — a patrol crossing two
    standing defenders merges with each of them in turn, and every track in
    the row comes apart (2026-08-10). What survives that is the geometry:
    the standing defenders split the row into stretches of ice, and the
    stretch that reaches a board holds nobody but the patrol. Inside it the
    patrol is unmistakable, and it does one unmistakable thing — it runs out
    of ice and turns round.

    The gap between two of those turns is exactly one period, out to the far
    board and back. With the period, the moment of the turn and the boards
    the row was calibrated with, the patrol is known everywhere, including
    the two thirds of its sweep this zone never sees.
    """

    def __init__(self, near: float, far: float, toward_right: bool,
                 period: float, last_turn: float, speed: float, turns: int):
        self._near = float(near)      # where it really runs out of ice
        self._far  = float(far)       # the other end of the patrol
        self._toward_right = toward_right
        self._period = float(period)
        self._last = float(last_turn)
        self.speed = abs(speed)       # px/s, from the same turns
        self._turns = int(turns)

    @property
    def period(self) -> float | None:
        return self._period

    @property
    def bounces(self) -> int:
        return self._turns

    def predict(self, t: float) -> float | None:
        """Where the patrol is at `t`, from the last turn it was seen to
        make. Straight there and straight back: this is the shape the game
        draws, and the zone confirms it every cycle."""
        period = self._period
        if not period:
            return None
        phase = (t - self._last) % period
        share = phase / (period / 2)
        if share <= 1:
            return self._near + (self._far - self._near) * share
        return self._far - (self._far - self._near) * (share - 1)


def _merge_clusters(groups: list) -> list:
    """Put back together the clusters that are one defender.

    A greedy pass takes the turns it can reach in one go, so a patrol whose
    turns were interrupted — by merging with the other one, by a blind
    stretch — comes out as two sequences on the same clock at the same
    phase. Two *different* defenders can share a period, but not a period
    and a phase: that would put them in the same place at the same time.
    """
    merged: list = []
    for period, turns in groups:
        for index, (other_period, other_turns) in enumerate(merged):
            union = sorted(other_turns + turns, key=lambda item: item[0])
            fitted = _fit_period(union, min(period, other_period))
            if fitted is None:
                continue
            merged[index] = (fitted, union)
            break
        else:
            merged.append((period, turns))
    return merged


def _fit_period(turns: list, seed: float) -> float | None:
    """A period every one of these turns lands on, or None for "not one
    defender".

    Refitted over the whole span rather than trusting either cluster's own
    number. Each is read off a pair of turns and is a fraction of a percent
    out; over the twenty seconds between two clusters of the same defender
    that fraction becomes a quarter of a second of phase, and the two look
    like strangers (2026-08-11).
    """
    span = turns[-1][0] - turns[0][0]
    cycles = round(span / seed) if seed > 0 else 0
    if cycles < 1:
        return None
    period = span / cycles
    if not _MIN_PERIOD_S <= period <= _MAX_PERIOD_S:
        return None
    anchor = turns[0][0]
    if all(_on_grid(t, anchor, period) for t, _x, _speed in turns):
        return period
    return None


def _on_grid(t: float, anchor: float, period: float) -> bool:
    """Whether a turn at `t` lands on the clock `anchor` + k periods."""
    cycles = round((t - anchor) / period)
    return abs(t - anchor - cycles * period) <= _BOUNCE_SLACK * period


class _Zone:
    """One stretch of ice at a board, and every patrol that turns in it.

    A row can hold two moving defenders, and both of them come through the
    same stretch — so taking whichever detection is deepest in it each frame
    reads one pass as the other's. Instead every detection is followed
    across the zone as its own chain, each finished chain gives one turn,
    and turns are filed against the patrol whose speed they match. Two
    defenders sharing a board are told apart by how fast they arrive at it,
    which is the one thing about them that does not change.
    """

    def __init__(self, low: float, high: float, toward_right: bool,
                 far: float):
        self.low, self.high = float(low), float(high)
        self.toward_right = bool(toward_right)
        self._far = float(far)
        self._turns: deque[tuple[float, float, float]] = deque(
            maxlen=_TURN_MEMORY)      # (t, x, speed)
        self._chains: list[list[tuple[float, float]]] = []
        self._last_t = 0.0
        self._cached: tuple[int, list] = (-1, [])

    # ── Watching it ──────────────────────────────────────────────────────

    def feed(self, t: float, xs: list[float]):
        """Detections inside the zone this tick, standers already removed."""
        gap = t - self._last_t if self._last_t else 0.0
        self._last_t = t

        free = sorted(xs)
        matched = []
        for chain in self._chains:
            if not free:
                break
            reach = _ZONE_MATCH_PX + abs(self._pace(chain)) * max(gap, 0.0)
            nearest = min(free, key=lambda x: abs(x - chain[-1][1]))
            if abs(nearest - chain[-1][1]) <= reach:
                chain.append((t, nearest))
                free.remove(nearest)
                matched.append(chain)
        for x in free:
            matched.append([(t, x)])

        for chain in self._chains:
            if chain not in matched:
                self._close(chain)
        self._chains = matched

    @staticmethod
    def _pace(chain: list) -> float:
        if len(chain) < 2:
            return 0.0
        elapsed = chain[-1][0] - chain[0][0]
        return (chain[-1][1] - chain[0][1]) / elapsed if elapsed > 0 else 0.0

    def _close(self, chain: list):
        """One pass through the zone, judged whole.

        A turn is only a turn if it was watched in and watched out again.
        Reading it a frame at a time called every wobble a turn: a fast
        patrol crosses a narrow zone in two or three frames, and row 5's
        period flickered between 1.21, 1.31 and 3.63 seconds from one status
        line to the next (2026-08-10). Demanding a stretch of ice either side
        of the extreme also settles, for free, whether this zone can time
        this patrol at all — too fast or too narrow and no chain ever
        qualifies, so nothing is claimed.
        """
        if len(chain) < _MIN_ZONE_SAMPLES + _MIN_RETREAT:
            return
        pick = max if self.toward_right else min
        turn = pick(range(len(chain)), key=lambda i: chain[i][1])
        if turn < _MIN_ZONE_SAMPLES or len(chain) - turn - 1 < _MIN_RETREAT:
            return                    # only saw him arrive, or only leave
        if (chain[turn][0] - chain[0][0] < _MIN_APPROACH_S
                or chain[-1][0] - chain[turn][0] < _MIN_RETREAT_S):
            return                    # too brief either side to be a turn
        t, x = chain[turn]
        elapsed = t - chain[0][0]
        speed = abs(x - chain[0][1]) / elapsed if elapsed > 0 else 0.0
        self._file(t, x, speed)

    def _file(self, t: float, x: float, speed: float):
        self._turns.append((t, x, abs(speed)))

    def sweep(self):
        """Nothing to sweep: patrols are worked out afresh from the turns
        themselves, so one that has stopped turning up simply stops being
        found. Kept as a no-op because the caller does not need to know."""

    # ── What it knows ────────────────────────────────────────────────────

    def patrols(self) -> list[_BoardPatrol]:
        """The periodic sequences hiding in the turns, one per defender.

        Not built up turn by turn. Two defenders share this board and their
        turns arrive interleaved, so the first patrol's first two can be one
        from each — which fixes a period that then attracts every turn that
        happens to fit it, and the whole row settles on a number belonging
        to nobody (2026-08-11: one patrol at 6.74s where the truth was 2.1
        and 3.4). Instead every pair of turns proposes a period, the one the
        most other turns agree with wins, its turns are taken out, and what
        is left is offered the same deal. A defender is whatever the timing
        says is regular.
        """
        if self._cached[0] == len(self._turns):
            return self._cached[1]
        found = self._cluster()
        self._cached = (len(self._turns), found)
        return found

    def _cluster(self) -> list[_BoardPatrol]:
        left = list(self._turns)
        found = []
        while len(left) >= _MIN_BOUNCES and len(found) < _MAX_PATROLS:
            best = None
            for i in range(len(left)):
                for j in range(i + 1, len(left)):
                    period = left[j][0] - left[i][0]
                    if not _MIN_PERIOD_S <= period <= _MAX_PERIOD_S:
                        continue
                    members = [k for k, turn in enumerate(left)
                               if _on_grid(turn[0], left[i][0], period)]
                    if len(members) < _MIN_BOUNCES:
                        continue
                    if best is None or len(members) > len(best[1]):
                        best = (period, members)
            if best is None:
                break
            period, members = best
            found.append((period, [left[k] for k in members]))
            taken = set(members)
            left = [turn for k, turn in enumerate(left) if k not in taken]
        return [self._patrol_from(turns, period)
                for period, turns in _merge_clusters(found)]

    def _patrol_from(self, turns: list, period: float) -> _BoardPatrol:
        """One defender, from the turns that agreed on his clock.

        The period is refined over the whole run — but by *cycle number*,
        not by how many turns were seen. Half of them are missed whenever
        two defenders merge on their way through, so dividing the span by
        the count reads the average gap instead of the period: 4.31 seconds
        where the truth was 3.39 (2026-08-11). The first and last turn and
        how many cycles apart they are is right however many went unseen in
        between.
        """
        # A turn read off a merged blob stops short of the board and is
        # early: the centroid of two defenders turns round where neither of
        # them did. Those are the ones that sit away from the board, so the
        # ones that reached it are the ones to trust.
        deep = float(np.median([turn[1] for turn in turns]))
        clean = [turn for turn in turns
                 if abs(turn[1] - deep) <= _TURN_SPREAD_PX] or turns

        times = [turn[0] for turn in clean]
        cycles = round((times[-1] - times[0]) / period)
        if cycles >= 1:
            period = (times[-1] - times[0]) / cycles

        # The anchor from the whole run rather than from the last turn
        # alone: one bad sample there shifts every prediction by its own
        # error, and the row read fifty pixels out of phase (2026-08-11).
        marks = [round((t - times[0]) / period) for t in times]
        start = float(np.mean([t - k * period for t, k in zip(times, marks)]))
        # The deepest turn, not the typical one: the patrol does reach the
        # board, so the furthest it was ever seen is the least wrong guess
        # at where it turns, and a median only averages in the frames that
        # caught it on its way there.
        deepest = [turn[1] for turn in clean]
        near = max(deepest) if self.toward_right else min(deepest)
        speed = float(np.median([turn[2] for turn in clean]))
        return _BoardPatrol(near, self._far, self.toward_right, period,
                            start + marks[-1] * period, speed, len(turns))

    @property
    def bounces(self) -> int:
        return len(self._turns)

    @property
    def period(self) -> float | None:
        return max((p.period for p in self.patrols()), default=None)

    def predict(self, t: float) -> list[float] | None:
        found = [p.predict(t) for p in self.patrols()]
        return None if not found or any(x is None for x in found) else found

    def forget(self):
        self._turns.clear()
        self._chains = []
        self._cached = (-1, [])


class RowMotion:
    """One row's defenders, each followed as its own track."""

    def __init__(self, horizon_s: float, left: float = 0.0,
                 width: float = 2000.0):
        self._horizon = float(horizon_s)
        self._tracks: list[_Track] = []
        self._y: deque[tuple[float, float]] = deque()
        self._pending: deque[tuple[float, tuple[float, ...]]] = deque()
        self._errors: deque[tuple[float, float]] = deque()
        self._best: _Verdict | None = None
        self._green = False
        self._zone: tuple[float, float, bool] | None = None   # lo, hi, right
        self._board: _Zone | None = None
        # Whether the zone was the one answering last tick — see
        # _doubt_the_board, which empties the error window at every handover.
        self._board_answering = False
        # Predict this row from its board zone and nothing else — see
        # MotionModel.orange.
        self.orange = False
        # Answer with the best available rather than only with the proved —
        # see settle_for_now.
        self._rough = False
        self._refit_at = 0.0
        self._ticks = 0
        self._seen  = 0
        self._last_tick = 0.0
        # (t, how many were seen) per tick, for the head count.
        self._counts: deque[tuple[float, int]] = deque()
        # (t, xs) per tick — the timestamp is what _seen_at needs. Long
        # enough to hold a whole sweep of a slow patrol, since the span of
        # what has been seen is where a zone's boards come from.
        self._sightings: deque = deque()
        # Standing spots as of the last tick, or None when they have to be
        # worked out again — see standing_spots.
        self._spots: list[float] | None = None
        self._first_seen: float | None = None

    # ── Taking observations ──────────────────────────────────────────────

    def feed(self, t: float, xs: list[float], ys: list[float] | None = None):
        """One tick's detections for this row. An empty list is a tick where
        nobody was seen — an occluded helmet is a gap in the record, not a
        defender at zero."""
        self._ticks += 1
        if self._first_seen is None:
            self._first_seen = t
        self._last_tick = t
        if xs:
            self._seen += 1
        self._counts.append((t, len(xs)))
        self._sightings.append((t, tuple(float(x) for x in xs)))
        for y in (ys or ()):
            self._y.append((t, float(y)))
        _trim(self._counts, t, _COUNT_WINDOW_S)
        _trim(self._sightings, t, _SIGHTING_WINDOW_S)
        _trim(self._y, t, _BUFFER_S)
        self._spots = None      # the record moved; the tally is stale

        if self._board is not None and self._zone is not None:
            low, high, _right = self._zone
            # A standing defender inside the zone would sit there for ever
            # and no chain would ever end, so no turn could be read at all.
            # He is known by counting anyway; the zone is for the ones that
            # move.
            parked = self.standing_spots()
            self._board.feed(t, [
                float(x) for x in xs if low <= x <= high
                and not any(abs(x - spot) <= _STILL_SPAN_PX * 2
                            for spot in parked)])
            self._board.sweep()

        self._match(t, [float(x) for x in xs])
        for track in self._tracks:
            track.trim(t)
        self._tracks = [
            track for track in self._tracks
            if track.samples and track.missing <= (
                _ANCHOR_LOST_S if track.anchored or track.frozen
                else _TRACK_LOST_S)]

        self._settle(t)
        self._doubt_the_board()
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
        _trim(self._errors, now, _ERROR_WINDOW_S)
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
            if len(actual) < len(predicted):
                # Fewer blobs than defenders: some of them are merged, and
                # the centroid between two is nobody. Scoring against it
                # charges the model half a helmet for the detector's doing.
                continue
            # For each defender that really was there, how close the nearest
            # prediction came to him. This way round, and not the other:
            # scoring predictions against the nearest *sighting* punishes
            # the model for a defender being hidden — the guess for a
            # patrol at 1200 gets measured against the only helmet visible,
            # a stander at 700, and reads as a 500px failure when nothing
            # was wrong. What a shot actually needs to know is whether
            # anybody turned up somewhere unforeseen.
            self._errors.append((due, float(np.mean(
                [min(abs(seen - guess) for guess in predicted)
                 for seen in actual]))))

    def _error_values(self) -> list[float]:
        """The misses alone, without the moments they were measured at."""
        return [error for _due, error in self._errors]

    def _checks_ready(self) -> bool:
        """Whether the checks in hand say anything yet: enough of them, and
        spread over enough of a patrol to be about the patrol rather than
        about one moment of it — see _MIN_CHECK_SPAN_S."""
        if len(self._errors) < _MIN_CHECKS:
            return False
        return self._errors[-1][0] - self._errors[0][0] >= _MIN_CHECK_SPAN_S

    def _doubt_the_board(self):
        """Throw the bounce timing away when its own predictions do not hold.

        The fallback took the row over on the strength of having a period at
        all, and then kept it whatever that period turned out to be worth —
        row 5 sat on 45 to 167 pixels of error for a minute, still
        announcing a period every five seconds (2026-08-10). Being wrong is
        allowed; going on being wrong without noticing is not. Cleared
        rather than switched off: with the turns re-counted it either
        measures something better or measures nothing, and nothing is an
        honest answer that puts the row back on its trackers.

        Whose misses these are has to be settled first. The row swaps
        predictors the moment a third turn makes a patrol — and the window
        it is judged on still holds the misses whoever answered *before*
        made. Judging the board on those is what the guard below was for,
        but the guard is read now and the misses were made then: at 40
        frames a second the board was condemned the tick it arrived, over
        and over, and never held three turns long enough to be worth
        anything. So the window is emptied at the handover, and each of them
        answers for its own.

        The None is real and not defensive: in orange mode a row where
        everybody stands is ready without a zone at all, since there is
        nothing in it to time (2026-08-11).
        """
        answering = self._board is not None and self.by_board_ready()
        if answering != self._board_answering:
            self._board_answering = answering
            self._errors.clear()
            self._pending.clear()
            return
        if not answering:
            return
        if not self._checks_ready():
            return
        if float(np.mean(self._error_values())) <= _HOPELESS_PX:
            return
        self._board.forget()
        self._errors.clear()
        # And the predictions still in flight, which were made with the
        # timing just thrown away: left in, they come due over the next
        # second and a half and condemn the new timing for the old one's
        # mistakes.
        self._pending.clear()

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
        if not self._checks_ready():
            return
        signature = self._signature()
        errors = self._error_values()
        now = _Verdict(mean=float(np.mean(errors)),
                       worst=max(errors),
                       checks=len(errors),
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
        """What the detector reported at `t`, as reported.

        Not what the tracks say was there. The tracks are the thing being
        checked, and in a row where they have come apart they are also the
        thing that came apart — scoring a prediction against them is asking
        the answer to mark its own paper. A row predicted correctly to
        within 8px was condemned at 38 by its own broken trackers
        (2026-08-10).
        """
        best, gap = None, None
        for when, xs in self._sightings:
            distance = abs(when - t)
            if gap is None or distance < gap:
                best, gap = xs, distance
        if best is None or gap > _MAX_SAMPLE_GAP_S:
            return []
        return list(best)

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
        loose = [x for _when, frame in self._sightings for x in frame
                 if not any(abs(x - where) <= _MATCH_PX for where in claimed)]
        if not loose:
            return None
        return len(loose), min(loose), max(loose)

    def watch_zone(self, low: float, high: float, toward_right: bool,
                   far: float):
        """Watch one stretch of ice for a patrol turning round in it.

        `low`..`high` is a stretch reaching a board and holding no standing
        defender; `far` is the other end of the patrol. Set once and left
        alone — restarting the count would throw away the turns already
        timed.
        """
        if self._zone is not None:
            return
        self._zone = (float(low), float(high), bool(toward_right))
        self._board = _Zone(low, high, toward_right, far)

    def zone(self) -> tuple[float, float, bool] | None:
        return self._zone

    def settled(self) -> bool:
        return self.watched_for() >= _MIN_STILL_S

    def movers(self) -> int:
        """How many of this row's defenders are not standing still — by the
        head count and the standing tally, neither of which needs a track."""
        return max(0, self.occupants() - len(self.standing_spots()))

    def watched_for(self) -> float:
        """Seconds since this row was first looked at."""
        if self._first_seen is None:
            return 0.0
        return max(0.0, self._last_tick - self._first_seen)

    def seen_span(self) -> tuple[float, float] | None:
        """The leftmost and rightmost anybody has been seen in this row.

        Where a zone's boards come from when there is no standing defender
        to measure against: a patrol turns where it turns, and the walls
        marked by hand are only where it *could*.
        """
        xs = [x for _when, frame in self._sightings for x in frame]
        return (min(xs), max(xs)) if xs else None

    def board_patrol(self) -> "_BoardPatrol | None":
        return self._board

    def standing_spots(self) -> list[float]:
        """Where somebody is in most frames — found by counting, not by
        following.

        A patrol crossing a standing defender drags his track off him and
        the row stops being readable at all (row 3: three defenders, one
        followed, 2026-08-10). But the crossing cannot move where he *is*,
        and he is there in nearly every frame, so his position shows up as
        a spike in the tally whatever the tracker makes of it. A patrol
        contributes a frame here and a frame there and never a spike.

        Worked out once per tick and then handed out. It reads the whole
        sighting record — sixteen seconds of it, which at the rate frames
        now arrive is some six hundred entries — and half a dozen callers
        ask for it between one observation and the next, feed() itself
        among them. The answer cannot change in between: _sightings only
        moves when a tick is fed.
        """
        if self._spots is not None:
            return list(self._spots)
        self._spots = self._find_standing_spots()
        return list(self._spots)

    def _find_standing_spots(self) -> list[float]:
        if self.watched_for() < _MIN_STILL_S:
            return []
        tally: dict[int, int] = {}
        for _when, frame in self._sightings:
            for spot in {int(round(x / _STILL_SPAN_PX)) for x in frame}:
                tally[spot] = tally.get(spot, 0) + 1

        needed = _STANDING_SHARE * len(self._sightings)
        # Neighbouring bins belong to the same defender when he sits on a
        # boundary; take the busiest of each run.
        spots = sorted(bin_ for bin_, seen in tally.items() if seen >= needed)
        found, run = [], []
        for bin_ in spots + [None]:
            if run and (bin_ is None or bin_ != run[-1] + 1):
                best = max(run, key=lambda b: tally[b])
                found.append(self._settle_spot(best * float(_STILL_SPAN_PX)))
                run = []
            if bin_ is not None:
                run.append(bin_)
        return found

    def _settle_spot(self, about: float) -> float:
        """The bin says roughly where he stands; the detections in it say
        exactly. A bin is 14px wide and half of that is most of the error
        the crowded-row fallback has left."""
        near = [x for _when, frame in self._sightings for x in frame
                if abs(x - about) <= _STILL_SPAN_PX]
        return float(np.median(near)) if near else about

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
        single check.

        Both sides of the share are counted over the same stretch of time,
        which is what makes it a share at all. It used to compare a track's
        whole sample count against the ticks that fit in the buffer at grid
        resolution — two different clocks, and neither of them the one the
        frames actually arrived on.

        Anchoring is deliberately *not* a way past this. Standing still in
        one place is not on its own evidence of being a defender: red that
        flashes three ticks in every forty, always at the same spot, stands
        perfectly still by every measure this class has and is a goal
        animation. Being seen often is the part that tells them apart.
        """
        if self.watched_for() < _MIN_STILL_S:
            return list(self._tracks)
        needed = _MIN_TRACK_SHARE * len(self._sightings)
        since = self._last_tick - _SIGHTING_WINDOW_S
        return [track for track in self._tracks
                if track.samples_since(since) >= needed]

    def has_mover(self) -> bool:
        if self.orange:
            # No tracks are consulted in this mode, so neither is their
            # verdict on who moves — the head count and the standing tally
            # answer it without them.
            return self.movers() > 0
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
        counts = [seen for _when, seen in self._counts]
        needed = max(1, int(_MIN_TRACK_SHARE * len(counts)))
        for count in range(max(counts), 0, -1):
            if sum(seen >= count for seen in counts) >= needed:
                return count
        return 0

    def positions(self, t_future: float) -> list[float] | None:
        """Everyone in this row at `t_future`, or None when any of them
        cannot be predicted — which the caller must treat as "do not shoot"
        rather than as "the row is clear"."""
        if self.watched_for() < _MIN_STILL_S:
            # A helmet seen once is not a prediction: it may be parked or it
            # may be crossing the row at speed, and nothing here can tell
            # yet. Until the row has settled there is no answer to give.
            return None
        by_board = self._by_board(t_future)
        if by_board is not None:
            return by_board
        if self.orange and not self._rough:
            # Nothing but the zone answers here. Until it has timed
            # somebody, the row has no answer — the trackers are still
            # running, but they are not what this mode is for.
            return [] if self.occupants() == 0 else None

        tracks = self._real_tracks()
        if self._rough:
            # Out of time. Every track answers with whatever it has: its
            # period if it found one, and its last speed carried forward if
            # it did not.
            return sorted(track.rough(t_future) for track in tracks) \
                if tracks else sorted(self.standing_spots())
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

    def struggling(self) -> bool:
        """Whether the trackers have lost this row.

        Any mismatch, either way. Too few tracks is a defender nobody is
        following; too many is the same crossing seen as extra defenders
        that will then be dodged as if they were real. A crowded row does
        both by turns — three defenders read as one track, then as four
        (2026-08-10).
        """
        return len(self._real_tracks()) != self.occupants()

    def by_board_ready(self) -> bool:
        """Whether the bounce timing has anything to say, and whether it is
        wanted: a zone with at least one patrol timed in it, and a row that
        has not managed without it.

        "Has not managed" rather than "is struggling": a row can keep a
        tidy track per defender and still never pin the period down, which
        is not a mismatched head count but is just as stuck.

        In orange mode there is no "without it" — the zone is the only thing
        answering, so it answers as soon as it can.
        """
        if self.orange and not self.movers():
            return True           # everybody stands; nothing to time
        if self._board is None or not self._board.patrols():
            return False
        if self._rough:
            return True           # out of time: whoever was timed will do
        if not self.orange:
            return not self._green
        # Every mover the row appears to hold has to have been timed. A zone
        # that has caught one of two is not half right, it is a row with a
        # defender missing from it — and judged against every sighting it
        # scores as hopeless and throws away the one it did catch, over and
        # over (2026-08-11).
        return len(self._board.patrols()) >= self.movers()

    def _by_board(self, t_future: float) -> list[float] | None:
        """The whole row from the bounce timing plus the standing tally —
        every patrol the zone has timed, and everybody who never moves."""
        if not self.by_board_ready():
            return None
        if self._board is None or not self.movers():
            return sorted(self.standing_spots())
        moving = self._board.predict(t_future)
        if moving is None:
            return None
        return sorted(self.standing_spots() + moving)

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

    def slack(self) -> float:
        """How far this row's own predictions have been known to miss by.

        Not a safety margin invented for the occasion — the row's own track
        record, in pixels, measured against reality over exactly the flight
        a shot takes. A plan that leaves less room than this on a row is
        planning inside its own error bar: the shot that lost level 8 was
        given 2px of clearance on a row whose worst miss that minute was 40
        (2026-08-10).

        Zero for a row with nobody in it, and for one that has never been
        checked — there is nothing there to be wrong about, or nothing yet
        measured to be wrong by.
        """
        if self.occupants() == 0:
            return 0.0
        verdict = self.verdict()
        errors = self._error_values()
        worst = max(errors) if errors else 0.0
        return max(worst, verdict.worst if verdict else 0.0)

    def error(self) -> float | None:
        """The error this row is judged on — its best under the present fit,
        falling back to the live window while it has yet to earn one."""
        verdict = self.verdict()
        if verdict is not None:
            return verdict.mean
        return self.live_error()

    def live_error(self) -> float | None:
        errors = self._error_values()
        return float(np.mean(errors)) if errors else None

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
        if self.watched_for() < _MIN_STILL_S:
            return False
        if self.occupants() == 0:
            return True          # nothing to dodge, and nothing to latch
        if self.by_board_ready():
            # Predicted from its turns at the board instead of by following
            # anybody. It still has to prove itself against reality the same
            # way — the check below compares whatever positions() answers
            # with what actually turned up — but the head count cannot be
            # the thing that stops it, since not matching the head count is
            # the whole reason it is on this path.
            return self._proved()
        if self.orange:
            # Only the zone answers, so only the zone is judged: it has to
            # have timed somebody, and its predictions have to have come
            # true like anybody else's.
            return self._proved() if self.by_board_ready() else False
        if self.struggling():
            # The trackers do not agree with the head count, either way.
            # Too few is a defender nobody is following — which is how the
            # model announced it had converged while two rows had never been
            # modelled at all. Too many is a crossing read as extra
            # defenders, and a row certified on that is certified on a
            # picture of the row that is wrong (2026-08-10).
            return False
        if not self.has_mover():
            return self._latch()  # nothing that moves, so nothing to prove
        return self._proved()

    def _proved(self) -> bool:
        """Whether the predictions this row has been making came true, by
        the same measure however they were arrived at."""
        verdict = self.verdict()
        if verdict is None or not verdict.passes:
            return False
        # The average is the row's best; the wildest miss is always the one
        # that just happened.
        errors = self._error_values()
        if not errors or max(errors) >= _MAX_WORST_PX:
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

    def settle_for_now(self) -> bool:
        """Take whatever has been measured and call it good enough.

        For a row that has run out of time. Everything careful about this
        module is about refusing to answer without proof, and that is right
        while there is time to gather it — but a level ends whether or not a
        shot is taken, so a row still arguing with itself after a minute has
        cost the attempt just as surely as a miss would.

        So the bar comes down rather than the answer being invented: a
        period fitted but not confident enough is used, a zone that has
        timed one of two patrols is used for the one, and a track with
        neither is carried forward on the speed it was last seen at. All of
        it is worse than a proved model and none of it is a guess about
        something never observed.
        """
        if self._green:
            return False
        self._rough = True
        self._latch()
        return True

    def relearn(self):
        """Forget the latch. For a new level, where the defenders really are
        different and everything known about the old ones is a lie."""
        self._green = False
        self._rough = False
        self._best = None
        # The zone belongs to a particular arrangement of standing
        # defenders, and a new level arranges them differently.
        self._zone = None
        self._board = None
        self._board_answering = False
        self._first_seen = None
        self._errors.clear()
        self._pending.clear()
        self._tracks.clear()
        self._counts.clear()
        self._sightings.clear()
        self._spots = None
        self._y.clear()
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
        errors = self._error_values()
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
            settled=self.settled(),
            fitted=(lead is None or (lead.period is not None
                                     and lead.confidence >= _MIN_CONFIDENCE)),
            standing=self.standing(),
            mean_y=(float(np.mean([y for _when, y in self._y]))
                    if self._y else None),
            period=lead.period if lead is not None else None,
            confidence=lead.confidence if lead is not None else 0.0,
            min_x=bounds[0] if bounds else None,
            max_x=bounds[1] if bounds else None,
            speed=speed,
            error=self.error(),
            worst=max(errors) if errors else None,
            checks=(verdict.checks if verdict is not None else len(errors)),
            live_error=self.live_error(),
            loose=self.unfollowed(),
            bounces=self._board.bounces if self._board else 0,
            bounce_period=self._board.period if self._board else None,
            zone_patrols=sorted(
                ((p.period, p.speed) for p in self._board.patrols()),
                reverse=True) if self._board else [],
            slack=self.slack(),
            ready=self.ready(),
        )


class MotionModel:
    """One RowMotion per row, fed straight from a scan."""

    def __init__(self, geom, horizon_s: float, orange: bool = False):
        self._geom = geom
        self._horizon = horizon_s
        # Predict every row from its board zone and nothing else: each row
        # gets a stretch of ice at one end, every defender that turns in it
        # is timed separately, and a period plus the row's own span gives a
        # speed. No trackers, no autocorrelation, no waiting for either to
        # come good — the same method for every row whatever is standing in
        # it and however many are moving.
        self.orange = bool(orange)
        self._rows = {lane.index: RowMotion(horizon_s, geom.left, geom.width)
                      for lane in geom.lanes}
        self._lanes = {lane.index: lane for lane in geom.lanes}

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

    def choose_zones(self) -> dict[int, tuple[float, float, bool]]:
        """Give a row that needs one a stretch of ice to watch, and say which.

        Two reasons to open a zone, and either is enough.

        *The row is crowded.* Standing defenders cut it into stretches; the
        one worth watching reaches a board, because that is where the patrol
        turns round and a turn is the one thing about it that can be timed
        without following it. A middle stretch would show the patrol
        crossing and tell nothing about when.

        *The row is simply taking too long.* After _ZONE_AFTER_S of watching
        with nothing to show for it, the trackers have had their chance —
        and there is no cost to counting turns alongside them, since the
        zone only ever gets used while they are still failing. Here the
        boards are the ends of the ice anybody has actually been seen on,
        and the zone is the share of it nearest one.
        """
        chosen = {}
        for index, row in self._rows.items():
            row.orange = self.orange
            lane = self._lanes.get(index)
            if lane is None or row.zone() is not None:
                continue
            if self.orange:
                # Every row with somebody moving in it gets one, straight
                # away: this mode has nothing else to predict with, so there
                # is nothing to wait for. A row where everybody stands needs
                # no zone at all — nobody will ever reach the board, and it
                # waited for turns that could not come (2026-08-11).
                if not row.settled() or not row.movers():
                    continue
                picked = (self._past_the_standers(row, lane)
                          if row.standing_spots() else
                          self._end_of_the_ice(row))
                if picked is None:
                    continue
                zone, far = picked
                row.watch_zone(zone[0], zone[1], zone[2], far)
                chosen[index] = zone
                continue
            if row.ready():
                # Already proved, or already watching somewhere. A row
                # predicting itself to a tenth of a pixel was handed a zone
                # because one stray blob made the head count disagree, and
                # the orange box said it was in trouble when it was not
                # (2026-08-10).
                continue
            if row.occupants() == 0:
                continue                      # nothing in it to time
            crowded = row.struggling() and row.standing_spots()
            late = row.watched_for() >= _ZONE_AFTER_S
            if not crowded and not late:
                continue
            picked = (self._past_the_standers(row, lane) if crowded
                      else self._end_of_the_ice(row))
            if picked is None:
                continue
            zone, far = picked
            row.watch_zone(zone[0], zone[1], zone[2], far)
            chosen[index] = zone
        return chosen

    def _past_the_standers(self, row, lane):
        """The wider of the two stretches the standing defenders leave —
        more ice is more frames of the patrol on its own."""
        spots = row.standing_spots()
        low, high = self._geom.centre_bounds(lane)
        half = self._geom.half_width(lane)
        left_gap = min(spots) - half - low
        right_gap = high - (max(spots) + half)
        if max(left_gap, right_gap) < _MIN_ZONE_PX:
            return None                       # no end of the row is free
        if right_gap >= left_gap:
            return (max(spots) + half, high + _ZONE_OPEN_PX, True), low
        return (low - _ZONE_OPEN_PX, min(spots) - half, False), high

    @staticmethod
    def _end_of_the_ice(row):
        """A share of the patrol's own span, at the end of it.

        Not the whole row: the defender has to leave the zone and come back
        for a visit to end, and a zone he never leaves is one no turn is
        ever read in. A third is enough ice to be watched crossing it and
        little enough to be out of it for most of the sweep.
        """
        span = row.seen_span()
        if span is None:
            return None
        low, high = span
        if high - low < _MIN_ZONE_PX * 2:
            return None                       # nobody is patrolling far
        width = (high - low) * _ZONE_SHARE
        return (high - width, high + _ZONE_OPEN_PX, True), low

    def zones(self) -> dict[int, tuple[float, float, bool]]:
        return {index: row.zone() for index, row in self._rows.items()
                if row.zone() is not None}

    def census(self) -> list[tuple[int, int, int]]:
        """(row index, standing, moving) for every row."""
        return [(index, *row.census())
                for index, row in sorted(self._rows.items())]

    def relearn(self):
        for row in self._rows.values():
            row.relearn()

    def settle_for_now(self) -> list[int]:
        """Bring the bar down on every row still arguing with itself, and
        say which ones. For a level that has run out of time."""
        return [index for index, row in sorted(self._rows.items())
                if row.settle_for_now()]

    def slack(self, index: int) -> float:
        row = self._rows.get(index)
        return row.slack() if row is not None else 0.0

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
