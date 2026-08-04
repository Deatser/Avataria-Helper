# modules/hockey/rink_area.py
"""The rink's own rectangle — calibrated by hand via "Область экрана"
(app/ui/calibration_overlay.py), the same way every other module's own
play area was worked out.

Everything else about the rink — the shooter's own spot, the goal mouth —
is defined as a fraction of this rectangle rather than its own separate
calibration, since both sit at a fixed place within it. Rough guesses off
the reference screenshot for now (see SHOOTER_*/GOAL_*_FRAC below) —
correct them once real coordinates are checked against the live game.
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

    def point(self, x_frac: float, y_frac: float) -> tuple[int, int]:
        """A point inside the area, given as a fraction of its own width
        and height — screen coordinates."""
        return (round(self.left + self.width * x_frac),
                round(self.top + self.height * y_frac))


# Calibrated once by hand (2026-08-03) via "Область экрана": 780×666 at
# (891, 385), centre (1280, 717).
FIXED_AREA = Area(left=891, top=385, width=780, height=666)

# Where the shooter's own puck starts — bottom-centre of the rink, a little
# short of the very edge since the reference screenshot shows the stick and
# puck sitting a short way above the shooter's own feet.
SHOOTER_X_FRAC = 0.50
SHOOTER_Y_FRAC = 0.90

# The goal mouth — a line near the top of the rink, not a point: the shot
# has to land somewhere between GOAL_LEFT_FRAC and GOAL_RIGHT_FRAC at
# GOAL_Y_FRAC for it to count.
GOAL_Y_FRAC     = 0.10
GOAL_LEFT_FRAC  = 0.32
GOAL_RIGHT_FRAC = 0.68
