# app/ui/resize_mixin.py
"""Mixin for edge-drag resize on frameless QWidget windows."""
from PySide6.QtCore import Qt, QRect, QPoint

_M = 8   # hit-zone margin in pixels
_L, _R, _T, _B = 1, 2, 4, 8

_CURSORS = {
    _L:        Qt.SizeHorCursor,
    _R:        Qt.SizeHorCursor,
    _T:        Qt.SizeVerCursor,
    _B:        Qt.SizeVerCursor,
    _L | _T:   Qt.SizeFDiagCursor,
    _R | _B:   Qt.SizeFDiagCursor,
    _R | _T:   Qt.SizeBDiagCursor,
    _L | _B:   Qt.SizeBDiagCursor,
}


class ResizeMixin:
    """
    Inherit before QWidget. Call _init_resize() at end of __init__.
    Override _on_resize_panel() to keep NtPanel in sync during drag.
    Override _on_resize_done()  to persist the new size to config.
    """
    _RESIZE_MIN_W: int = 220
    _RESIZE_MIN_H: int = 240

    def _init_resize(self):
        self._rsz_dir  = 0
        self._rsz_orig = QPoint()
        self._rsz_geo  = QRect()
        self.setMouseTracking(True)

    # ── Edge detection ───────────────────────────────────────────────────────

    def _edge_dir(self, local_pos: QPoint) -> int:
        w, h = self.width(), self.height()
        d = 0
        if local_pos.x() < _M:        d |= _L
        elif local_pos.x() > w - _M:  d |= _R
        if local_pos.y() < _M:        d |= _T
        elif local_pos.y() > h - _M:  d |= _B
        return d

    # ── Mouse events ─────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            d = self._edge_dir(event.position().toPoint())
            if d:
                self._rsz_dir  = d
                self._rsz_orig = event.globalPosition().toPoint()
                self._rsz_geo  = self.geometry()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._rsz_dir and (event.buttons() & Qt.LeftButton):
            delta = event.globalPosition().toPoint() - self._rsz_orig
            geo   = QRect(self._rsz_geo)

            if self._rsz_dir & _L:
                new_w = max(self._RESIZE_MIN_W, geo.width() - delta.x())
                geo.setLeft(geo.right() - new_w + 1)
            if self._rsz_dir & _R:
                geo.setRight(max(geo.left() + self._RESIZE_MIN_W - 1,
                                 geo.right() + delta.x()))
            if self._rsz_dir & _T:
                new_h = max(self._RESIZE_MIN_H, geo.height() - delta.y())
                geo.setTop(geo.bottom() - new_h + 1)
            if self._rsz_dir & _B:
                geo.setBottom(max(geo.top() + self._RESIZE_MIN_H - 1,
                                  geo.bottom() + delta.y()))

            self.setGeometry(geo)
            self._on_resize_panel()
            event.accept()
            return

        # Cursor hint when not dragging
        d = self._edge_dir(event.position().toPoint())
        self.setCursor(_CURSORS.get(d, Qt.ArrowCursor))
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._rsz_dir:
            self._rsz_dir = 0
            self._on_resize_done()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ── Subclass hooks ───────────────────────────────────────────────────────

    def _on_resize_panel(self):
        """Resize background NtPanel to fill window. Override in subclass."""
        pass

    def _on_resize_done(self):
        """Persist new size to config. Override in subclass."""
        pass
