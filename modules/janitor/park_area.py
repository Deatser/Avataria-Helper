# modules/janitor/park_area.py
"""The park's own rectangle — calibrated by hand via the "Область парка"
button (app/ui/calibration_overlay.py), the same way Садовник's garden
rectangle was worked out by eye before it became FIXED_AREA.
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


# Calibrated once by hand (2026-08-03) via "Область парка": 1924×1085 at
# (319, 169), centre (1280, 711).
FIXED_AREA = Area(left=319, top=169, width=1924, height=1085)
