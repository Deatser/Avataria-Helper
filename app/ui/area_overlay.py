# app/ui/area_overlay.py
"""A translucent rectangle over whatever region needs checking by eye —
a scan region, anything worth highlighting while a run is watched."""
from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QPainter

from app.ui.game_layer import GameLayer

_FILL   = QColor(255, 196, 0, 60)
_BORDER = QColor(255, 196, 0, 220)


class AreaOverlay(GameLayer):
    """Sits exactly over one rectangle, filled and outlined, until cleared.

    Click-through like every other layer here: it is only ever shown while
    a run keeps clicking the game underneath it, and must never be the
    thing that swallows one of those clicks.
    """

    def __init__(self, window_manager=None, reference=None):
        super().__init__(window_manager)
        self._reference = reference

    def show_area(self, rect: QRect):
        self.setGeometry(rect)
        if not self.isVisible():
            self.show()
            self.raise_()
        if self._reference is not None:
            self.adopt(self._reference)
        self.update()

    def clear(self):
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), _FILL)
        painter.setPen(_BORDER)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()
