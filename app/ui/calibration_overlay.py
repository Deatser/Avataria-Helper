# app/ui/calibration_overlay.py
"""A single axis-aligned box over the game — drag its middle to move it,
drag an edge or corner to resize it, then read its bounds back out.

For regions that really are straight rectangles on screen. Where they are
not (Snowboard's angled lanes), see quad_calibration_overlay.py's own
four-cornered version instead.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from app.ui.game_layer import GameLayer

_FILL   = QColor(103, 211, 245, 55)
_BORDER = QColor(103, 211, 245, 220)

_EDGE_HIT   = 10   # how close to an edge line still counts as grabbing it
_CORNER_HIT = 14   # a corner's own grab radius — a bit more forgiving

# Cursor per drag mode — same feel as a real window's own resize handles.
_CURSORS = {
    "move": Qt.SizeAllCursor,
    "n": Qt.SizeVerCursor,   "s": Qt.SizeVerCursor,
    "e": Qt.SizeHorCursor,   "w": Qt.SizeHorCursor,
    "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
    "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
}


class CalibrationOverlay(GameLayer):
    """Sized to the whole game window so an edge can be dragged anywhere on
    it; the box itself (local coordinates) is what gets shown and moved.
    Not click-through — that is the whole point of it."""

    def __init__(self, window_manager=None, reference=None):
        super().__init__(window_manager, click_through=False)
        self._reference = reference
        self._rect = QRectF()
        self._drag_mode: str | None = None   # None | "move" | n/s/e/w/ne/...
        self._drag_origin = QPoint()
        self._drag_start = QRectF()
        self.setMouseTracking(True)

    def show_at(self, rect: QRect):
        """Covers the whole game window, seeded with `rect` (screen
        coordinates) as the starting box — drag its middle or an edge from
        there."""
        origin = self.game_rect() or QRect(rect.left() - 200, rect.top() - 200,
                                           rect.width() + 400, rect.height() + 400)
        self.setGeometry(origin)
        ox, oy = origin.left(), origin.top()
        self._rect = QRectF(rect.left() - ox, rect.top() - oy,
                            rect.width(), rect.height())
        if not self.isVisible():
            self.show()
            self.raise_()
        if self._reference is not None:
            self.adopt(self._reference)
        self.update()

    def clear(self):
        self._drag_mode = None
        self.hide()

    def bounds(self) -> QRect:
        """The box, in screen coordinates."""
        origin = self.geometry().topLeft()
        r = self._rect.normalized()
        return QRect(int(origin.x() + r.left()), int(origin.y() + r.top()),
                    int(r.width()), int(r.height()))

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        if self._rect.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        r = self._rect.normalized()
        painter.fillRect(r, _FILL)
        painter.setPen(QPen(_BORDER, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(r)
        painter.end()

    # ── Mouse ────────────────────────────────────────────────────────────────

    def _mode_at(self, pos: QPointF) -> str | None:
        r = self._rect.normalized()
        near_l = abs(pos.x() - r.left())   <= _EDGE_HIT
        near_r = abs(pos.x() - r.right())  <= _EDGE_HIT
        near_t = abs(pos.y() - r.top())    <= _EDGE_HIT
        near_b = abs(pos.y() - r.bottom()) <= _EDGE_HIT
        in_x = r.left() - _EDGE_HIT <= pos.x() <= r.right() + _EDGE_HIT
        in_y = r.top()  - _EDGE_HIT <= pos.y() <= r.bottom() + _EDGE_HIT

        if near_t and near_l: return "nw"
        if near_t and near_r: return "ne"
        if near_b and near_l: return "sw"
        if near_b and near_r: return "se"
        if near_t and in_x: return "n"
        if near_b and in_x: return "s"
        if near_l and in_y: return "w"
        if near_r and in_y: return "e"
        if r.contains(pos): return "move"
        return None

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        mode = self._mode_at(event.position())
        if mode is None:
            return
        self._drag_mode = mode
        self._drag_origin = event.globalPosition().toPoint()
        self._drag_start = QRectF(self._rect)

    def mouseMoveEvent(self, event):
        if self._drag_mode is None:
            mode = self._mode_at(event.position())
            self.setCursor(_CURSORS.get(mode, Qt.ArrowCursor))
            return

        travelled = event.globalPosition().toPoint() - self._drag_origin
        dx, dy = travelled.x(), travelled.y()
        r = QRectF(self._drag_start)

        if self._drag_mode == "move":
            r.translate(dx, dy)
        else:
            if "n" in self._drag_mode: r.setTop(r.top() + dy)
            if "s" in self._drag_mode: r.setBottom(r.bottom() + dy)
            if "w" in self._drag_mode: r.setLeft(r.left() + dx)
            if "e" in self._drag_mode: r.setRight(r.right() + dx)

        self._rect = r
        self.update()

    def mouseReleaseEvent(self, event):
        self._drag_mode = None
