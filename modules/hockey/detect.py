# modules/hockey/detect.py
"""Finding defenders: a red helmet, confirmed against the player photos.

Every defender on the rink is another player's own avatar — different face,
hair and jersey every time — so matching a cropped sprite *alone* only ever
works on the exact avatars it was cropped from. The first version of this
module did that, with six templates cut out of three players, and would go
blind the moment a level showed anyone else. The one thing all of them share
is a red helmet, so that is what finds candidates.

Red alone is not enough to accept one, though (live, 2026-08-08: "он ищет
только красный но это задевает лишнее"), so every candidate is then scored
against those six photos and anything scoring too low is dropped. Used this
way round the photos cost nothing when they do not generalise — an unseen
avatar still gets *found* by its helmet, it just needs the score threshold
low enough not to throw it away, which is why the threshold is a setting and
why the debug snapshot prints the score of every candidate including the
rejected ones.

A row can hold more than one defender (2026-08-09), so every accepted helmet
inside a row's strip is reported, not just the best one. Which of them is
the one that actually moves is not decided here — see motion.py, which
settles it by watching rather than by looking at a single frame.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from app.core.template_match import TEMPLATES_DIR, best_match, load_template

# Red sits at the seam of the hue circle, so it takes two bands rather than
# one — a helmet's pixels land in either depending on how the shading falls.
_HUE_LOW  = (0, 10)
_HUE_HIGH = (170, 180)

# The helmet alone, plus its mirror — a defender's sprite can face either
# way and nothing here tells the two apart, so both are scored.
#
# Whole-player crops were tried first and dropped (2026-08-09). They carry
# the avatar's own face, hair and jersey, which differ for every player, so
# most of what was being compared was exactly the part that never matches;
# the helmet is the part they share. Cropping to it also makes the template
# small, which makes the match cheap.
HELMET_TEMPLATES = ["Hockey_helmet.png", "Hockey_helmet_mirror.png"]

# Slack around where the sprite is *expected* to sit. Small on purpose: the
# helmet's own blob says where the head is and how big it is, so the
# template can be placed rather than hunted for — this is only there to
# absorb a few pixels of centroid wobble.
_MATCH_MARGIN = 14

# Matching runs at half size. A quarter of the pixels for a score that moves
# in the third decimal, and the scan was measured 38ms against a 30ms tick
# before it (2026-08-09).
_MATCH_SCALE = 0.5

# How far a candidate may be from a template's own size before the pair is
# not worth comparing at all. The rink is drawn in perspective — the boards
# measured 439px wide on the top row and 635 on the bottom — so a defender
# near the puck is half again the size of one near the goal, and matching
# every template at its own fixed size scored the far rows so low they never
# passed the photo gate (row 5 was seen in 0% of frames, 2026-08-09).
_MIN_TEMPLATE_SCALE = 0.4
_MAX_TEMPLATE_SCALE = 2.5

# Finding the helmet inside a template: these crops are clean, so the bar
# can sit lower than the live thresholds without letting anything in.
_TEMPLATE_SAT_MIN = 100
_TEMPLATE_VAL_MIN = 60
_TEMPLATE_BLOB_MIN = 100

# Blobs that get scored against the helmet photos per scan, largest first.
# Two small matchTemplate calls each, so the ceiling is generous: a row can
# hold several defenders, so five rows is nowhere near five candidates.
_MAX_SCORED = 18

# The static-marking mask (see HelmetDetector._freeze_static) is built from
# this many frames; a pixel red in at least this share of them is scenery.
#
# What the mask is for is narrower than it first looks. A marking being
# reported *as* a helmet is already handled by shape and by the photo score,
# so the mask earns its keep on one case only: a helmet passing over a red
# line merges with it into a single long blob, which shape then rejects — so
# without the mask that defender goes missing for exactly as long as it
# overlaps the line, which is both intermittent and worst near the middle of
# the rink. Optional, and off by default, because it costs a warm-up and
# this rink may well have no red lines at all.
_STATIC_FRAMES = 80
_STATIC_RATIO  = 0.90

# Rows thinner than this cannot be searched meaningfully — see
# Geometry.lane_rows, which clamps a badly calibrated row to nothing.
_MIN_STRIP_H = 10

# Longest a blob may be relative to its own width before it stops being a
# plausible helmet. This exists for rink markings: a red line crossing a row
# is easily helmet-sized by area alone (a 780x4 line is 3120px, right in the
# middle of the accepted range) and, being bigger than a real helmet, would
# win the pick outright. By shape the two are nowhere near each other.
_MAX_ASPECT = 4.0

# Sanity ceiling on a helmet's area. Not a tuning knob and not in the config
# any more: perspective means a near defender's helmet is a couple of times
# the area of a far one, and the 4000 that used to sit in the config was a
# guess that rejected the near rows outright (a helmet at 1.4x the template
# size measures 5198px). Shape and the photo score do the discriminating;
# this only stops a whole red banner becoming a candidate.
_MAX_BLOB_AREA = 9000

# How much two accepted helmets may overlap before the weaker one is taken
# to be a piece of the stronger rather than a defender of its own. Defenders
# overlap on screen — the one nearer the puck is drawn in front — and an
# occluding sprite can cut the helmet behind it into fragments that each
# pass for a helmet. Two real defenders never share a place, so overlap is
# the tell.
_NMS_OVERLAP = 0.35

# scan_all reports blobs found without reference to any row at all.
NO_LANE = -1

# A helmet the model is already expecting is judged more leniently than one
# turning up out of nowhere. The photo score of a real defender swings
# between roughly 0.63 and 0.89 frame to frame — partial occlusion, the
# sprite's own animation — so a threshold anywhere in that band makes the
# same defender appear and vanish from frame to frame, which is what starves
# a track. Where a track says somebody should be, weaker evidence is enough;
# _EXPECTED_FLOOR keeps that from becoming "anything red will do".
_EXPECTED_PX     = 45
_EXPECTED_RELIEF = 0.75
_EXPECTED_FLOOR  = 0.40

# A helmet-shaped blob that has not moved for a second and a half is a
# defender standing still, whatever the photo score makes of its avatar. The
# score is what tells a helmet from a red banner, and it is unreliable
# exactly where it matters least: a standing defender cannot be the puck,
# cannot be an animation frame, and cannot be anywhere else next time. A row
# with two standers reported one of them, every frame, for a whole run
# (2026-08-10).
#
# _EXPECTED_FLOOR still applies, so this admits a helmet the gate was too
# strict for, not any red thing that happens to sit still.
_STEADY_FRAMES = 50
_STEADY_SHARE  = 0.70
_STEADY_PX     = 10

# A helmet is the topmost red of its sprite, so red inside somebody's body
# and below his head is his jersey, not a defender of his own. Measured:
# helmet at (1373, 601) in row 2, chest at (1366, 656) — 7px across and 55px
# down, which lands in row 3's strip and was reported as a defender standing
# in an empty row for the rest of the run (2026-08-10).
#
# Narrow in x on purpose. Two real defenders in neighbouring rows do line up
# now and then, and losing one of those for the moment they do is cheap —
# the tracker coasts through it — but a phantom that never moves is
# permanent.
_BODY_PART_DROP_PX = 25          # never suppress something at head height
_BODY_PART_WIDTH   = 3           # ...within body_w / this, across
# ...and never something sitting on a row's own line. Helmets hold their
# height: measured across a run, rows read 572–574, 601–605, 680–681,
# 738–740. A jersey lands wherever the sprite puts it — the one measured was
# 20px below row 3's line. Without this the rule fires the other way round
# and takes out a standing defender whenever a patrol passes above him.
_BODY_PART_OFF_LINE = 12


def _overlap(a, b) -> float:
    """How much of the smaller of two boxes the other one covers."""
    ax, ay, aw, ah = a.frame_box
    bx, by, bw, bh = b.frame_box
    left, right = max(ax, bx), min(ax + aw, bx + bw)
    top, bottom = max(ay, by), min(ay + ah, by + bh)
    if right <= left or bottom <= top:
        return 0.0
    smaller = min(aw * ah, bw * bh)
    return (right - left) * (bottom - top) / smaller if smaller else 0.0


def _suppress_overlaps(hits: list) -> list:
    """Drop a helmet sitting on top of a better one — see _NMS_OVERLAP.

    Ordered by photo score first and size second, so the fragment that
    survives is the one that most looks like a helmet, not merely the
    largest patch of red.
    """
    kept: list = []
    for hit in sorted(hits, key=lambda h: (h.match, h.area), reverse=True):
        if any(_overlap(hit, other) > _NMS_OVERLAP for other in kept):
            continue
        kept.append(hit)
    return kept


def _shrink(image: np.ndarray, factor: float) -> np.ndarray | None:
    """`image` resized by `factor`, or None when that leaves nothing to
    match against."""
    height = int(image.shape[0] * factor)
    width  = int(image.shape[1] * factor)
    if height < 4 or width < 4:
        return None
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)


@dataclass(frozen=True)
class _Profile:
    """A player photo plus where its own helmet sits inside it — the two
    numbers that let the template be *placed* over a candidate at the right
    size instead of searched for at a guessed one."""
    gray: np.ndarray
    helmet_w: int
    helmet_cx: float
    helmet_top: int


@lru_cache(maxsize=16)
def _profile(name: str) -> _Profile | None:
    """Locate the helmet inside one template, once.

    Topmost red blob rather than biggest: one of these avatars wears a red
    jersey, which is the larger patch of the two, and anchoring the sprite
    by its chest instead of its head would put every window half a body low.
    """
    colour = cv2.imread(str(TEMPLATES_DIR / name), cv2.IMREAD_COLOR)
    gray = load_template(name)
    if colour is None or gray is None:
        return None

    hsv = cv2.cvtColor(colour, cv2.COLOR_BGR2HSV)
    mask = cv2.bitwise_or(
        cv2.inRange(hsv, (_HUE_LOW[0], _TEMPLATE_SAT_MIN, _TEMPLATE_VAL_MIN),
                    (_HUE_LOW[1], 255, 255)),
        cv2.inRange(hsv, (_HUE_HIGH[0], _TEMPLATE_SAT_MIN, _TEMPLATE_VAL_MIN),
                    (_HUE_HIGH[1], 255, 255)))
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)

    best, best_top = None, None
    for i in range(1, count):
        if int(stats[i, cv2.CC_STAT_AREA]) < _TEMPLATE_BLOB_MIN:
            continue
        top = int(stats[i, cv2.CC_STAT_TOP])
        if best_top is None or top < best_top:
            best, best_top = i, top
    if best is None:
        return None
    return _Profile(gray=gray,
                    helmet_w=int(stats[best, cv2.CC_STAT_WIDTH]),
                    helmet_cx=float(centroids[best][0]),
                    helmet_top=best_top)


@dataclass
class LaneHit:
    """One candidate, in screen coordinates. `lane` is the row it was found
    in, or NO_LANE when it came from scan_all, which runs before rows
    exist. A rejected candidate is still returned by scan_debug — seeing
    what was thrown away, and how narrowly, is the whole point of the
    debug snapshot."""
    lane: int
    x: int      # centroid — this is the number everything downstream predicts
    y: int
    area: int
    match: float          # best score against the player photos, 0..1
    accepted: bool
    box: tuple[int, int, int, int]   # left, top, w, h, screen coordinates
    frame_box: tuple[int, int, int, int]   # the same, inside the rink grab
    # Let in by a relief rather than by its own photo score — see
    # _was_expected and _is_steady. Such a hit has no evidence of its own
    # that it is a helmet, which is why _drop_body_parts judges it harder.
    relieved: bool = False


class HelmetDetector:
    """One per bot run. Holds the static-marking mask, which is the only
    state it accumulates; everything else is per-frame."""

    def __init__(self, geom, cfg):
        self._geom      = geom
        self._sat_min   = int(cfg.red_sat_min)
        self._val_min   = int(cfg.red_val_min)
        self._area_min  = int(cfg.blob_area_min)
        self._match_min = float(getattr(cfg, "player_match_min", 0.0))
        self._use_static = bool(getattr(cfg, "static_mask", False))
        self._counter: np.ndarray | None = None   # per-pixel red tally
        self._counted = 0
        self._static: np.ndarray | None = None    # frozen mask, bool
        # Recent frames' plausible blobs, for _is_steady.
        self._recent: deque[tuple[float, ...]] = deque(maxlen=_STEADY_FRAMES)

    # ── Static marking mask ──────────────────────────────────────────────

    def static_ready(self) -> bool:
        """Whether detection is allowed to be trusted yet. Always true when
        the mask is switched off, which is the default."""
        return not self._use_static or self._static is not None

    def static_progress(self) -> tuple[int, int]:
        return self._counted, _STATIC_FRAMES

    def reset_static(self):
        self._counter = None
        self._counted = 0
        self._static  = None

    def _learn_static(self, red: np.ndarray):
        if self._counter is None or self._counter.shape != red.shape:
            self._counter = np.zeros(red.shape, np.int32)
            self._counted = 0
        self._counter += (red > 0)
        self._counted += 1
        if self._counted >= _STATIC_FRAMES:
            self._static = self._freeze_static()

    def _freeze_static(self) -> np.ndarray:
        """Turn the tally into a mask of rink scenery.

        A defender who never moves is red in every frame too, so a plain
        "always red → scenery" rule would mask a standing defender out and
        make that row permanently invisible — the exact rows where a shot is
        easiest to get wrong. Rink markings are long lines or small specks;
        anything helmet-shaped is far more likely to be someone standing
        still, so those components are put back.
        """
        mask = (self._counter >= _STATIC_RATIO * self._counted).astype(np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        for i in range(1, count):
            if self._plausible(stats[i]):
                mask[labels == i] = 0
        return mask.astype(bool)

    # ── What counts as a helmet ──────────────────────────────────────────

    def _plausible(self, stat) -> bool:
        """Whether one connected component could be a helmet at all.

        The same test wherever a blob is judged, so the detector and the
        static mask can never disagree about what one is — a shape the mask
        decided to keep must be a shape the scan is willing to report.
        """
        area = int(stat[cv2.CC_STAT_AREA])
        if not self._area_min <= area <= _MAX_BLOB_AREA:
            return False
        w = int(stat[cv2.CC_STAT_WIDTH])
        h = int(stat[cv2.CC_STAT_HEIGHT])
        if w <= 0 or h <= 0:
            return False
        return max(w, h) / min(w, h) <= _MAX_ASPECT

    def _photo_score(self, gray: np.ndarray, left: int, top: int,
                     width: int) -> float:
        """Best match against any of the player photos, for the sprite this
        helmet would belong to.

        The helmet's own blob does two jobs here. Its width against the
        template's own helmet width gives the scale — the rink is drawn in
        perspective, so a defender near the puck is half again the size of
        one near the goal, and comparing every row against one fixed size is
        what made the far rows score so low they never passed the gate. Its
        position gives the anchor: the template is *placed* over the
        candidate, head to head, and only searched within a few pixels of
        there, which is both more accurate and far cheaper than sweeping a
        window.
        """
        if gray.size == 0 or width <= 0:
            return 0.0
        frame_h, frame_w = gray.shape[:2]
        centre_x = left + width / 2
        best = 0.0

        for name in HELMET_TEMPLATES:
            profile = _profile(name)
            if profile is None or profile.helmet_w <= 0:
                continue
            scale = width / profile.helmet_w
            if not _MIN_TEMPLATE_SCALE <= scale <= _MAX_TEMPLATE_SCALE:
                continue

            full_h = int(profile.gray.shape[0] * scale)
            full_w = int(profile.gray.shape[1] * scale)
            if full_h < 8 or full_w < 8:
                continue

            # Where the sprite should sit if this candidate is its helmet.
            anchor_x = centre_x - profile.helmet_cx * scale
            anchor_y = top - profile.helmet_top * scale
            win_w = min(full_w + 2 * _MATCH_MARGIN, frame_w)
            win_h = min(full_h + 2 * _MATCH_MARGIN, frame_h)
            win_left = int(min(max(anchor_x - _MATCH_MARGIN, 0),
                               frame_w - win_w))
            win_top  = int(min(max(anchor_y - _MATCH_MARGIN, 0),
                               frame_h - win_h))
            window = gray[win_top:win_top + win_h, win_left:win_left + win_w]
            if window.size == 0:
                continue

            small_window = _shrink(window, _MATCH_SCALE)
            small_template = _shrink(profile.gray, scale * _MATCH_SCALE)
            if small_window is None or small_template is None:
                continue
            score, _corner = best_match(small_window, small_template)
            best = max(best, score)
        return best

    # ── Scanning ─────────────────────────────────────────────────────────

    def _red_mask(self, frame: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        low = cv2.inRange(hsv, (_HUE_LOW[0], self._sat_min, self._val_min),
                                (_HUE_LOW[1], 255, 255))
        high = cv2.inRange(hsv, (_HUE_HIGH[0], self._sat_min, self._val_min),
                                 (_HUE_HIGH[1], 255, 255))
        red = cv2.bitwise_or(low, high)

        if self._use_static:
            if self._static is None:
                self._learn_static(red)
            if self._static is not None and self._static.shape == red.shape:
                red[self._static] = 0
        return red

    def scan(self, frame: np.ndarray,
             expected: dict[int, list[float]] | None = None
             ) -> dict[int, list[LaneHit]]:
        """One entry per configured row: every defender in it, left to
        right. An empty list is a row with nobody in it, which is normal —
        a level shows some subset of the rows, not all of them.

        A list rather than one defender because a row can hold several
        (2026-08-09), of which at most one ever moves.

        `frame` is a BGR grab of the whole rink — Geometry.region — not a
        per-row grab. One capture and one colour conversion serve all rows.

        `expected` is where the model believes each row's defenders are, by
        row index. Candidates landing on one of those are held to a lower
        photo score — see _EXPECTED_RELIEF.
        """
        found, _candidates = self.scan_debug(frame, expected)
        return found

    def scan_debug(self, frame: np.ndarray,
                   expected: dict[int, list[float]] | None = None
                   ) -> tuple[dict[int, list[LaneHit]], list[LaneHit]]:
        """The same scan, plus every candidate it considered — accepted or
        not. What the debug snapshot draws.

        Components are found once over the union of the strips rather than
        once per strip, and each is then handed to a single row. Rows have
        to overlap on screen — the defenders themselves overlap, so a strip
        drawn tightly enough to avoid its neighbours would clip the helmets
        it is meant to hold (2026-08-09) — and searching each strip on its
        own turned that into two separate faults: one helmet reported as two
        defenders in two rows, and a helmet clipped by a strip edge losing
        both its area and its shape. Neither can happen to a component found
        whole and assigned once.
        """
        red  = self._red_mask(frame)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        candidates = self._candidates(self._band(red), gray, _MAX_SCORED)
        found: dict[int, list[LaneHit]] = {lane.index: []
                                           for lane in self._geom.lanes}
        for hit in candidates:
            hit.lane = self._nearest_lane(hit.y)
            if not hit.accepted and (self._was_expected(hit, expected)
                                     or self._is_steady(hit)):
                hit.accepted = True
                hit.relieved = True
        # Last, so that a jersey cannot be let back in by having sat still:
        # standing is exactly what a jersey does.
        self._drop_body_parts(candidates)
        for hit in candidates:
            if hit.accepted and hit.lane in found:
                found[hit.lane].append(hit)
        for hits in found.values():
            hits.sort(key=lambda hit: hit.x)
        self._recent.append(tuple(complex(hit.x, hit.y) for hit in candidates))
        return found, candidates

    def _drop_body_parts(self, candidates: list[LaneHit]):
        """Un-accept red that belongs to somebody already found.

        `_suppress_overlaps` handles fragments of a helmet, which sit *on*
        each other. This handles the rest of the sprite, which sits below —
        a chest, a glove, a sock — and overlaps nothing, so it survives as a
        defender of its own in whichever row its strip happens to reach.
        """
        lanes = {lane.index: lane for lane in self._geom.lanes}
        taken = [hit for hit in candidates if hit.accepted]
        for lower in taken:
            own = lanes.get(lower.lane)
            if (own is not None and not lower.relieved
                    and abs(lower.y - own.y) < _BODY_PART_OFF_LINE):
                # Standing on a row's line, and it got here on its own photo
                # score: a head, not a hip. A blob let in by a relief has no
                # such evidence, and a defender who never moves has a torso
                # that never moves either — which is how a stander in row 4
                # grew a copy of himself in row 5 a couple of seconds later
                # (2026-08-10).
                continue
            for upper in taken:
                if upper is lower or upper.match < lower.match:
                    continue
                lane = lanes.get(upper.lane)
                if lane is not None and self._inside_body(lower, upper, lane):
                    lower.accepted = False
                    break

    def _inside_body(self, lower: LaneHit, upper: LaneHit, lane) -> bool:
        left, top, width, height = self._geom.body_box(lane, upper.x, upper.y)
        return (lower.y > upper.y + _BODY_PART_DROP_PX
                and lower.y <= top + height
                and abs(lower.x - upper.x) <= width / _BODY_PART_WIDTH)

    def _is_steady(self, hit: LaneHit) -> bool:
        """Whether a helmet-shaped blob has been sitting right here for a
        while — see _STEADY_FRAMES."""
        if hit.match < _EXPECTED_FLOOR or len(self._recent) < _STEADY_FRAMES:
            return False
        here = complex(hit.x, hit.y)
        seen = sum(any(abs(spot - here) <= _STEADY_PX for spot in frame)
                   for frame in self._recent)
        return seen >= _STEADY_SHARE * len(self._recent)

    def _was_expected(self, hit: LaneHit,
                      expected: dict[int, list[float]] | None) -> bool:
        """Whether the model was already waiting for a helmet here.

        A defender the tracker is following is not asked to prove itself
        from scratch every frame: it has to be somewhere, it was here a
        moment ago, and the photo score of a real helmet swings far enough
        to fall through a fixed threshold at random.
        """
        if not expected or hit.match < max(_EXPECTED_FLOOR,
                                           self._match_min * _EXPECTED_RELIEF):
            return False
        return any(abs(hit.x - where) <= _EXPECTED_PX
                   for where in expected.get(hit.lane, ()))

    def _band(self, red: np.ndarray) -> np.ndarray:
        """`red` with everything outside every row's strip blanked out — the
        rink's own scoreboard, the boards and the player's own avatar are
        not defenders and should never become candidates."""
        keep = np.zeros(red.shape[0], bool)
        for lane in self._geom.lanes:
            top, bottom = self._geom.lane_rows(lane)
            if bottom - top >= _MIN_STRIP_H:
                keep[top:bottom] = True
        if keep.all():
            return red
        band = red.copy()
        band[~keep] = 0
        return band

    def _nearest_lane(self, screen_y: int) -> int:
        """The row a helmet belongs to: of the strips it falls inside, the
        one whose own centre line is closest. Overlapping strips make this a
        choice rather than a lookup, and the centre is what a row was
        calibrated on."""
        best, best_distance = NO_LANE, None
        for lane in self._geom.lanes:
            if not lane.top <= screen_y <= lane.bottom:
                continue
            distance = abs(screen_y - lane.y)
            if best_distance is None or distance < best_distance:
                best, best_distance = lane.index, distance
        return best

    def scan_all(self, frame: np.ndarray) -> list[LaneHit]:
        """Every accepted helmet anywhere in the rink, ignoring rows — what
        "Тест детекции" falls back to when no rows are calibrated yet, so
        the one button that could tell you the detector works at all still
        reports something when rows are what is misconfigured."""
        return [hit for hit in self.scan_all_debug(frame) if hit.accepted]

    def scan_all_debug(self, frame: np.ndarray) -> list[LaneHit]:
        red  = self._red_mask(frame)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return self._candidates(red, gray, _MAX_SCORED)

    def _candidates(self, mask: np.ndarray, gray: np.ndarray,
                    limit: int) -> list[LaneHit]:
        """Shape-plausible blobs in `mask`, largest first, each scored
        against the helmet photos. Only the first `limit` get scored, which
        is what keeps the whole scan inside a tick. Rows are not assigned
        here — see scan_debug."""
        count, _labels, stats, centroids = cv2.connectedComponentsWithStats(
            mask, 8)
        plausible = [i for i in range(1, count) if self._plausible(stats[i])]
        plausible.sort(key=lambda i: int(stats[i, cv2.CC_STAT_AREA]),
                       reverse=True)

        hits = []
        for i in plausible[:limit]:
            stat = stats[i]
            left   = int(stat[cv2.CC_STAT_LEFT])
            top    = int(stat[cv2.CC_STAT_TOP])
            width  = int(stat[cv2.CC_STAT_WIDTH])
            height = int(stat[cv2.CC_STAT_HEIGHT])
            score  = self._photo_score(gray, left, top, width)
            cx, cy = centroids[i]
            hits.append(LaneHit(
                lane=NO_LANE,
                x=int(round(self._geom.to_screen_x(cx))),
                y=int(round(self._geom.to_screen_y(cy))),
                area=int(stat[cv2.CC_STAT_AREA]),
                match=score,
                accepted=score >= self._match_min,
                box=(int(self._geom.to_screen_x(left)),
                     int(self._geom.to_screen_y(top)), width, height),
                frame_box=(left, top, width, height),
            ))
        return _suppress_overlaps(hits)
