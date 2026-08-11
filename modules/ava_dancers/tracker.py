# modules/ava_dancers/tracker.py
"""Follow every note down its lane and press on a *predicted* arrival.

Why this replaced the 30px strip at the hit line
------------------------------------------------
The old detector watched one 30-pixel strip per lane and pressed when the
reading there changed from "nothing" to "an arrow". Two things about the
game make that fail, and no amount of retuning the strip fixes either:

1. Landing a note lights the whole bottom of its lane up in a solid cyan
   wash for ~230ms (measured), and that wash reads as a perfectly good
   cyan arrow. The strip sits inside it. Between the press, the wash and
   its fade the lane reads "arrow" continuously for ~490ms after every
   single note.
2. The gap between two notes in one lane shrinks as the round goes on —
   1400ms at the start, 375ms by 2:49 (5th percentile, measured over a
   real round). Once that gap drops under the ~490ms blind window the
   second note never produces a *change*, so it is never pressed. Nothing
   is logged; it is simply gone. That is the miss that gets worse at every
   speed wave.

Watching the lane *above* the flash instead solves both at once, and turns
the press into a scheduled event rather than a reaction:

* Notes are found 640px up the lane where nothing else is ever drawn, and
  each one is followed as an object with an identity, so two notes are two
  tracks no matter how close together they land.
* A track's speed is measured over ~15-20 frames, and the press is
  scheduled for the moment the note will *reach* the hit line — accurate
  to a millisecond through the delayed-call scheduler, instead of landing
  wherever the ~42ms capture loop happened to tick.
* Being blinded no longer matters. A track coasts on its own fitted line,
  so the note does not have to be visible at the moment it is pressed.

Geometry
--------
Measured 2026-08-11 off testlogs/calib_20260811_015809 (a recorded round,
see calib_record.py) and confirmed by hand-marking all four lanes over the
live game. The playfield's inner area is x 924..1635, y 186..1236; that is
712 wide, so a lane is exactly 178. The hit flash saturates the lane from
about y 835 down, and notes travel at 363 px/s at the start of a round,
stepping up to 544 and then 664 as the speed waves arrive — per-track
speed is measured, never assumed.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from modules.ava_dancers.tiles import (
    EMPTY, PRESSABLE, Thresholds, decide_kind,
    _IDLE_V, _LIT_V, _NEON_S, _RED_S, _MAGENTA_S,
    _RED_LOW, _RED_HIGH, _GREEN_LOW, _GREEN_HIGH,
    _CYAN_LOW, _CYAN_HIGH, _MAGENTA_LOW, _MAGENTA_HIGH,
)

# ── Playfield ────────────────────────────────────────────────────────────
LANES = 4

# Everything vertical is a fraction of the playfield's own height, so the
# same numbers hold whatever size the game window is. Measured against the
# reference layout below (inner field 924..1635 x 186..1236):
#
#   zone top     y  190 — just under the field's own top border
#   zone bottom  y  830 — the hit flash saturates the lane from ~835 down
#   commit       y  780 — speed is settled and the blob is not yet clipped
#   hit line     y 1085 — where a note's bottom edge is when the key lands
_ZONE_TOP_F   = 0.004
_ZONE_BOT_F   = 0.613
_COMMIT_F     = 0.565
_HIT_F        = 0.855

# Landing a note washes the bottom of its lane solid cyan for ~230ms. That is
# what makes the lane unreadable down there — and also the only honest answer
# to "did that press actually score". The capture reaches down past it so the
# tracker can check its own work; nothing below _FLASH_TOP_F is ever tracked.
_FLASH_TOP_F  = 0.618   # y 835 — where the wash starts
_CAPTURE_BOT_F = 0.99   # y 1226 — well inside the flash, short of the border


@dataclass(frozen=True)
class Geometry:
    """The playfield's inner rectangle, in screen coordinates, and every
    line derived from it."""
    left:   int
    top:    int
    width:  int
    height: int

    @property
    def lane_w(self) -> int:
        return self.width // LANES

    @property
    def zone_top(self) -> int:
        return self.top + round(self.height * _ZONE_TOP_F)

    @property
    def zone_height(self) -> int:
        return round(self.height * (_ZONE_BOT_F - _ZONE_TOP_F))

    @property
    def commit_y(self) -> int:
        return self.top + round(self.height * _COMMIT_F)

    @property
    def hit_y(self) -> int:
        """Where a note's bottom edge is at the moment the key should be down.

        The old detector fired when that edge entered its strip at y=1070 and
        could be up to one poll (~28px at speed) late, so the presses that
        scored ИДЕАЛЬНО spanned roughly y 1070..1112 — this aims at the
        middle of a range already proven to work.
        """
        return self.top + round(self.height * _HIT_F)

    @property
    def flash_row(self) -> int:
        """First row of the capture that belongs to the hit flash."""
        return round(self.height * (_FLASH_TOP_F - _ZONE_TOP_F))

    @property
    def region(self) -> dict:
        """What to capture every poll.

        Taller than the tracked zone: the rows past flash_row carry the hit
        flash, which is not tracked but is read to confirm that a press
        actually landed. The capture costs nothing extra — grab_window
        renders the whole game window either way and this only crops more of
        what was already drawn.
        """
        return {"left": self.left, "top": self.zone_top,
                "width": self.lane_w * LANES,
                "height": round(self.height * (_CAPTURE_BOT_F - _ZONE_TOP_F))}


# Hand-verified 2026-08-11: measured off the playfield's own cyan border in
# a recorded round, then confirmed by marking all four lanes by hand over
# the live game (922/1104/1279/1462 by hand vs 924/1102/1280/1458 measured).
DEFAULT_GEOMETRY = Geometry(left=924, top=186, width=712, height=1051)

# ── Reading a lane ───────────────────────────────────────────────────────
ROW_ON     = 6    # lit pixels in a row before the row is part of a note
CLOSE_K    = 7    # ← and → glyphs are stacks of horizontal bars, so their
                  # row profile is a comb with gaps up to 3 rows (measured).
                  # Closing over that turns one note into one block, while
                  # staying far below the 140+ row gap between two notes.
MIN_BLOB_H = 30   # shorter than this is glow or noise, not a note
BLIND_FRAC = 0.60 # if this much of the zone is lit at once it is not notes;
                  # skip the lane this frame and let its tracks coast

# ── Tracking ─────────────────────────────────────────────────────────────
MATCH_TOL_PX   = 70     # slack around a track's predicted position…
MATCH_TOL_REL  = 0.35   # …plus this share of the distance it should have moved
MAX_MISSES     = 3
MIN_SAMPLES    = 3      # before a track's own measured speed is trusted
FIT_WINDOW     = 24     # samples the line is fitted over — at ~24Hz that
                        # is a second, longer than a note is visible for,
                        # so in practice the fit uses the whole descent.
                        # Was 8: shortening it only threw away readings
                        # that were already being taken, and the noise on
                        # the fit falls off as 1/sqrt(n)
VOTE_MIN_H     = 60     # only vote on what a note looks like once enough of
                        # it is inside the zone to judge
DEFAULT_SPEED  = 550.0  # px/s — only used before a lane has ever measured one
SPEED_MIN      = 150.0
SPEED_MAX      = 2000.0

# ── What is allowed to become a press ────────────────────────────────────
# Everything below exists to answer one question: is this bright thing
# actually a note falling down the lane? A note always enters at the top,
# is seen for most of the zone, keeps a constant size and covers hundreds
# of pixels on the way down. Glow, flash edges, the tail of a note already
# gone and anything else that merely happens to be lit fails at least one
# of these — and a press with nothing under it costs combo, so the bar is
# on the side of refusing.
BORN_ABOVE_F   = 0.45   # a new track may only start in the top of the zone
MIN_TRAVEL_PX  = 150    # …and must be seen to cover this much, measured from
                        # where the track was BORN. Measuring it across
                        # `samples` instead was the bug that made the first
                        # live run press nothing at all: that deque holds the
                        # last FIT_WINDOW frames only, which at 24Hz and the
                        # 363 px/s of a round's opening covers ~106px — under
                        # this floor, so every single note was turned down.
MIN_NOTE_H     = 50     # a note measures 116-122px tall; well outside that
MAX_NOTE_H     = 260    # is not a note
MAX_LEAD_S     = 1.5    # never schedule further out than this; a longer
                        # prediction means the speed estimate is wrong

# Two real notes in one lane are never closer than this (measured 5th
# percentile 375ms, absolute minimum 181ms over a full round), so anything
# landing inside this window of an already-scheduled press is the same note
# reaching the same place twice — a track that was lost and re-acquired —
# and must not be pressed again. A wrong press costs combo; a skipped
# duplicate costs nothing.
MIN_PRESS_GAP_S = 0.12

# ── Checking the bot's own work ──────────────────────────────────────────
# A press that scores lights the lane up; one that misses does not. Measured
# over a recorded round: the flash follows the press by 131-185ms (median
# 145) and holds for ~230ms. Anything inside this window after a scheduled
# arrival counts as that press landing, so the detector can report how many
# of its notes actually scored instead of leaving it to be guessed at.
FLASH_LIT_FRAC   = 0.92   # share of a row's width lit before it is a wash
FLASH_MIN_ROWS   = 15     # …over this many sampled rows before the lane is lit
FLASH_STEP_Y     = 4      # the wash is a solid block hundreds of pixels tall,
FLASH_STEP_X     = 2      # so it is read off a fraction of the pixels
VERIFY_FROM_S    = 0.06
VERIFY_UNTIL_S   = 0.40

PRESS_LEAD_S = 0.0      # fire this long before the predicted arrival

_CLOSE_KERNEL = np.ones((CLOSE_K, 1), np.uint8)

# Row-profile planes, in the order lane_profiles() stacks them.
LIT, CYAN, GREEN, RED, MAGENTA = range(5)


@dataclass
class Press:
    """One note, one press, at a time worked out from its own speed."""
    lane:    int
    kind:    str
    delay_s: float     # from now until the key should go down
    speed:   float     # px/s the note was measured at
    arrival: float     # monotonic time its bottom edge reaches HIT_Y


@dataclass
class Track:
    lane:    int
    t:       float
    bottom:  float                      # screen y of the note's bottom edge
    v:       float = 0.0
    height:  float = 0.0
    misses:  int = 0
    fired:   bool = False
    smooth:  float = 0.0                # bottom edge off the fitted line —
                                        # what the arrival is worked out from
    born_at: float = 0.0                # where it was first seen — `samples`
                                        # is a rolling window and cannot
                                        # answer "how far has it come"
    samples: deque = field(default_factory=lambda: deque(maxlen=FIT_WINDOW))
    votes:   Counter = field(default_factory=Counter)
    weak:    Counter = field(default_factory=Counter)

    @property
    def kind(self) -> str:
        """What the note is, by majority over the frames it was seen in.

        Frames where the glyph was still half outside the zone get their own
        tally and are only consulted when there is nothing better: a note
        picked up late (the bot attaching mid-round, or a track re-acquired
        low down) would otherwise reach the hit line with no verdict at all
        and be dropped in silence — the exact failure this whole rewrite
        exists to remove.
        """
        for tally in (self.votes, self.weak):
            if tally:
                return tally.most_common(1)[0][0]
        return EMPTY


# ── Pure image processing ────────────────────────────────────────────────

def region_masks(bgr: np.ndarray) -> np.ndarray:
    """(5, H, W) bool — lit / cyan / green / red / magenta.

    Exactly the bands classify_hsv uses, computed once for the whole capture
    so both the row profiles and every note's own classification come off
    the same arrays instead of re-converting per note.
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(np.int16)
    sat = hsv[:, :, 1].astype(np.int16)
    val = hsv[:, :, 2].astype(np.int16)

    lit  = val >= _LIT_V
    neon = lit & (sat >= _NEON_S)
    return np.stack([
        lit,
        neon & (hue >= _CYAN_LOW)  & (hue <= _CYAN_HIGH),
        neon & (hue >= _GREEN_LOW) & (hue <= _GREEN_HIGH),
        (val >= _IDLE_V) & (sat >= _RED_S) & ((hue <= _RED_LOW) | (hue >= _RED_HIGH)),
        lit & (sat >= _MAGENTA_S) & (hue >= _MAGENTA_LOW) & (hue <= _MAGENTA_HIGH),
    ])


def flash_rows_lit(bgr: np.ndarray) -> np.ndarray:
    """(lanes,) — how many sampled rows below the tracked zone are washed out.

    Only brightness matters here, and only roughly: the hit flash is a solid
    block hundreds of pixels tall and a whole lane wide, so reading every
    fourth row of every second column loses nothing and costs a sixteenth as
    much. That matters — this runs on every frame, and frames are what the
    speed fit is made of.

    Brightness is the largest of B, G and R — HSV's V, the same measure the
    tracked rows are thresholded on. Not greyscale: that weights green and
    blue down (0.587G + 0.114B), so the game's cyan wash reads far darker
    than it is and the flash goes unseen. Taking the max costs nothing once
    the frame has been thinned out.
    """
    if bgr.size == 0:
        return np.zeros(LANES, np.int32)
    small = bgr[::FLASH_STEP_Y, ::FLASH_STEP_X]
    rows, width = small.shape[:2]
    lane_w = width // LANES
    if lane_w == 0 or rows == 0:
        return np.zeros(LANES, np.int32)
    lit = small.max(axis=2) >= _LIT_V
    per_lane = lit[:, :lane_w * LANES].reshape(rows, LANES, lane_w)
    return (per_lane.mean(axis=2) >= FLASH_LIT_FRAC).sum(axis=0).astype(np.int32)


def lane_profiles(masks: np.ndarray) -> np.ndarray:
    """(lanes, 5, H) — how many pixels of each row fall in each mask.

    One reshape-and-sum for all four lanes at once; a lane is exactly
    LANE_W wide, so the split is free.
    """
    planes, h, w = masks.shape
    usable = (w // LANES) * LANES
    grid = masks[:, :, :usable].reshape(planes, h, LANES, w // LANES)
    return grid.sum(axis=3).transpose(2, 0, 1)


def close_profile(on: np.ndarray) -> np.ndarray:
    """Bridge the gaps between an arrow's own bars — see CLOSE_K."""
    col = on.astype(np.uint8).reshape(-1, 1)
    closed = cv2.morphologyEx(col, cv2.MORPH_CLOSE, _CLOSE_KERNEL)
    return closed.ravel().astype(bool)


def find_blobs(on: np.ndarray, min_h: int = MIN_BLOB_H) -> list[tuple[int, int]]:
    """[(top_row, bottom_row_exclusive)] for every run at least min_h tall."""
    edges = np.diff(np.concatenate(([0], on.view(np.int8), [0])))
    starts = np.flatnonzero(edges == 1)
    stops  = np.flatnonzero(edges == -1)
    return [(int(a), int(b)) for a, b in zip(starts, stops) if b - a >= min_h]


def locate_field(bgr: np.ndarray, origin_left: int, origin_top: int) -> Geometry | None:
    """Find the playfield by its own bright cyan border, in screen coords.

    The whole geometry hangs off one rectangle, and every coordinate in this
    module used to be a hand-measured constant that quietly stopped being
    true the moment the game window changed size. The border is a solid,
    unmistakable cyan frame around the lanes while a round is up, so it can
    simply be looked up instead. Returns None when it is not on screen (the
    menus, a loading screen), and the caller keeps the measured defaults.
    """
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    frame = ((hsv[:, :, 2] >= 150) & (hsv[:, :, 1] >= 120)
             & (hsv[:, :, 0] >= _CYAN_LOW) & (hsv[:, :, 0] <= _CYAN_HIGH))
    h, w = frame.shape

    # The two side rails are the only thing on screen that is a solid cyan
    # line hundreds of pixels tall, so they fix left and right outright.
    columns = np.flatnonzero(frame.sum(axis=0) > h * 0.35)
    if columns.size < 2:
        return None
    vert = int(columns[0]), int(columns[-1])

    # Top and bottom come off the left rail's own extent, not off row sums
    # over the whole frame: the game draws a full-width cyan "ИДЕАЛЬНО" bar
    # below the playfield on a good hit, and a row-sum reading would happily
    # stretch the field down to swallow it. The rail is one unbroken run, so
    # the longest run of lit rows in it is exactly the field.
    rail = frame[:, vert[0]:vert[0] + 3].any(axis=1)
    edges_ = np.diff(np.concatenate(([0], rail.view(np.int8), [0])))
    starts = np.flatnonzero(edges_ == 1)
    stops  = np.flatnonzero(edges_ == -1)
    if starts.size == 0:
        return None
    longest = int(np.argmax(stops - starts))
    horiz = int(starts[longest]), int(stops[longest] - 1)

    # Step inward off the border itself: it is ~8px thick, and its own lit
    # pixels would otherwise sit inside the tracked zone forever.
    thickness = 8
    left, right = vert[0] + thickness, vert[1] - thickness
    top, bottom = horiz[0] + thickness, horiz[1] - thickness
    width, height = right - left + 1, bottom - top + 1
    if width < 400 or height < 600 or width > w or height > h:
        return None
    return Geometry(left=left + origin_left, top=top + origin_top,
                    width=width, height=height)


def classify_blob(profile: np.ndarray, top: int, bottom: int,
                  lane_w: int, th: Thresholds) -> str:
    """What kind of note occupies rows [top, bottom) of one lane.

    Judged over the whole glyph rather than a 30px slice of it, which is
    what makes the washed-out `dislike` fallback rare instead of routine —
    the shares are measured on hundreds of the note's own rows.
    """
    area = max(1, (bottom - top) * lane_w)
    n_lit = int(profile[LIT, top:bottom].sum())
    if n_lit == 0:
        return EMPTY
    return decide_kind(
        lit_share     = n_lit / area,
        red_share     = float(profile[RED, top:bottom].sum()) / area,
        green_share   = float(profile[GREEN, top:bottom].sum()) / n_lit,
        cyan_share    = float(profile[CYAN, top:bottom].sum()) / n_lit,
        magenta_share = float(profile[MAGENTA, top:bottom].sum()) / n_lit,
        th            = th,
    )


def fit_line(samples: deque) -> tuple[float, float]:
    """(px/s, where the note is now) through the track's own history.

    Least squares over the whole descent rather than the last pair or the
    last reading. Both halves matter, and for the same reason: one frame's
    measurement of the note's edge carries about 5px of jitter (measured),
    and the press is scheduled some 300px ahead on it.

    Taking the *position* off the fitted line too — rather than using the
    latest raw reading, which is what this used to do — was worth as much as
    everything else here put together. Replayed over a recorded round it cut
    the spread of the predicted arrival from 21ms to 12ms and the share of
    notes landing more than 40ms out from 9% to 2%.

    Fitting a straight line is safe because notes do not accelerate: across
    354 tracked descents the median curvature came to 3 px/s², which over
    the whole prediction horizon is under 4ms.
    """
    n = len(samples)
    if n < 2:
        return 0.0, samples[-1][1] if n else 0.0
    ts = np.fromiter((s[0] for s in samples), float, n)
    ys = np.fromiter((s[1] for s in samples), float, n)
    t0 = ts[0]
    ts = ts - t0
    t_mean, y_mean = ts.mean(), ys.mean()
    denom = float(((ts - t_mean) ** 2).sum())
    if denom <= 0.0:
        return 0.0, float(ys[-1])
    speed = float(((ts - t_mean) * (ys - y_mean)).sum() / denom)
    return speed, float(y_mean + speed * (ts[-1] - t_mean))


# ── The tracker ──────────────────────────────────────────────────────────

class LaneTracker:
    """Per-lane note tracking and press scheduling.

    Stateful but Qt-free and win32-free: feed it a timestamp and the
    captured zone, get back the presses to schedule. Everything about
    *when* a press happens is decided here; the caller only posts keys.
    """

    def __init__(self, thresholds: Thresholds | None = None,
                 geometry: Geometry | None = None):
        self._th  = thresholds or Thresholds()
        self._geo = geometry or DEFAULT_GEOMETRY
        self.reset()

    @property
    def geometry(self) -> Geometry:
        return self._geo

    def set_geometry(self, geometry: Geometry):
        """Point the tracker at a freshly located playfield — everything in
        flight refers to the old one, so nothing survives the move."""
        self._geo = geometry
        self.reset()

    def reset(self):
        self._tracks: list[list[Track]] = [[] for _ in range(LANES)]
        self._last_speed = [DEFAULT_SPEED] * LANES
        self._scheduled: list[list[float]] = [[] for _ in range(LANES)]
        # Why committed tracks were turned down, by reason. The bot prints
        # this periodically: when it presses wrongly or not at all, the split
        # says which check is doing it, instead of leaving it to guesswork.
        self.refusals: Counter = Counter()
        self.committed = 0
        # Presses waiting to be confirmed by their lane lighting up, and the
        # running tally of how many did.
        self._awaiting: list[list[dict]] = [[] for _ in range(LANES)]
        self.scored = 0
        self.missed = 0

    def configure(self, thresholds: Thresholds):
        self._th = thresholds

    def update(self, now: float, bgr: np.ndarray) -> list[Press]:
        """One frame in, the presses it decided on out."""
        cut = min(self._geo.flash_row, bgr.shape[0])
        profiles = lane_profiles(region_masks(bgr[:cut]))
        flash = flash_rows_lit(bgr[cut:])
        presses: list[Press] = []
        for lane in range(LANES):
            presses += self._update_lane(lane, now, profiles[lane])
            self._verify(lane, now, int(flash[lane]))
        return presses

    # ── one lane ─────────────────────────────────────────────────────────

    def _update_lane(self, lane: int, now: float,
                     profile: np.ndarray) -> list[Press]:
        on = profile[LIT] >= ROW_ON
        if on.mean() >= BLIND_FRAC:
            # Not notes — something has lit the whole lane. Leave every
            # track exactly as it is and let it coast; missing a frame
            # costs nothing when the position is fitted over many.
            return []

        blobs = find_blobs(close_profile(on))
        tracks = self._tracks[lane]
        taken: set[int] = set()

        for tr in tracks:
            idx = self._match(tr, now, blobs, taken)
            if idx is None:
                tr.misses += 1
                continue
            taken.add(idx)
            self._absorb(tr, now, profile, *blobs[idx])

        # A note enters the lane at the top and is visible all the way down,
        # so a track may only be born up there. Anything appearing lower is
        # the tail of a note already on its way out, or the glow of a hit —
        # neither can be measured, and both would otherwise reach the commit
        # line within a frame or two and fire a press with nothing under it.
        born_below = int(len(on) * BORN_ABOVE_F)
        for i, (top, bottom) in enumerate(blobs):
            if i in taken or bottom > born_below:
                continue
            tr = Track(lane=lane, t=now, bottom=self._geo.zone_top + bottom)
            self._absorb(tr, now, profile, top, bottom)
            tracks.append(tr)

        presses = [p for tr in tracks
                   if (p := self._maybe_press(tr, now)) is not None]

        self._tracks[lane] = [tr for tr in tracks if tr.misses < MAX_MISSES]
        cutoff = now - 2.0
        self._scheduled[lane] = [t for t in self._scheduled[lane] if t > cutoff]
        return presses

    def _match(self, tr: Track, now: float, blobs: list[tuple[int, int]],
               taken: set[int]) -> int | None:
        dt = max(0.0, now - tr.t)
        moved = (tr.v or self._last_speed[tr.lane]) * dt
        predicted = tr.bottom + moved
        tol = MATCH_TOL_PX + MATCH_TOL_REL * abs(moved)
        best, best_d = None, tol
        for i, (_, bottom) in enumerate(blobs):
            if i in taken:
                continue
            d = abs((self._geo.zone_top + bottom) - predicted)
            if d < best_d:
                best, best_d = i, d
        return best

    def _absorb(self, tr: Track, now: float, profile: np.ndarray,
                top: int, bottom: int):
        """Fold one frame's blob into a track.

        The bottom edge is only believed while the whole note is inside the
        zone: once it starts clipping on the capture's own bottom row that
        edge stops moving, and a speed fitted through it would collapse
        toward zero exactly when the press is being scheduled.
        """
        clipped = bottom >= len(profile[LIT]) - 2
        height = bottom - top
        if clipped and tr.height:
            position = self._geo.zone_top + top + tr.height
        else:
            position = self._geo.zone_top + bottom
            tr.height = max(tr.height, height)

        if not tr.samples:
            tr.born_at = position
        tr.samples.append((now, position))
        tr.bottom = position
        tr.t      = now
        tr.misses = 0
        speed, smooth = fit_line(tr.samples)
        if SPEED_MIN <= speed <= SPEED_MAX:
            tr.v = speed
            tr.smooth = smooth
        kind = classify_blob(profile, top, bottom, self._geo.lane_w, self._th)
        if height >= VOTE_MIN_H and not clipped:
            tr.votes[kind] += 1
        else:
            tr.weak[kind] += 1

    def _maybe_press(self, tr: Track, now: float) -> Press | None:
        if tr.fired or tr.bottom < self._geo.commit_y:
            return None
        tr.fired = True   # committed either way — a refusal is final too
        self.committed += 1

        why = self._refuse(tr)
        if why:
            self.refusals[why] += 1
            return None

        arrival = now + (self._geo.hit_y - tr.smooth) / tr.v
        if any(abs(arrival - s) < MIN_PRESS_GAP_S for s in self._scheduled[tr.lane]):
            self.refusals["дубль"] += 1
            return None      # the same note, tracked twice — see MIN_PRESS_GAP_S
        self._scheduled[tr.lane].append(arrival)
        self._last_speed[tr.lane] = tr.v

        return Press(lane=tr.lane, kind=tr.kind, speed=tr.v, arrival=arrival,
                     delay_s=max(0.0, arrival - now - PRESS_LEAD_S))

    def _refuse(self, tr: Track) -> str | None:
        """Why this track must not press, or None if it may.

        Deliberately strict. A missed note costs one hit; a press with
        nothing under it costs the whole combo, and a detector that fires on
        glow does it over and over.
        """
        if len(tr.samples) < MIN_SAMPLES:
            return "мало кадров"
        if not SPEED_MIN <= tr.v <= SPEED_MAX:
            return "нет скорости"
        if tr.bottom - tr.born_at < MIN_TRAVEL_PX:
            return "мало прошло"
        if not MIN_NOTE_H <= tr.height <= MAX_NOTE_H:
            return "не тот размер"
        if (self._geo.hit_y - tr.bottom) / tr.v > MAX_LEAD_S:
            return "слишком далеко"
        if tr.kind not in PRESSABLE:
            return tr.kind
        return None

    # ── did that press actually score? ───────────────────────────────────

    def _verify(self, lane: int, now: float, rows_lit: int):
        """Match each scheduled press against its lane lighting up.

        The only feedback the game gives is that flash, and it is the
        difference between the two failures that look identical from
        outside: a press sent at the wrong moment, and a press that was
        never registered at all. Counting them separately is what makes the
        last percent diagnosable rather than anecdotal.
        """
        pending = self._awaiting[lane]
        if not pending:
            return
        if rows_lit > FLASH_MIN_ROWS:
            due = [p for p in pending
                   if not p["ok"]
                   and p["at"] + VERIFY_FROM_S <= now <= p["at"] + VERIFY_UNTIL_S]
            if due:
                # Nearest first: two notes in one lane are never closer than
                # MIN_PRESS_GAP_S, so the closest pending press owns it.
                min(due, key=lambda p: abs(now - p["at"]))["ok"] = True

        still = []
        for p in pending:
            if now <= p["at"] + VERIFY_UNTIL_S:
                still.append(p)
            elif p["ok"]:
                self.scored += 1
            else:
                self.missed += 1
        self._awaiting[lane] = still

    def expect_hit(self, lane: int, arrival: float):
        """Told by the bot when it schedules a press, so _verify can look for
        the flash that should follow it."""
        self._awaiting[lane].append({"at": arrival, "ok": False})

    # ── introspection, for the UI and tests ──────────────────────────────

    def live_tracks(self) -> list[Track]:
        return [tr for lane in self._tracks for tr in lane]
