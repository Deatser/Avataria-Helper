# modules/hockey/trajectory.py
"""Following the puck after a shot, and turning that into per-row timings.

The planner's real question is not "how long does a shot take" but "when
does it reach *this* row, and where across the rink is it then" — the top
row and the bottom row are crossed seconds apart, and each has to be dodged
at its own moment. One number for the whole flight cannot answer that.

So the flight is measured rather than modelled. Nothing here assumes the
puck travels in a straight line, at constant speed, or along any particular
curve: it is followed frame by frame from the moment the mouse is released,
and the crossings fall out of the track by interpolation.

Finding it is deliberately *not* done frame by frame. The first version
picked the best-looking moving blob each frame and kept it, and it went
wrong in exactly the way that approach always does: the shooter's own
avatar animates the moment the button comes up, right where the puck
starts, and once a wrong blob is adopted, continuity defends it for the
rest of the flight (live, 2026-08-09 — the marks landed nowhere near the
puck).

Instead every plausible blob of every frame is collected first, and the
puck is found afterwards as the best *path* through them: a chain that
rises steadily at a near-constant velocity. A defender crossing the rink
makes blobs in every frame but never a rising chain; an animation flickers
for a few frames and cannot sustain one. A single bad frame costs nothing,
because the chain is allowed to skip it.

The frames themselves are kept (JPEG-encoded, a few megabytes for a whole
flight) so the per-row pictures can be cut *after* the path is known. Taking
them live meant photographing whatever the tracker believed at the time,
which is worthless precisely when the tracker is wrong.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import median

import cv2
import numpy as np

# Where measured flights accumulate, next to config.json and for the same
# reason — relative to the working directory, so a test writes into its own
# tmp directory rather than the repo.
TRAJECTORY_FILE = Path("hockey_trajectories.json")

# A pixel has to change by this much between frames to count as movement.
# Low rather than safe: the puck is small and only middling in contrast
# against ice, and it was being seen in barely a third of frames at 25
# (2026-08-09). Extra noise costs nothing here — a blob that is not part of
# a steadily rising chain is never chosen, however many of them there are.
_DIFF_THRESHOLD = 18

# What a puck's own difference blob may be. A moving defender's is
# body-sized and lands far above the ceiling; a disc is roughly as wide as
# it is tall, which a sliding sprite's smear is not.
_PUCK_AREA_MIN    = 40
_PUCK_AREA_MAX    = 900
_MAX_PUCK_ASPECT  = 2.2
_MAX_BLOBS_PER_FRAME = 16

# The game keeps its aim guide on screen — a column of cyan dashes running
# from the stick to the goal, animating as it goes. Each dash is about
# 420px and roughly puck-shaped, there are a dozen and a half of them, and
# they were filling the candidate budget and squeezing the real puck out of
# it (2026-08-09: 14 tracked points out of some 43 frames). Colour throws
# them out outright: the ice is pale but not blue, and the puck is dark.
_GUIDE_MIN_BLUE  = 150
_GUIDE_MIN_GREEN = 150
_GUIDE_MAX_RED   = 140
_GUIDE_MIN_SHARE = 0.7

# How long the whole flight is watched. Collected blindly and resolved
# afterwards, so this only has to be longer than a shot takes.
_FLIGHT_WINDOW_S = 2.5

# Where a chain may begin. Generous on purpose: it used to have to start
# within 120px of the stick in the first eight frames, and that is exactly
# where the shooter's swing animation swallows the puck into one big blob
# that the size filter throws out — no seed, no chain, no measurement at all
# even though the puck was visible in 43 frames out of 51 (2026-08-10).
#
# Nothing is lost by relaxing it, because a chain is judged on what it does
# rather than where it starts: it has to rise steadily, at a consistent
# velocity, for at least _MIN_CHAIN steps, and the caller then throws away
# anything that did not reach the goal. A defender crossing the rink or an
# animation flickering in place cannot satisfy that from any starting point.
_SEED_SHARE = 0.6      # of the watched frames

# Building the chain. It may skip frames — the puck passes behind the
# defenders it is being timed against, and a difference image loses it for
# as long as that lasts — must keep rising, and each step has to land near
# where the previous velocity said it would.
#
# In seconds and pixels per second throughout, never in frames. Half a
# second of not seeing it sounds generous until you look at where the puck
# goes: a left-post shot swings out to x=1363, straight through where the
# lower rows' defenders stand, and vanishes behind them. Two left shots out
# of three died there while the puck was plainly visible again further up
# (2026-08-10) — and the ten frames that allowance used to be spelt as would
# have been a quarter of a second again at the rate the capture now runs.
#
# The allowance for a skipped frame grows slowly and then stops: a puck half
# a second on is not ten times less predictable, it is very close to where
# its own speed says, and an allowance that kept growing would let a long
# jump reach halfway across the rink.
_MAX_GAP_S           = 0.6
_MIN_RISE_PX_S       = 50.0
_FIRST_STEP_DRIFT_PX_S = 1000.0  # before there is a velocity to predict from
_VELOCITY_TOLERANCE  = 26.0      # px from where its own speed said: no rate in it
_GAP_TOLERANCE_PX_S  = 130.0     # added for every second of gap past an ordinary one
_MAX_TOLERANCE_PX    = 70.0      # however long the gap

# The widest pair of samples a crossing may be interpolated between. Longer
# than this and the answer is invented rather than measured — see _cross.
_MAX_INTERP_GAP_S = 0.15

# Shorter than this is not a flight, it is a coincidence. Both halves: six
# blobs is the least that can describe a path at all, and a third of a
# second is the least that can be one — at 40 frames a second six of them
# are a seventh of a second, which any flicker manages.
_MIN_CHAIN   = 6
_MIN_CHAIN_S = 0.35

# The chain has to reach the goal line itself, with no slack. Slack was
# tried and made the two halves of this module disagree: a flight ending
# 40px short passed as "arrived" while every row whose defender reaches
# above the goal needs a crossing *at* that line, so the shot was accepted
# and then measured nothing (2026-08-09 — "попал в ворота, но не прошёл
# ряды 1-4").

# And how near the stick a track has to begin before "the puck started
# inside this row's outline" is a statement about geometry rather than
# about the tracker having woken up late.
_NEAR_START_PX = 60

# JPEG quality for the kept frames — enough to read a picture off, small
# enough that a whole flight is a few megabytes rather than a hundred. Also
# kept low because encoding happens inside the capture loop, and every
# millisecond there is a coarser timing grid for every crossing.
_KEEP_QUALITY = 70


@dataclass
class PuckSample:
    t: float      # seconds since the mouse was released
    x: float      # screen coordinates, like everything else here
    y: float


@dataclass
class RowCrossing:
    """When the puck was level with one row's defenders, and where.

    An interval rather than an instant: a defender is as tall as its own
    outline, so the puck is at risk from the moment it reaches the bottom of
    that outline until it leaves the top.
    """
    row: int
    t_enter: float
    t_exit: float
    x_enter: float
    x_exit: float

    @property
    def t_middle(self) -> float:
        return (self.t_enter + self.t_exit) / 2


@dataclass
class ShotTrack:
    aim: float
    at: str = ""
    samples: list[PuckSample] = field(default_factory=list)

    @property
    def travel_s(self) -> float | None:
        return self.samples[-1].t if self.samples else None


def _is_guide(patch: np.ndarray) -> bool:
    """Whether a blob is a piece of the game's own aim guide rather than the
    puck. The guide is cyan, the ice is pale but not blue, and the puck is
    dark.

    By share of pixels rather than by average, because the puck spends much
    of its flight directly over the guide: a difference blob then holds the
    dark puck *and* the cyan dash it just uncovered, and an average of the
    two lands in between and could go either way. Nearly all of it has to be
    guide before the blob is thrown out.
    """
    if patch.size == 0:
        return False
    blue, green, red = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
    cyan = ((blue > _GUIDE_MIN_BLUE) & (green > _GUIDE_MIN_GREEN)
            & (red < _GUIDE_MAX_RED))
    return float(cyan.mean()) > _GUIDE_MIN_SHARE


@dataclass(frozen=True)
class _Blob:
    t: float
    x: float
    y: float


class PuckFlight:
    """One flight. `feed` every frame until it returns False, then
    `resolve()` for the path and `frame_at()` for the pictures."""

    def __init__(self, geom, aim: float, start: tuple[int, int]):
        self._geom  = geom
        self._aim   = aim
        self._start = (float(start[0]), float(start[1]))
        self._prev: np.ndarray | None = None
        self._times: list[float] = []
        self._kept: list[bytes] = []
        self._blobs: list[list[_Blob]] = []

    # ── Collecting ───────────────────────────────────────────────────────

    def feed(self, frame: np.ndarray, t: float) -> bool:
        """One frame, `t` seconds after release. False once the watching
        window is over — nothing is decided here, only gathered."""
        if t > _FLIGHT_WINDOW_S:
            return False
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        previous, self._prev = self._prev, gray

        self._times.append(t)
        self._blobs.append([] if previous is None or previous.shape != gray.shape
                           else self._candidates(previous, gray, frame, t))
        ok, buffer = cv2.imencode(".jpg", frame,
                                  [cv2.IMWRITE_JPEG_QUALITY, _KEEP_QUALITY])
        self._kept.append(buffer.tobytes() if ok else b"")
        return True

    def _candidates(self, previous, gray, colour, t: float) -> list[_Blob]:
        diff = cv2.absdiff(gray, previous)
        _threshold, mask = cv2.threshold(diff, _DIFF_THRESHOLD, 255,
                                         cv2.THRESH_BINARY)
        count, _labels, stats, centroids = cv2.connectedComponentsWithStats(
            mask, 8)

        found = []
        for i in range(1, count):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if not _PUCK_AREA_MIN <= area <= _PUCK_AREA_MAX:
                continue
            left   = int(stats[i, cv2.CC_STAT_LEFT])
            top    = int(stats[i, cv2.CC_STAT_TOP])
            width  = int(stats[i, cv2.CC_STAT_WIDTH])
            height = int(stats[i, cv2.CC_STAT_HEIGHT])
            if min(width, height) <= 0:
                continue
            if max(width, height) / min(width, height) > _MAX_PUCK_ASPECT:
                continue      # a smear, not a disc
            if _is_guide(colour[top:top + height, left:left + width]):
                continue

            # A rising puck's difference blob covers where it was *and*
            # where it is, so its centroid trails the puck by half a frame's
            # travel. The blob's leading edge is the new position, and its
            # narrow side is the disc's own diameter, whatever the motion.
            diameter = min(width, height)
            found.append((area, _Blob(
                t=t,
                x=self._geom.to_screen_x(centroids[i][0]),
                y=self._geom.to_screen_y(top + diameter / 2))))
        found.sort(key=lambda pair: pair[0], reverse=True)
        return [blob for _area, blob in found[:_MAX_BLOBS_PER_FRAME]]

    # ── Resolving ────────────────────────────────────────────────────────

    def resolve(self) -> ShotTrack:
        """The best rising chain through everything collected. Empty when
        nothing sustained one, which is an honest "not measured" rather than
        a track through whatever moved."""
        best: list[tuple[int, _Blob]] = []
        best_rank = None
        seed_limit = max(1, int(len(self._blobs) * _SEED_SHARE))
        for index in range(min(seed_limit, len(self._blobs))):
            for blob in self._blobs[index]:
                chain, error = self._extend(index, blob)
                if (len(chain) < _MIN_CHAIN
                        or chain[-1][1].t - chain[0][1].t < _MIN_CHAIN_S
                        or not self._rises_enough(chain)):
                    continue
                # Longest wins; then the steadiest; then the one that began
                # lowest, which is the one that began nearest the stick.
                rank = (len(chain), -error, blob.y)
                if best_rank is None or rank > best_rank:
                    best, best_rank = chain, rank

        track = ShotTrack(aim=self._aim,
                          at=datetime.now().isoformat(" ", "seconds"))
        for _index, blob in best:
            track.samples.append(PuckSample(t=blob.t, x=blob.x, y=blob.y))
            if blob.y <= self._geom.goal_y:
                break      # arrived; nothing past the line matters
        return track

    def _extend(self, index: int, blob: _Blob) -> tuple[list, float]:
        """Follow one seed forward, always taking the blob nearest where the
        current velocity says the next one should be.

        The velocity is px per second, off the blobs' own timestamps, rather
        than px per frame off their positions in the list. Frames do not
        arrive on a fixed beat — the loop is paced by the game's redraws —
        so a step counted in frames is a different amount of travel every
        time, and every allowance measured against it moves with it.
        """
        chain = [(index, blob)]
        velocity = None
        error = 0.0
        while True:
            last_index, last = chain[-1]
            step = self._next(last_index, last, velocity)
            if step is None:
                break
            next_index, next_blob, deviation = step
            gap = next_blob.t - last.t
            if gap > 0:
                velocity = ((next_blob.x - last.x) / gap,
                            (next_blob.y - last.y) / gap)
            error += deviation
            chain.append((next_index, next_blob))
        return chain, error / max(1, len(chain) - 1)

    def _next(self, index: int, last: _Blob, velocity):
        ordinary = self._step()
        for candidate_index in range(index + 1, len(self._blobs)):
            gap = self._times[candidate_index] - last.t
            if gap > _MAX_GAP_S:
                break
            best, best_deviation = None, None
            for blob in self._blobs[candidate_index]:
                if blob.y > last.y - _MIN_RISE_PX_S * gap:
                    continue      # not heading for the goal
                if velocity is None:
                    deviation = abs(blob.x - last.x)
                    if deviation > _FIRST_STEP_DRIFT_PX_S * gap:
                        continue
                else:
                    expect = (last.x + velocity[0] * gap,
                              last.y + velocity[1] * gap)
                    deviation = ((blob.x - expect[0]) ** 2
                                 + (blob.y - expect[1]) ** 2) ** 0.5
                    allowance = min(
                        _VELOCITY_TOLERANCE
                        + _GAP_TOLERANCE_PX_S * max(0.0, gap - ordinary),
                        _MAX_TOLERANCE_PX)
                    if deviation > allowance:
                        continue
                if best_deviation is None or deviation < best_deviation:
                    best, best_deviation = blob, deviation
            if best is not None:
                return candidate_index, best, best_deviation
        return None

    def _step(self) -> float:
        """Mean seconds between frames — what "a gap longer than an ordinary
        one" is measured against, and the resolution every crossing time is
        ultimately read off."""
        if len(self._times) < 2:
            return 0.0
        return (self._times[-1] - self._times[0]) / (len(self._times) - 1)

    @staticmethod
    def _rises_enough(chain) -> bool:
        (_first_index, first), (_last_index, last) = chain[0], chain[-1]
        elapsed = last.t - first.t
        return elapsed > 0 and (first.y - last.y) / elapsed >= _MIN_RISE_PX_S

    # ── The kept frames ──────────────────────────────────────────────────

    def summary(self) -> tuple[int, int, float | None, float]:
        """(frames captured, frames with a candidate, highest y any candidate
        reached, mean milliseconds between frames).

        The first three tell a broken track's two causes apart: candidates
        reaching the goal while the chain stopped short means the chain is at
        fault; candidates stopping in the same place means the puck was
        simply not seen any higher, and no chain-building can help.

        The fourth is the resolution everything else is limited by — every
        crossing time is interpolated across one of these gaps.
        """
        seen = sum(1 for frame in self._blobs if frame)
        highest = min((blob.y for frame in self._blobs for blob in frame),
                      default=None)
        return len(self._times), seen, highest, self._step() * 1000

    def candidates(self) -> list[tuple[float, float]]:
        """Every blob that was ever considered, as (x, y).

        Drawn alongside the chosen path so a failure says which failure it
        was: no candidates in the upper half means the puck was never seen
        there — occluded, or too faint — while candidates the chain declined
        to follow means the chain is at fault.
        """
        return [(blob.x, blob.y) for frame in self._blobs for blob in frame]

    def frame_at(self, t: float) -> np.ndarray | None:
        """The frame nearest `t`, decoded. What the per-row pictures are cut
        from, once the path is known."""
        if not self._times:
            return None
        index = min(range(len(self._times)),
                    key=lambda i: abs(self._times[i] - t))
        data = self._kept[index]
        if not data:
            return None
        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


@dataclass
class RowTiming:
    """One row's crossing, averaged over every flight measured at one aim."""
    row: int
    shots: int
    t_enter: float
    t_exit: float
    x_enter: float
    x_exit: float
    spread: float      # widest disagreement between shots, seconds
    mirrored: bool = False   # taken from the opposite aim, not measured


def averaged(geom, tracks: list[ShotTrack], aim: float,
             tolerance: float = 0.02) -> list[RowTiming]:
    """What every flight at this aim agrees the timings are.

    Averaging is not a nicety here: a crossing time is interpolated across
    one frame's gap, so a single flight is only ever as sharp as the capture
    rate. Repeats are independent of it, and three of them measured the
    whole flight to within 10ms of each other (2026-08-10) while individual
    rows scattered by up to 90ms. `spread` is kept alongside so a row that
    does *not* agree with itself says so rather than hiding inside a mean.
    """
    matching = [track for track in tracks
                if abs(track.aim - aim) <= tolerance]
    gathered: dict[int, list[RowCrossing]] = {}
    for track in matching:
        for crossing in crossings(geom, track):
            gathered.setdefault(crossing.row, []).append(crossing)

    timings = []
    for row, items in sorted(gathered.items()):
        enters = [item.t_enter for item in items]
        # Median, not mean: one flight that went wrong drags a mean away
        # from four that agree, and a table is written to once and read from
        # for the rest of the session. `spread` still reports the outlier —
        # it is hidden from the number, not from the log.
        timings.append(RowTiming(
            row=row, shots=len(items),
            t_enter=median(enters),
            t_exit=median([item.t_exit for item in items]),
            x_enter=median([item.x_enter for item in items]),
            x_exit=median([item.x_exit for item in items]),
            spread=max(enters) - min(enters)))
    return timings


def mirror_fill(geom, timings: dict) -> list[tuple[float, int]]:
    """Fill a row missing from one aim with the opposite aim's, mirrored.

    Not a guess. The two side shots were measured independently and, folded
    about the shooter, agreed to one or two pixels on every row that both
    reached — 1196/1364 against 1363, 1218/1342 against 1341, 1325/1235
    against 1234 (2026-08-09). Their crossing times matched just as closely.

    It is needed because a row goes unmeasured for a reason that has nothing
    to do with the shot: the puck passes behind a defender from another row
    and the frame difference loses it there. Waiting for a flight where that
    never happens can take many attempts, and the alternative — planning a
    shot with a row missing — is the one thing this module exists to
    prevent.

    Mutates `timings` and returns the (aim, row) pairs it filled, so the
    caller can say which numbers were inferred rather than seen.
    """
    filled = []
    for aim, rows in timings.items():
        opposite = timings.get(-aim if aim else None)
        if not opposite or aim == 0:
            continue
        have = {timing.row for timing in rows}
        for timing in opposite:
            if timing.row in have:
                continue
            rows.append(RowTiming(
                row=timing.row, shots=timing.shots,
                t_enter=timing.t_enter, t_exit=timing.t_exit,
                x_enter=2 * geom.shooter_x - timing.x_enter,
                x_exit=2 * geom.shooter_x - timing.x_exit,
                spread=timing.spread, mirrored=True))
            filled.append((aim, timing.row))
        rows.sort(key=lambda item: item.row)
    return filled


# The pull the two side shots were measured at, and how finely the pulls
# between them are stepped when the fine aim is switched on. 0.05 is 40px of
# goal mouth — finer than that is below what the release timing can hold
# anyway, and every extra aim is another sweep of the whole search.
_FULL_AIM  = 0.5
_AIM_STEP  = 0.05


def blend(straight: list[RowTiming], full: list[RowTiming],
          aim: float) -> list[RowTiming]:
    """One row table for a pull somewhere between nothing and the full one.

    The arc's shape is measured, not modelled: a straight shot holds x to
    within half a pixel over the whole flight, and the two full pulls
    deviate from it by mirror-image amounts at every moment — 7px of
    disagreement against an 84px swing, correlated 0.978 (2026-08-10). One
    shape, then, signed by which way the pull went and scaled by how hard.

    Scaled *linearly*, because with measurements at nothing and at full
    there is nothing in the data to justify any other curve through them.
    That is an assumption, and the honest place for it: everything else here
    is measured, and this is behind a switch.
    """
    share = abs(aim) / _FULL_AIM
    by_row = {timing.row: timing for timing in full}
    blended = []
    for base in straight:
        other = by_row.get(base.row)
        if other is None:
            continue          # a row one of the two never reached
        blended.append(RowTiming(
            row=base.row,
            shots=min(base.shots, other.shots),
            t_enter=base.t_enter + share * (other.t_enter - base.t_enter),
            t_exit=base.t_exit + share * (other.t_exit - base.t_exit),
            x_enter=base.x_enter + share * (other.x_enter - base.x_enter),
            x_exit=base.x_exit + share * (other.x_exit - base.x_exit),
            spread=max(base.spread, other.spread),
            mirrored=base.mirrored or other.mirrored))
    return blended


def spread_aims(timings: dict, step: float = _AIM_STEP) -> dict:
    """The three measured tables, plus one for every pull between them.

    A shot has a whole range of pulls available and only three of them have
    ever been fired; the ones in between are where most of the windows are.
    Returns the measured tables untouched — an aim that was really measured
    is never replaced by an interpolation of itself.
    """
    straight = timings.get(0.0)
    if not straight:
        return dict(timings)

    dense = dict(timings)
    for side in (-_FULL_AIM, _FULL_AIM):
        full = timings.get(side)
        if not full:
            continue
        steps = int(round(_FULL_AIM / step))
        for i in range(1, steps):
            aim = round(side * i / steps, 3)
            if aim in dense:
                continue
            rows = blend(straight, full, aim)
            if rows:
                dense[aim] = rows
    return dense


def arrived(geom, track: ShotTrack) -> bool:
    """Whether the puck was followed all the way to the goal.

    A chain that died halfway is not a shorter flight, it is a lost one, and
    storing it next to good measurements is how the table quietly fills with
    numbers that disagree for no reason.
    """
    return bool(track.samples) and track.samples[-1].y <= geom.goal_y


def puck_at(track: ShotTrack, t: float) -> tuple[float, float] | None:
    """Where the puck was at `t`, interpolated between the samples either
    side — so a picture and the number printed on it agree."""
    samples = track.samples
    if len(samples) < 2 or not samples[0].t <= t <= samples[-1].t:
        return None
    for before, after in zip(samples, samples[1:]):
        if before.t <= t <= after.t and after.t > before.t:
            share = (t - before.t) / (after.t - before.t)
            return (before.x + share * (after.x - before.x),
                    before.y + share * (after.y - before.y))
    return None


def crossings(geom, track: ShotTrack) -> list[RowCrossing]:
    """When the puck was level with each row, worked out from the track.

    Measured against the strip of ice the defender stands on — see
    Geometry.block_band. Not the row's own line, which is where a helmet
    sits, and not the whole drawn outline either, which is a standing person
    and reaches far further up the screen than the ice it occupies.

    Both ends of the interval are clipped to the flight itself. A defender
    whose head reaches above the goal line is never *left* — the puck has
    scored by then — and demanding a real exit threw away four rows out of
    five (live, 2026-08-09). The same at the near end: a band reaching below
    the stick is one the puck starts inside.
    """
    samples = track.samples
    if len(samples) < 2:
        return []

    found = []
    for lane in geom.lanes:
        # The ice the defender stands on, not the height it is drawn at —
        # see Geometry.block_band.
        band_top, band_bottom = geom.block_band(lane)
        # Risk in this row runs until the puck leaves that strip or scores,
        # whichever comes first.
        exit_line = max(band_top, geom.goal_y)
        if band_bottom <= exit_line:
            continue          # the whole defender stands behind the goal

        enter = _cross(samples, band_bottom)
        if (enter is None and samples[0].y <= band_bottom
                and samples[0].y >= geom.shooter_y - _NEAR_START_PX):
            # "The puck began inside this row" is only a statement about
            # geometry when the track really did begin at the stick.
            enter = (samples[0].t, samples[0].x)   # started inside it
        exit_ = _cross(samples, exit_line)
        if enter is None or exit_ is None:
            continue
        found.append(RowCrossing(row=lane.index,
                                 t_enter=enter[0], x_enter=enter[1],
                                 t_exit=exit_[0], x_exit=exit_[1]))
    return found


def _cross(samples: list[PuckSample], line_y: float):
    """(time, x) at which the track passed `line_y` going up, or None when
    it never did — or did, but between two samples too far apart to say
    when.

    That last case is a real one and it has to be refused rather than
    guessed at. The chain may bridge half a second of the puck being hidden,
    which keeps the flight alive; interpolating a crossing across that same
    bridge invents a time to a tenth of a second out of nothing. A track
    that did it reported a row at 0.35s that four other shots put at 0.65
    (2026-08-10).
    """
    for before, after in zip(samples, samples[1:]):
        if before.y >= line_y >= after.y and before.y != after.y:
            if after.t - before.t > _MAX_INTERP_GAP_S:
                return None
            share = (before.y - line_y) / (before.y - after.y)
            return (before.t + share * (after.t - before.t),
                    before.x + share * (after.x - before.x))
    return None


# ── Storage ──────────────────────────────────────────────────────────────

def load(path: Path | None = None) -> list[ShotTrack]:
    """Every flight measured so far. A file that has been hand-edited into
    nonsense costs the history, not the run."""
    target = Path(path) if path is not None else TRAJECTORY_FILE
    if not target.exists():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        return [ShotTrack(aim=float(shot["aim"]), at=str(shot.get("at", "")),
                          samples=[PuckSample(*point)
                                   for point in shot["samples"]])
                for shot in raw.get("shots", [])]
    except Exception:
        return []


def append(track: ShotTrack, path: Path | None = None) -> int:
    """Add one flight to the file; returns how many are now stored. A flight
    the tracker lost is not written — a short track would sit in the table
    looking like a measurement."""
    target = Path(path) if path is not None else TRAJECTORY_FILE
    shots = load(target)
    shots.append(track)
    target.write_text(json.dumps(
        {"shots": [{"aim": shot.aim, "at": shot.at,
                    "samples": [[round(s.t, 4), round(s.x, 1), round(s.y, 1)]
                                for s in shot.samples]}
                   for shot in shots]},
        indent=2, ensure_ascii=False), encoding="utf-8")
    return len(shots)
