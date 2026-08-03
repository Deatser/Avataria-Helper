# modules/gardener/timer_read.py
"""Reading the countdown clock out of a gardener_nextshift.png-shaped crop.

The font is small and monospaced with a tight, 6px pitch — tighter than the
glyph templates themselves, so plain matchTemplate at native resolution lets
neighbouring characters bleed into each other's score. Upscaling both the
crop and the templates before matching (bicubic, _SCALE times) helps, but
the templates also carry the badge's own background colour as most of their
area — plain correlation was matching that shared background as much as the
digit itself, which is what let "0" win almost every cell regardless of the
real character. Masking each template down to just its bright glyph pixels
before matching (cv2.matchTemplate's mask=, TM_CCORR_NORMED) fixed that:
every digit in both calibration crops then matched at >0.99 confidence.

The colon is the one glyph too thin to mask reliably — its own match stayed
weak and ambiguous even after masking. The game's clock is always shown as
"H:MM:SS" though, so rather than classify it, the two colon positions are
simply assumed.
"""
from __future__ import annotations

import cv2
import numpy as np

from app.core.template_match import load_template

# Calibrated once by hand (2026-08-03) against real crops showing "0:49:41"
# and "0:19:46": characters sit at a fixed 6px pitch, the first one starting
# at x=6; the row itself sits roughly y=18-30. _X_TOLERANCE absorbs the
# crop's own left/right jitter without letting a cell drift far enough to
# read its neighbour instead.
_CELL_X0     = 6
_CELL_PITCH  = 6
_ROW_Y0      = 18
_ROW_Y1      = 30
_X_TOLERANCE = 2

_SCALE = 4   # upscale factor before matching — see module docstring

# Above this a template pixel is glyph, not the badge's own background —
# see module docstring. Calibrated against the same two crops: with this
# cut every real digit matched at >0.99, its nearest wrong guess well below.
_MASK_LEVEL = 160

# "H:MM:SS" — always 7 characters, confirmed against the two calibration
# crops; the colon sits at these two positions, assumed rather than matched.
_LENGTH = 7
_COLON_POSITIONS = {1, 4}


def read_timer(crop_bgr: np.ndarray) -> str | None:
    """The clock's own text, read left to right, or None if the crop is
    too small to hold it at all."""
    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    big = cv2.resize(gray, None, fx=_SCALE, fy=_SCALE,
                     interpolation=cv2.INTER_CUBIC)

    results: dict[str, np.ndarray] = {}
    for digit in range(10):
        template = load_template(f"{digit}.png")
        if template is None:
            continue
        mask = (template > _MASK_LEVEL).astype(np.uint8) * 255
        template_big = cv2.resize(template, None, fx=_SCALE, fy=_SCALE,
                                  interpolation=cv2.INTER_CUBIC)
        mask_big = cv2.resize(mask, None, fx=_SCALE, fy=_SCALE,
                              interpolation=cv2.INTER_NEAREST)
        if template_big.shape[0] > big.shape[0] \
                or template_big.shape[1] > big.shape[1]:
            continue
        results[str(digit)] = cv2.matchTemplate(
            big, template_big, cv2.TM_CCORR_NORMED, mask=mask_big)

    if not results:
        return None

    y0, y1 = _ROW_Y0 * _SCALE, _ROW_Y1 * _SCALE
    chars = []
    for i in range(_LENGTH):
        if i in _COLON_POSITIONS:
            chars.append(":")
            continue
        x = (_CELL_X0 + i * _CELL_PITCH) * _SCALE
        tol = _X_TOLERANCE * _SCALE
        char, score = _best_digit(results, x, tol, y0, y1)
        if not char:
            return None
        chars.append(char)

    return "".join(chars)


def _best_digit(results: dict[str, np.ndarray], x: int, tol: int,
                y0: int, y1: int) -> tuple[str, float]:
    best_char, best_score = "", -1.0
    for char, result in results.items():
        yy0, yy1 = max(0, y0), min(result.shape[0], y1)
        xx0, xx1 = max(0, x - tol), min(result.shape[1], x + tol + 1)
        if yy0 >= yy1 or xx0 >= xx1:
            continue
        score = float(result[yy0:yy1, xx0:xx1].max())
        if score > best_score:
            best_char, best_score = char, score
    return best_char, best_score
