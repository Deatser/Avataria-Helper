# modules/snowboard/track_rows.py
"""The lane grid's own rows — a reference line spanning all seven lanes
at once, repeated down the slope by its own depth shift, and filled in
between consecutive repeats to give each of the seven rows its own
independent quad.

Calibrated by hand (2026-08-03) via "Область по углам": top-left/top-right
is the line itself (perpendicular to the direction of travel); the two
short edges (top-left→bottom-left and top-right→bottom-right) are what one
row's own depth step looks like, averaged into a single shift vector.
Seven filled rows need eight boundary lines, not seven — the given line is
the first boundary, and it is walked forward seven more times, not six.
"""
from __future__ import annotations

ROW_COUNT = 7

TOP_LEFT     = (1493, 910)
TOP_RIGHT    = (1632, 977)
BOTTOM_LEFT  = (1485, 935)
BOTTOM_RIGHT = (1620, 1006)

# One row's depth step, averaged from both short edges — (-10, 27). Its
# own x component is not zero, so successive rows lean slightly left as
# they step down the slope — see the sort below for why that matters.
_DEPTH_SHIFT = (
    ((BOTTOM_LEFT[0] - TOP_LEFT[0]) + (BOTTOM_RIGHT[0] - TOP_RIGHT[0])) / 2,
    ((BOTTOM_LEFT[1] - TOP_LEFT[1]) + (BOTTOM_RIGHT[1] - TOP_RIGHT[1])) / 2,
)

# Stretched (2026-08-03: "нижние 7 полосок очень короткие... чтобы они
# тянулись ближе к верхним 7 полоскам", then corrected the same day —
# scaling _DEPTH_SHIFT stretched the gap BETWEEN rows, which reads as
# making each row thicker/wider, not longer). What "short" actually meant
# is each row line's own span, left endpoint to right endpoint — the
# seven lanes above it spread across a much wider stretch of screen than
# this calibrated line does. row_line below stretches each line
# lengthwise, about its own midpoint, so its own depth position and the
# gap to its neighbours are untouched.
_LENGTH_SCALE = 1.8

# A pastel rainbow, one shade per lane — index 0 (red) is always the
# leftmost lane, index 6 (purple) the rightmost, in both this grid and
# player_lanes.py's own.
ROW_COLOURS = [
    "#FFB3BA", "#FFDFBA", "#FFFFBA", "#BAFFC9",
    "#BAE1FF", "#C9BAFF", "#C9A0FF",
]


def row_line(row: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """The left/right endpoints of a boundary line, walked `row` steps
    down the slope by _DEPTH_SHIFT — row 0 sits on the calibrated line
    itself, before _LENGTH_SCALE stretches it outward about its own
    midpoint (leaving that midpoint, and so the row's own depth, exactly
    where it was calibrated)."""
    dx, dy = _DEPTH_SHIFT[0] * row, _DEPTH_SHIFT[1] * row
    left  = (TOP_LEFT[0]  + dx, TOP_LEFT[1]  + dy)
    right = (TOP_RIGHT[0] + dx, TOP_RIGHT[1] + dy)
    mid_x, mid_y = (left[0] + right[0]) / 2, (left[1] + right[1]) / 2
    left  = (round(mid_x + (left[0]  - mid_x) * _LENGTH_SCALE),
             round(mid_y + (left[1]  - mid_y) * _LENGTH_SCALE))
    right = (round(mid_x + (right[0] - mid_x) * _LENGTH_SCALE),
             round(mid_y + (right[1] - mid_y) * _LENGTH_SCALE))
    return left, right


# Eight boundary lines — enough for seven filled rows between them. Starts
# a step early (-1), not at the calibrated line itself: the whole grid
# read one row too far down and left, and shifting the start back by one
# _DEPTH_SHIFT is what moves it up and right to sit where it should.
ROW_LINES = [row_line(i) for i in range(-1, ROW_COUNT)]

# Seven quads, each between two consecutive boundary lines, built in the
# order they were walked down the slope — near-to-far, not left-to-right.
_QUADS_BY_DEPTH = [
    (ROW_LINES[i][0], ROW_LINES[i][1], ROW_LINES[i + 1][1], ROW_LINES[i + 1][0])
    for i in range(ROW_COUNT)
]


def _avg_x(quad: tuple[tuple[int, int], ...]) -> float:
    return sum(point[0] for point in quad) / len(quad)


# Re-sorted left to right on screen, not by how they were built — because
# _DEPTH_SHIFT leans left as well as down, the quad built first is not the
# leftmost one. Index i is now simply "the i-th lane from the left", the
# same thing index i means in player_lanes.py's own LANE_QUADS, so the two
# grids line up by plain index — no reversed colour order, no row-to-lane
# arithmetic anywhere else needed.
ROW_QUADS = sorted(_QUADS_BY_DEPTH, key=_avg_x)


def _bbox(quad: tuple[tuple[int, int], ...]) -> dict:
    xs = [point[0] for point in quad]
    ys = [point[1] for point in quad]
    left, top = min(xs), min(ys)
    return {"left": left, "top": top,
           "width": max(xs) - left, "height": max(ys) - top}


# One grab region per row — each quad's own bounding box, in the mss shape
# grab_window wants. A rectangle rather than the quad's own slanted
# outline: close enough for "did this row's own patch of screen change",
# and a plain box is what grab_window can actually crop.
ROW_REGIONS = [_bbox(quad) for quad in ROW_QUADS]
