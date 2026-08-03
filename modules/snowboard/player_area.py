# modules/snowboard/player_area.py
"""Where the snowboarder itself always sits on screen — calibrated by hand
via the "Область по углам" button (app/ui/quad_calibration_overlay.py),
tilted to match the lanes' own \\-slant rather than the screen's own
horizontal/vertical, the way a plain rectangle could not.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Quad:
    top_left: tuple[int, int]
    top_right: tuple[int, int]
    bottom_right: tuple[int, int]
    bottom_left: tuple[int, int]

    @property
    def centre(self) -> tuple[int, int]:
        xs = (self.top_left[0], self.top_right[0],
              self.bottom_right[0], self.bottom_left[0])
        ys = (self.top_left[1], self.top_right[1],
              self.bottom_right[1], self.bottom_left[1])
        return sum(xs) // 4, sum(ys) // 4


# Calibrated once by hand (2026-08-03) via "Область по углам": tilted
# parallel to the lanes' own \-slant, the box the snowboarder always sits
# inside of, whichever lane it is actually in.
FIXED_PLAYER_QUAD = Quad(
    top_left=(915, 815),
    top_right=(1099, 679),
    bottom_right=(1203, 735),
    bottom_left=(1028, 873),
)
