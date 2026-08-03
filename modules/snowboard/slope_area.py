# modules/snowboard/slope_area.py
"""The slope's own rectangle — calibrated by hand via the "Область спуска"
button (app/ui/calibration_overlay.py), the same way Уборщик's park and
Садовник's garden were worked out.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Area:
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


# Calibrated once by hand (2026-08-03) via "Область спуска": 779×666 at
# (888, 378), centre (1277, 710).
FIXED_AREA = Area(left=888, top=378, width=779, height=666)
