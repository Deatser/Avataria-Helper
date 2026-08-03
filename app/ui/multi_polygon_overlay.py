# app/ui/multi_polygon_overlay.py
"""Several translucent, independently-coloured polygons over the game at
once — polygon_overlay.py's single filled shape, multiplied, and each one
its own colour so neighbours sharing an edge still read apart."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect
from PySide6.QtGui import QColor, QPainter, QPolygonF

from app.ui.game_layer import GameLayer

_ALPHA        = 110   # fill opacity — pastel, not neon, but still findable
_BORDER_ALPHA = 200


class MultiPolygonOverlay(GameLayer):
    """Sits over several polygons (screen coordinates), each with its own
    fill colour, until cleared. Click-through — painted for the eye,
    never touched by the mouse."""

    def __init__(self, window_manager=None, reference=None):
        super().__init__(window_manager)
        self._reference = reference
        self._shapes: list[tuple[list[QPointF], QColor, QColor]] = []

    def show_shapes(self, shapes: list[tuple[list[tuple[int, int]], str]]):
        """`shapes` — a list of (points, colour) pairs; `colour` a hex
        string, one polygon per entry."""
        all_points = [p for points, _ in shapes for p in points]
        xs = [p[0] for p in all_points]
        ys = [p[1] for p in all_points]
        left, top = min(xs), min(ys)
        self.setGeometry(QRect(left, top, max(xs) - left, max(ys) - top))

        self._shapes = []
        for points, colour_hex in shapes:
            local = [QPointF(x - left, y - top) for x, y in points]
            fill = QColor(colour_hex); fill.setAlpha(_ALPHA)
            border = QColor(colour_hex); border.setAlpha(_BORDER_ALPHA)
            self._shapes.append((local, fill, border))

        if not self.isVisible():
            self.show()
            self.raise_()
        if self._reference is not None:
            self.adopt(self._reference)
        self.update()

    def clear(self):
        self.hide()

    def paintEvent(self, event):
        if not self._shapes:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        for points, fill, border in self._shapes:
            painter.setPen(border)
            painter.setBrush(fill)
            painter.drawPolygon(QPolygonF(points))
        painter.end()
