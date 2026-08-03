# modules/janitor/timer_read.py
"""Reading the countdown clock out of a janitor_nextshift.png-shaped crop.

A straight copy of Садовник's own timer_read.py — the badge is the same
graphic in both locations, so the same calibration (6px pitch, masked
digit templates, assumed colon positions) reads it the same way. See that
module's docstring for the reasoning.
"""
from __future__ import annotations

import cv2
import numpy as np

from app.core.template_match import load_template

_CELL_X0     = 6
_CELL_PITCH  = 6
_ROW_Y0      = 18
_ROW_Y1      = 30
_X_TOLERANCE = 2

_SCALE = 4

_MASK_LEVEL = 160

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
