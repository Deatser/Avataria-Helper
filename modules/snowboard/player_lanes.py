# modules/snowboard/player_lanes.py
"""The box the snowboarder always sits in — see player_area.py — split
into seven lane slices side by side, the same colours as track_rows.py's
seven rows so the two grids read as one continuous set.

The box's own long edges (top-left→top-right, bottom-left→bottom-right)
are the lane-spanning direction — the same role top_left→top_right plays
in track_rows.py — so cutting each into seven equal steps and pairing them
up gives seven lane quads directly, no extrapolation needed the way
track_rows.py's repeated depth-shift required.
"""
from __future__ import annotations

from modules.snowboard.player_area import FIXED_PLAYER_QUAD
from modules.snowboard.track_rows import ROW_COLOURS

LANE_COUNT = 7


def _lerp(a: tuple[int, int], b: tuple[int, int], t: float) -> tuple[int, int]:
    return (round(a[0] + (b[0] - a[0]) * t), round(a[1] + (b[1] - a[1]) * t))


def _boundary(i: int) -> tuple[tuple[int, int], tuple[int, int]]:
    t = i / LANE_COUNT
    top = _lerp(FIXED_PLAYER_QUAD.top_left, FIXED_PLAYER_QUAD.top_right, t)
    bottom = _lerp(FIXED_PLAYER_QUAD.bottom_left, FIXED_PLAYER_QUAD.bottom_right, t)
    return top, bottom


# Eight boundary points along each long edge — enough for seven lane quads
# between them.
_BOUNDARIES = [_boundary(i) for i in range(LANE_COUNT + 1)]

# Seven independent quads, one per lane: (near-top, far-top, far-bottom,
# near-bottom).
LANE_QUADS = [
    (_BOUNDARIES[i][0], _BOUNDARIES[i + 1][0],
     _BOUNDARIES[i + 1][1], _BOUNDARIES[i][1])
    for i in range(LANE_COUNT)
]

# Same seven pastel shades track_rows.py uses, in the same order — index
# for index, a lane here reads as the same colour as its row further down
# the slope.
LANE_COLOURS = ROW_COLOURS


def _bbox(quad: tuple[tuple[int, int], ...]) -> dict:
    xs = [point[0] for point in quad]
    ys = [point[1] for point in quad]
    left, top = min(xs), min(ys)
    return {"left": left, "top": top,
           "width": max(xs) - left, "height": max(ys) - top}


# One grab region per lane — each quad's own bounding box, in the mss
# shape grab_window wants. See track_rows.py's own ROW_REGIONS for the
# same reasoning: a plain box rather than the quad's own slanted outline.
LANE_REGIONS = [_bbox(quad) for quad in LANE_QUADS]
