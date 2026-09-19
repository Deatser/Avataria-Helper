# modules/hockey/debug_frame.py
"""Save the exact frame a scan judged, with a box round each helmet it
found — and nothing else on it.

A live overlay cannot answer "did it find them in the right place": by the
time the boxes are on screen the defenders have already moved on, so what is
being compared is this instant's box against that instant's player. Drawing
onto the captured frame itself removes the question — the picture and the
reading are the same instant by construction, however long the file sits
around before anyone opens it.

Deliberately bare. An earlier version also drew the row strips, the goal
line, the boards, the puck and a caption on every box, and the result was a
picture of the calibration rather than a picture of the detection — the one
thing it existed to show got lost in it. The overlays already draw the
geometry, live and on demand; this file answers one question only.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from app.core.paths import data_path

# The same place ConfigManager.CONFIG_FILE goes: the working directory when
# run from source, so a test that has chdir'd into a tmp directory leaves its
# snapshots there rather than in the repo, and %APPDATA% once built.
_OUT_DIR = data_path("debug_frames")

_HELMET = (0, 0, 255)     # BGR — what the detector actually found
_BODY   = (0, 0, 0)       # what a shot has to get past
_WIDTH  = 2


def annotate(frame: np.ndarray, geom, hits: list) -> np.ndarray:
    """A copy of `frame` with two boxes per defender: red round the helmet
    that was detected, black round the whole defender that blocks a shot.

    Both are worth seeing and they are not the same rectangle — the helmet
    says whether detection works, the outline says what the planner will be
    dodging, and a calibration that got one right and the other wrong looks
    fine until a puck goes through a shoulder.
    """
    canvas = frame.copy()
    lanes = {lane.index: lane for lane in geom.lanes}
    for hit in hits:
        left, top, width, height = hit.frame_box
        lane = lanes.get(hit.lane)
        if lane is not None:
            bx, by, bw, bh = geom.body_box(lane, hit.x, hit.y)
            bx, by = bx - geom.left, by - geom.top
            cv2.rectangle(canvas, (bx, by), (bx + bw, by + bh),
                          _BODY, _WIDTH)
        cv2.rectangle(canvas, (left, top), (left + width, top + height),
                      _HELMET, _WIDTH)
    return canvas


def save(frame: np.ndarray, geom, hits: list,
         out_dir: Path | None = None) -> Path:
    """Write the annotated frame and hand back where it went."""
    return _write(annotate(frame, geom, hits), "hockey", out_dir)


def burst_dir(root: Path | None = None) -> Path:
    """A fresh folder for one run of frames.

    A single snapshot answers "did it find them", and a run of them answers
    the question that actually comes up: whether the same defender is found
    in every frame or only in some. Its own folder, because that is a
    hundred files and they are only worth anything together.
    """
    base = Path(root) if root is not None else _OUT_DIR
    directory = base / f"detect_{datetime.now():%Y%m%d_%H%M%S}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_numbered(frame: np.ndarray, geom, hits: list, directory: Path,
                  index: int) -> Path:
    """One frame of a burst, twice: annotated to look at, and untouched in
    `raw/` to re-judge.

    The boxes are drawn in pure red, which is the colour the detector reads,
    so an annotated frame cannot be scanned again — the boxes merge with the
    helmets they surround and every measurement taken off them is wrong.
    Whatever the run turns out to be about, the answer is in pixels nobody
    has drawn on (2026-08-10).
    """
    directory = Path(directory)
    raw_dir = directory / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(raw_dir / f"{index:04d}.png"), frame)
    path = directory / f"{index:04d}.png"
    cv2.imwrite(str(path), annotate(frame, geom, hits))
    return path


# ── The puck crossing a row ──────────────────────────────────────────────

_ROW_BAND   = (255, 200, 60)    # BGR
_ENTRY_EDGE = (60, 255, 120)
_PUCK       = (0, 220, 255)
_FONT       = cv2.FONT_HERSHEY_SIMPLEX
_FONT_SCALE = 0.5


def annotate_crossing(frame: np.ndarray, geom, lane, puck: tuple[float, float],
                      t: float, aim: float) -> np.ndarray:
    """The frame in which the puck reached one row: the band that row's
    defenders occupy, the edge it was entering, and the puck itself.

    Taken at the moment of entry rather than anywhere inside the crossing,
    because entry means the same thing for every row — the puck exactly on
    the band's lower edge — so every picture should look alike and any that
    does not is a real error. The middle of the crossing was tried and is
    not comparable between rows: for a defender reaching above the goal the
    interval is clipped there, so its midpoint sits somewhere different in
    each band (2026-08-10).

    The caption is ASCII because cv2 cannot draw Cyrillic; it would silently
    render "???".
    """
    canvas = frame.copy()
    height, width = canvas.shape[:2]

    _left, top, _w, band_h = geom.body_box(lane, lane.wall_left, lane.y)
    band_top = max(0, top - geom.top)
    band_bottom = min(height - 1, band_top + band_h)
    cv2.rectangle(canvas, (0, band_top), (width - 1, band_bottom),
                  _ROW_BAND, 2)
    # The edge the timing is about: the puck should be sitting on it.
    cv2.line(canvas, (0, band_bottom), (width - 1, band_bottom),
             _ENTRY_EDGE, 2)

    px = int(round(puck[0] - geom.left))
    py = int(round(puck[1] - geom.top))
    cv2.circle(canvas, (px, py), 14, _PUCK, 2)
    cv2.line(canvas, (px - 20, py), (px + 20, py), _PUCK, 1)
    cv2.line(canvas, (px, py - 20), (px, py + 20), _PUCK, 1)

    caption = f"row {lane.index + 1}  enter t={t:.2f}s  aim={aim:+.2f}"
    (tw, th), _base = cv2.getTextSize(caption, _FONT, _FONT_SCALE, 1)
    cv2.rectangle(canvas, (2, 2), (8 + tw, 10 + th), (20, 20, 20), -1)
    cv2.putText(canvas, caption, (5, 6 + th), _FONT, _FONT_SCALE,
                (240, 240, 240), 1, cv2.LINE_AA)
    return canvas


def save_crossing(frame: np.ndarray, geom, lane, puck: tuple[float, float],
                  t: float, aim: float, out_dir: Path | None = None) -> Path:
    return _write(annotate_crossing(frame, geom, lane, puck, t, aim),
                  f"puck_row{lane.index + 1}", out_dir)


# ── The whole flight in one picture ──────────────────────────────────────

_PATH      = (0, 220, 255)
_CANDIDATE = (150, 150, 150)


def annotate_flight(frame: np.ndarray, geom, track, aim: float,
                    candidates: list | None = None) -> np.ndarray:
    """The whole path the tracker settled on, over every blob it weighed up.

    One picture answers the question the per-row shots cannot: not "was the
    puck here at this moment" but "did it follow the puck at all". A path
    that wanders sideways or stops halfway is obvious at a glance and
    nowhere near obvious in a list of timings — and the grey dots say which
    kind of failure it was. None of them up near the goal means the puck was
    never seen there; a trail of them the bright line ignored means the
    chain is at fault.
    """
    canvas = frame.copy()
    for x, y in (candidates or []):
        cv2.circle(canvas, (int(round(x - geom.left)), int(round(y - geom.top))),
                   3, _CANDIDATE, 1)
    points = [(int(round(s.x - geom.left)), int(round(s.y - geom.top)))
              for s in track.samples]
    for before, after in zip(points, points[1:]):
        cv2.line(canvas, before, after, _PATH, 2)
    for point in points:
        cv2.circle(canvas, point, 4, _PATH, -1)

    travel = track.travel_s or 0.0
    caption = (f"aim={aim:+.2f}  {travel:.2f}s  {len(points)} points")
    (tw, th), _base = cv2.getTextSize(caption, _FONT, _FONT_SCALE, 1)
    cv2.rectangle(canvas, (2, 2), (8 + tw, 10 + th), (20, 20, 20), -1)
    cv2.putText(canvas, caption, (5, 6 + th), _FONT, _FONT_SCALE,
                (240, 240, 240), 1, cv2.LINE_AA)
    return canvas


def save_flight(frame: np.ndarray, geom, track, aim: float,
                candidates: list | None = None,
                out_dir: Path | None = None) -> Path:
    return _write(annotate_flight(frame, geom, track, aim, candidates),
                  "puck_path", out_dir)


def _write(canvas: np.ndarray, stem: str, out_dir: Path | None) -> Path:
    directory = Path(out_dir) if out_dir is not None else _OUT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stem}_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
    cv2.imwrite(str(path), canvas)
    return path.resolve()
