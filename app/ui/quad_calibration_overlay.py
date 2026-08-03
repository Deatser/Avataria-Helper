# app/ui/quad_calibration_overlay.py
"""A four-cornered box over the game, for hand-marking a region that is not
aligned to the screen.

CalibrationOverlay (see calibration_overlay.py) drags by its middle and
resizes by its edges, which is exactly wrong for a slope drawn at an angle
— its lanes run diagonally, not axis-aligned, so no rectangle can ever sit
flush against them. This is the same idea with each of the four corners
draggable on its own instead, which lets the shape lean into a
parallelogram and actually follow the lanes.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF

from app.ui.game_layer import GameLayer

_FILL   = QColor(255, 196, 0, 60)
_BORDER = QColor(255, 196, 0, 220)

_HANDLE_R   = 7    # drawn corner-handle radius
_HANDLE_HIT = 14   # a bit more forgiving than the drawn radius, for grabbing it


class QuadCalibrationOverlay(GameLayer):
    """Sized to the whole game window so a corner can be dragged anywhere
    on it; the four corners themselves are what gets shown and moved.
    Not click-through — that is the whole point of it."""

    def __init__(self, window_manager=None, reference=None):
        super().__init__(window_manager, click_through=False)
        self._reference = reference
        self._corners: list[QPointF] = []   # local to this widget, 4 points
        self._drag_mode: str | int | None = None   # None | "move" | 0..3
        self._drag_origin = QPoint()
        self._drag_start: list[QPointF] = []
        self.setMouseTracking(True)

    def show_at(self, rect: QRect):
        """Covers the whole game window, seeded with a plain rectangle
        inscribed in `rect` (screen coordinates) — drag its corners or its
        middle from there."""
        origin = self.game_rect() or QRect(rect.left() - 200, rect.top() - 200,
                                           rect.width() + 400, rect.height() + 400)
        self.setGeometry(origin)
        ox, oy = origin.left(), origin.top()
        local = QRect(rect.left() - ox, rect.top() - oy, rect.width(), rect.height())
        self._corners = [
            QPointF(local.left(),  local.top()),
            QPointF(local.right(), local.top()),
            QPointF(local.right(), local.bottom()),
            QPointF(local.left(),  local.bottom()),
        ]
        if not self.isVisible():
            self.show()
            self.raise_()
        if self._reference is not None:
            self.adopt(self._reference)
        self.update()

    def clear(self):
        self._drag_mode = None
        self.hide()

    def corners(self) -> list[QPoint]:
        """The four corners, in screen coordinates, in the order show_at()
        seeded them: top-left, top-right, bottom-right, bottom-left."""
        origin = self.geometry().topLeft()
        return [QPoint(int(origin.x() + p.x()), int(origin.y() + p.y()))
               for p in self._corners]

    def bounds(self) -> QRect:
        """The corners' own bounding box, in screen coordinates — a
        width/height/centre reading even once the shape itself has been
        pulled into a parallelogram and is no longer a rectangle."""
        pts = self.corners()
        xs = [p.x() for p in pts]
        ys = [p.y() for p in pts]
        return QRect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        if len(self._corners) != 4:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        path = QPainterPath()
        path.addPolygon(QPolygonF(self._corners))
        painter.fillPath(path, _FILL)
        painter.setPen(QPen(_BORDER, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        painter.setPen(Qt.NoPen)
        painter.setBrush(_BORDER)
        for point in self._corners:
            painter.drawEllipse(point, _HANDLE_R, _HANDLE_R)
        painter.end()

    # ── Mouse ────────────────────────────────────────────────────────────────

    def _corner_at(self, pos: QPointF) -> int | None:
        for i, point in enumerate(self._corners):
            dx, dy = point.x() - pos.x(), point.y() - pos.y()
            if dx * dx + dy * dy <= _HANDLE_HIT ** 2:
                return i
        return None

    def _inside(self, pos: QPointF) -> bool:
        return QPolygonF(self._corners).containsPoint(pos, Qt.OddEvenFill)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.position()
        corner = self._corner_at(pos)
        if corner is not None:
            self._drag_mode = corner
        elif self._inside(pos):
            self._drag_mode = "move"
        else:
            return
        self._drag_origin = event.globalPosition().toPoint()
        self._drag_start = list(self._corners)

    def mouseMoveEvent(self, event):
        if self._drag_mode is None:
            pos = event.position()
            hot = self._corner_at(pos) is not None or self._inside(pos)
            self.setCursor(Qt.SizeAllCursor if hot else Qt.ArrowCursor)
            return

        travelled = event.globalPosition().toPoint() - self._drag_origin
        delta = QPointF(travelled.x(), travelled.y())
        if self._drag_mode == "move":
            self._corners = [point + delta for point in self._drag_start]
        else:
            self._corners = list(self._drag_start)
            self._corners[self._drag_mode] = self._drag_start[self._drag_mode] + delta
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag_mode = None
