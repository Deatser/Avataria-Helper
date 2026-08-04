# modules/hockey/players.py
"""Finding the other players (defenders) on the rink — six templates (three
photos the user cropped, plus a horizontal mirror of each, since a
defender can face either way) matched against the rink's own screenshot.

Six templates matching the same physical player is the expected case, not
an error — _dedupe keeps the best-scoring box in any cluster of
overlapping matches rather than reporting the same defender six times.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2

from app.core.capture import grab_window
from app.core.template_match import find_all, load_template

# Photo + mirror, one pair per pose the user captured — a defender's sprite
# can face either direction, and nothing here tells the two apart, so both
# get matched every scan.
PLAYER_TEMPLATES = [
    "hockey_player1.png", "hockey_player1_mirror.png",
    "hockey_player2.png", "hockey_player2_mirror.png",
    "hockey_player3.png", "hockey_player3_mirror.png",
]

# Below this, a match is noise, not a defender — same register best_match
# uses elsewhere in the project (TM_CCOEFF_NORMED).
MATCH_THRESHOLD = 0.55

# Two boxes count as "the same defender" once they overlap by more than
# this fraction of the smaller one's own area, OR their centres land
# within _DEDUPE_CENTER_DIST of each other. Overlap alone under-merges
# here: the three source photos are very different crop sizes (195x116,
# 159x77, 137x93), so two matches on the very same real defender, through
# two different templates, do not always overlap by much even when their
# centres sit right on top of each other — a live run (2026-08-04) showed
# 4-5 "threats" reported for what was really a couple of real defenders.
_DEDUPE_OVERLAP = 0.35
_DEDUPE_CENTER_DIST = 70


@dataclass
class Detection:
    x: int          # top-left, screen coordinates
    y: int
    w: int
    h: int
    score: float
    template: str

    @property
    def centre(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


def _overlap_fraction(a: Detection, b: Detection) -> float:
    left, right = max(a.x, b.x), min(a.x + a.w, b.x + b.w)
    top, bottom = max(a.y, b.y), min(a.y + a.h, b.y + b.h)
    if right <= left or bottom <= top:
        return 0.0
    inter = (right - left) * (bottom - top)
    smaller = min(a.w * a.h, b.w * b.h)
    return inter / smaller if smaller else 0.0


def _center_dist(a: Detection, b: Detection) -> float:
    ax, ay = a.centre
    bx, by = b.centre
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _same_defender(a: Detection, b: Detection) -> bool:
    return (_overlap_fraction(a, b) > _DEDUPE_OVERLAP
           or _center_dist(a, b) < _DEDUPE_CENTER_DIST)


def _dedupe(detections: list[Detection]) -> list[Detection]:
    """Best score first; a box already close enough to a kept one is the
    same defender seen through a different template, not a second one."""
    kept: list[Detection] = []
    for det in sorted(detections, key=lambda d: d.score, reverse=True):
        if any(_same_defender(det, other) for other in kept):
            continue
        kept.append(det)
    return kept


def scan(hwnd: int, region: dict,
        threshold: float = MATCH_THRESHOLD) -> list[Detection]:
    """Every defender found inside `region` (screen rectangle, mss shape),
    one entry per real player regardless of how many of the six templates
    matched it."""
    try:
        frame = grab_window(hwnd, region)
    except Exception:
        return []
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    found: list[Detection] = []
    for name in PLAYER_TEMPLATES:
        template = load_template(name)
        if template is None:
            continue
        th, tw = template.shape[:2]
        for score, x, y in find_all(gray, template, threshold):
            found.append(Detection(
                x=region["left"] + x, y=region["top"] + y,
                w=tw, h=th, score=score, template=name))
    return _dedupe(found)


# ── Tracking ─────────────────────────────────────────────────────────────────
# A defender is only "stood still or moving left/right" — scan() alone has
# no memory, so this is what turns two consecutive scans into a velocity
# worth predicting from.

_MAX_MATCH_DIST = 90   # further than this between scans is a different
                       # defender, not the same one having moved
_MAX_MISSES     = 3    # scans a tracked defender can go undetected before
                       # it is dropped, rather than kept forever on a stale
                       # position
_VX_SMOOTH      = 0.5  # EMA factor on each new velocity sample — smooths
                       # over a jittery single-frame reading without lagging
                       # a real speed change out for long


@dataclass
class Tracked:
    x: float
    y: float
    vx: float = 0.0     # px/second, smoothed
    last_t: float = 0.0
    misses: int = 0
    min_x: float = 0.0  # leftmost/rightmost x ever seen for this entry —
    max_x: float = 0.0  # its own patrol bounds, learned as they're crossed


class Tracker:
    """One of these per bot run — update() every scan, predict_x() when
    working out where a defender will be at some future time."""

    def __init__(self):
        self._entries: list[Tracked] = []

    def update(self, detections: list[Detection], now: float) -> list[Tracked]:
        unmatched = list(detections)
        for entry in self._entries:
            nearest, nearest_d = None, _MAX_MATCH_DIST
            for det in unmatched:
                cx, cy = det.centre
                d = ((cx - entry.x) ** 2 + (cy - entry.y) ** 2) ** 0.5
                if d < nearest_d:
                    nearest, nearest_d = det, d
            if nearest is None:
                entry.misses += 1
                continue
            dt = max(1e-3, now - entry.last_t)
            cx, cy = nearest.centre
            sample_vx = (cx - entry.x) / dt
            entry.vx = entry.vx * (1 - _VX_SMOOTH) + sample_vx * _VX_SMOOTH
            entry.x, entry.y = cx, cy
            entry.min_x = min(entry.min_x, cx)
            entry.max_x = max(entry.max_x, cx)
            entry.last_t = now
            entry.misses = 0
            unmatched.remove(nearest)

        self._entries = [e for e in self._entries if e.misses <= _MAX_MISSES]
        for det in unmatched:
            cx, cy = det.centre
            self._entries.append(
                Tracked(x=cx, y=cy, last_t=now, min_x=cx, max_x=cx))
        return list(self._entries)

    def predict_x(self, entry: Tracked, target_t: float) -> float:
        """Where this defender's own x will be at `target_t`.

        A shot takes on the order of a second and a half to arrive — long
        enough that a defender patrolling back and forth (2026-08-04: "с
        какой скоростью они едут с такой и будут всегда... едут туда
        сюда") can easily reverse direction before it does, so straight-
        line extrapolation over that whole window is wrong on purpose,
        not just imprecise. Once min_x/max_x have actually seen the
        defender cross some real span, position is instead unfolded as a
        constant-speed bounce between those two bounds — a triangle wave,
        reflecting at each end exactly the way the patrol itself does.
        Before that span exists (a defender only just spotted, or one
        standing still), there is nothing yet to bounce between, so this
        falls back to the same straight-line guess.
        """
        dt = target_t - entry.last_t
        span = entry.max_x - entry.min_x
        if dt <= 0 or span < 1.0 or abs(entry.vx) < 1e-6:
            return entry.x + entry.vx * dt

        speed = abs(entry.vx)
        direction = 1.0 if entry.vx > 0 else -1.0
        travelled = (entry.x - entry.min_x) + direction * speed * dt
        period = 2 * span
        offset = travelled % period
        if offset < 0:
            offset += period
        if offset <= span:
            return entry.min_x + offset
        return entry.min_x + (period - offset)
