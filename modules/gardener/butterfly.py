# modules/gardener/butterfly.py
"""Following butterflies, which will not hold still to be found.

Every other kind of litter is where it was a second ago, so one screenshot
answers everything. A butterfly is not: by the time a dot is drawn where it
was, it has moved. So it is tracked rather than found — each one is followed
between frames, its speed is measured, and its position between measurements
is worked out from that rather than guessed at.

It also changes shape. There are two drawings of it, it animates between
them, and it banks as it flies, so any single picture matches only now and
then — which is why one would be caught for a second and then lost. Every
drawing, at every 45 degrees, is looked for.

Three things keep that affordable:

  * a butterfly already being followed is looked for only in a small box
    around where it should be, which costs a fraction of a millisecond
    instead of the ~55 ms a full screen takes;
  * in that box the picture that matched it last time is tried first — it
    banks gradually, so the same one usually still fits, and stopping there
    is the difference between one match and sixteen;
  * the whole screen is swept at half size, a quarter of the work, and only
    about once a second, to pick up ones that have just appeared.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import cv2
from PySide6.QtCore import QObject, QTimer, Signal

from app.core.capture import ScreenCapture
from app.core.template_match import (find_all, load_template,
                                     primary_monitor_region)
from modules.gardener.rotations import variants

TICK_MS      = 70     # how often a followed butterfly is re-measured
FULL_SCAN_MS = 1200   # how often the whole screen is swept for new ones
ROI_PAD      = 70     # half-size of the box searched around a prediction
SWEEP_SCALE  = 0.5    # the sweep runs at half size; a quarter of the work

# A butterfly moves, but not arbitrarily far between two frames 70 ms apart.
# Anything further away is a different butterfly, not this one having jumped.
MATCH_RADIUS = 110.0

# Velocity is smoothed rather than taken raw: a single frame's measurement
# carries the matcher's own jitter, and a dot that twitches is worse than one
# that lags slightly.
SMOOTH = 0.55

# How many measurements in a row a butterfly may go unseen before it is
# dropped. It passes behind scenery constantly, so a couple of misses mean
# nothing; a dozen mean it has gone.
MAX_MISSED = 8

# A hard ceiling on how many are followed at once. The picture is tiny —
# 24x26 — so a threshold anywhere near the noise floor finds hundreds of them
# on a screen that has none, and each one costs a grab per tick. The
# best-scoring survive; the rest are noise by definition.
MAX_TRACKS = 12

# One per number, so butterfly №1 is the same colour on screen as in the
# readout, and stays that colour for as long as it is followed.
TRACK_COLOURS = ["#00ffa3", "#ffd166", "#7cd4ff", "#ff8fab", "#c3f584",
                 "#ff9f45", "#a78bfa", "#5ee7c4", "#f7768e", "#8bd450",
                 "#e0aaff", "#4cc9f0"]


def colour_for(number: int) -> str:
    return TRACK_COLOURS[(number - 1) % len(TRACK_COLOURS)]


@dataclass
class Track:
    """One butterfly, as far as anyone can tell."""
    x: float
    y: float
    vx: float = 0.0      # pixels per second
    vy: float = 0.0
    score: float = 0.0
    missed: int = 0
    number: int = 0      # what it is called in the readout, and its colour
    picture: int = 0     # which drawing matched last; tried first next time
    # Where it was actually last seen, as opposed to where it is now
    # predicted to be. Speed has to be measured against the sighting: taken
    # against the prediction it is only the leftover error, and the estimate
    # settles at half the real speed — which is a dot that always lags.
    seen_x: float = 0.0
    seen_y: float = 0.0

    @property
    def colour(self) -> str:
        return colour_for(self.number)

    def predicted(self, dt: float) -> tuple[float, float]:
        return self.x + self.vx * dt, self.y + self.vy * dt


class Tracks:
    """The bookkeeping, with no screen in it — this is the testable half."""

    def __init__(self):
        self.items: list[Track] = []
        self._next_number = 1

    def positions(self) -> list[tuple[int, int, int, float]]:
        """(number, x, y, score) for everything currently being followed."""
        return [(t.number, int(round(t.x)), int(round(t.y)), t.score)
                for t in self.items]

    def step(self, detections: list, dt: float):
        """Move every track forward by dt, then fold in what was just seen.

        A detection is (x, y, score) or (x, y, score, picture).
        """
        for track in self.items:
            track.seen_x, track.seen_y = track.x, track.y
            track.x, track.y = track.predicted(dt)

        free = list(detections)
        for track in self.items:
            match = self._nearest(track, free)
            if match is None:
                track.missed += 1
                continue
            free.remove(match)
            self._absorb(track, match, dt)

        self.items = [t for t in self.items if t.missed <= MAX_MISSED]
        self.items += [self._new(detection) for detection in free]

        if len(self.items) > MAX_TRACKS:
            self.items.sort(key=lambda t: -t.score)
            del self.items[MAX_TRACKS:]

    # ── Internals ────────────────────────────────────────────────────────────

    def _new(self, detection) -> Track:
        x, y, score = detection[0], detection[1], detection[2]
        track = Track(x, y, score=score, seen_x=x, seen_y=y,
                      number=self._next_number,
                      picture=detection[3] if len(detection) > 3 else 0)
        self._next_number += 1
        return track

    def _nearest(self, track: Track, free: list):
        best, best_distance = None, MATCH_RADIUS
        for detection in free:
            distance = math.hypot(detection[0] - track.x, detection[1] - track.y)
            if distance < best_distance:
                best, best_distance = detection, distance
        return best

    def _absorb(self, track: Track, detection, dt: float):
        x, y, score = detection[0], detection[1], detection[2]
        if dt > 0:
            measured_vx = (x - track.seen_x) / dt
            measured_vy = (y - track.seen_y) / dt
            track.vx = track.vx * SMOOTH + measured_vx * (1 - SMOOTH)
            track.vy = track.vy * SMOOTH + measured_vy * (1 - SMOOTH)
        track.x, track.y, track.score, track.missed = x, y, score, 0
        if len(detection) > 3:
            track.picture = detection[3]


def dedupe(found: list, radius: float) -> list:
    """Drop detections sitting on top of a better one.

    One butterfly matches several of the turned pictures at once, and the
    search boxes overlap; left in, every duplicate would become a track of
    its own and the count would run away.
    """
    kept = []
    for detection in sorted(found, key=lambda hit: -hit[2]):
        if any(math.hypot(detection[0] - k[0], detection[1] - k[1]) < radius
               for k in kept):
            continue
        kept.append(detection)
    return kept


class ButterflyTracker(QObject):
    """Keeps the tracks fed from the screen and says where they are."""

    updated = Signal(list)   # [(number, x, y, score), ...]

    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self._kind   = kind
        self._tracks = Tracks()
        self._last   = 0.0
        self._swept  = 0.0
        self._pictures: list = []   # every drawing at every angle, full size
        self._small: list    = []   # the same, halved, for the sweep
        self._region = None

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    @property
    def pictures(self) -> int:
        return len(self._pictures)

    def start(self) -> bool:
        self._pictures = []
        for filename in self._kind.filenames:
            for name in variants(filename):
                picture = load_template(name)
                if picture is not None:
                    self._pictures.append(picture)
        if not self._pictures:
            return False
        self._small = [cv2.resize(p, None, fx=SWEEP_SCALE, fy=SWEEP_SCALE,
                                  interpolation=cv2.INTER_AREA)
                       for p in self._pictures]

        self._region = primary_monitor_region()
        self._tracks = Tracks()
        self._last = self._swept = 0.0   # first tick sweeps everything
        self._timer.start()
        return True

    def stop(self):
        self._timer.stop()
        self._tracks = Tracks()
        ScreenCapture.release()

    # ── The loop ─────────────────────────────────────────────────────────────

    def _tick(self):
        now = time.monotonic()
        dt  = min(now - self._last, 0.5) if self._last else 0.0
        self._last = now

        try:
            if not self._tracks.items or now - self._swept > FULL_SCAN_MS / 1000:
                self._swept = now
                detections = self._sweep()
            else:
                detections = self._follow(dt)
        except Exception:
            return   # a failed grab is not worth losing the tracks over

        self._tracks.step(detections, dt)
        self.updated.emit(self._tracks.positions())

    def _sweep(self) -> list:
        """The whole screen at half size, for ones nobody is following yet."""
        capture = ScreenCapture.get()
        gray  = cv2.cvtColor(capture.grab(self._region), cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, None, fx=SWEEP_SCALE, fy=SWEEP_SCALE,
                           interpolation=cv2.INTER_AREA)
        found = []
        for index, picture in enumerate(self._small):
            h, w = picture.shape[:2]
            found += [((x + w / 2) / SWEEP_SCALE, (y + h / 2) / SWEEP_SCALE,
                       score, index)
                      for score, x, y in find_all(small, picture,
                                                  self._kind.threshold,
                                                  limit=MAX_TRACKS)]
        return dedupe(found, self._span() * 0.6)

    def _follow(self, dt: float) -> list:
        """Only the boxes where the tracks say the butterflies should be."""
        capture = ScreenCapture.get()
        found = []
        for track in self._tracks.items:
            box = self._box(*track.predicted(dt))
            if box is None:
                continue
            gray = cv2.cvtColor(capture.grab(box), cv2.COLOR_BGR2GRAY)
            hit  = self._best_in(gray, track.picture)
            if hit is None:
                continue
            score, x, y, index = hit
            h, w = self._pictures[index].shape[:2]
            found.append((box["left"] + x + w / 2, box["top"] + y + h / 2,
                          score, index))
        return dedupe(found, self._span() * 0.6)

    def _best_in(self, gray, first: int):
        """Best match in a box, starting with the drawing that fitted last."""
        order = [first] + [i for i in range(len(self._pictures)) if i != first]
        for index in order:
            picture = self._pictures[index]
            if (gray.shape[0] < picture.shape[0]
                    or gray.shape[1] < picture.shape[1]):
                continue
            hits = find_all(gray, picture, self._kind.threshold, limit=1)
            if hits:
                score, x, y = hits[0]
                return score, x, y, index
        return None

    def _span(self) -> float:
        return float(max(self._pictures[0].shape[:2]))

    def _box(self, x: float, y: float) -> dict | None:
        """A search box around a prediction, clipped to the screen."""
        screen = self._region
        h, w = self._pictures[0].shape[:2]
        left   = max(screen["left"], int(x - w / 2 - ROI_PAD))
        top    = max(screen["top"], int(y - h / 2 - ROI_PAD))
        right  = min(screen["left"] + screen["width"], int(x + w / 2 + ROI_PAD))
        bottom = min(screen["top"] + screen["height"], int(y + h / 2 + ROI_PAD))
        if right - left <= w or bottom - top <= h:
            return None
        return {"left": left, "top": top,
                "width": right - left, "height": bottom - top}
