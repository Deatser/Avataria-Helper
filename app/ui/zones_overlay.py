# app/ui/zones_overlay.py
"""Click-through overlay that draws a fixed set of coloured rectangles over
the game — purely a display, nothing is dragged or read back out of it.

Built for eyeballing detector calibration (AvaDancers' 4 top strips + 4
bottom safety-net zones) without having to guess coordinates from a config
file — see CalibrationOverlay for the interactive, single-box version this
is a display-only sibling of.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from app.ui.game_layer import GameLayer


class ZonesOverlay(GameLayer):
    """Sized to cover every zone handed to show_zones(); click-through so it
    never gets in the way of the game underneath."""

    def __init__(self, window_manager=None, reference=None):
        super().__init__(window_manager, click_through=True)
        self._reference = reference
        self._boxes: list[tuple[QRectF, QColor]] = []

    def show_zones(self, zones: list[tuple[QRect, QColor]]):
        """zones: (rect in screen coordinates, colour) pairs."""
        if not zones:
            return
        origin = self.game_rect()
        if origin is None:
            left   = min(r.left()   for r, _ in zones) - 50
            top    = min(r.top()    for r, _ in zones) - 50
            right  = max(r.right()  for r, _ in zones) + 50
            bottom = max(r.bottom() for r, _ in zones) + 50
            origin = QRect(left, top, right - left, bottom - top)
        self.setGeometry(origin)
        ox, oy = origin.left(), origin.top()
        self._boxes = [
            (QRectF(r.left() - ox, r.top() - oy, r.width(), r.height()), colour)
            for r, colour in zones
        ]
        if not self.isVisible():
            self.show()
            self.raise_()
        if self._reference is not None:
            self.adopt(self._reference)
        self.update()

    def clear(self):
        self._boxes = []
        self.hide()

    def paintEvent(self, event):
        if not self._boxes:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        for rect, colour in self._boxes:
            fill = QColor(colour)
            fill.setAlpha(55)
            border = QColor(colour)
            border.setAlpha(220)
            painter.fillRect(rect, fill)
            painter.setPen(QPen(border, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
        painter.end()
