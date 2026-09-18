# modules/gardener/garden_area.py
"""Finding the garden itself on the screen.

Every other template here is a small object; this one is the whole view —
a thousand pixels across. Matching it at full size costs seconds, and there
is no need: a region a thousand pixels wide can be located to within a few
pixels from a quarter-size copy, at a sixteenth of the work.

The score will never be high. The picture was taken of one garden at one
moment, and by the time it is searched for the character has moved, litter
has gone and flowers have animated. What matters is not the score but where
the peak is: that is the corner of the garden view, and knowing it means the
matcher can be pointed at the garden instead of the whole screen.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2

from app.core.capture import grab_screen_region
from app.core.template_match import (load_template, game_region)

GARDEN_TEMPLATE = "gardener_11.png"

# A quarter size. Below this the picture loses the structure that makes the
# match mean anything; above it there is no gain worth the time.
SCALE = 0.25


@dataclass
class Area:
    score: float
    left: int
    top: int
    width: int
    height: int

    @property
    def centre(self) -> tuple[int, int]:
        return self.left + self.width // 2, self.top + self.height // 2

    @property
    def region(self) -> dict:
        """The rectangle in the mss shape every grab call takes."""
        return {"left": self.left, "top": self.top,
                "width": self.width, "height": self.height}


# Calibrated once by hand (2026-08-03) by dragging the area overlay onto
# the real garden and reading its position back. locate() kept landing the
# same fixed distance off — a drift consistent enough that a known-good
# rectangle beats searching for one that keeps ending up in the same wrong
# place. GARDEN_TEMPLATE and locate() are left in place but unused by the
# gardener window now.
FIXED_AREA = Area(score=1.0, left=780, top=313, width=1000, height=797)


def locate(filename: str = GARDEN_TEMPLATE, screen_gray=None,
           scale: float = SCALE) -> Area | None:
    """Where the picture sits on screen, or None if it cannot be read."""
    template = load_template(filename)
    if template is None:
        return None

    region = game_region()
    if screen_gray is None:
        screen_gray = cv2.cvtColor(grab_screen_region(region),
                                   cv2.COLOR_BGR2GRAY)
        origin = (region["left"], region["top"])
    else:
        origin = (0, 0)

    small_screen = _shrink(screen_gray, scale)
    small_shape  = _shrink(template, scale)
    if (small_screen.shape[0] < small_shape.shape[0]
            or small_screen.shape[1] < small_shape.shape[1]):
        return None

    result = cv2.matchTemplate(small_screen, small_shape, cv2.TM_CCOEFF_NORMED)
    _min_v, score, _min_l, loc = cv2.minMaxLoc(result)
    h, w = template.shape[:2]
    return Area(float(score),
                origin[0] + int(loc[0] / scale), origin[1] + int(loc[1] / scale),
                w, h)


def _shrink(image, scale: float):
    return cv2.resize(image, None, fx=scale, fy=scale,
                      interpolation=cv2.INTER_AREA)
