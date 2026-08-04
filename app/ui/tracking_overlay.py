# app/ui/tracking_overlay.py
"""Boxes over the game that follow whatever they were drawn around, frame
to frame — built for Хоккей's defenders, but generic (a plain list of
rectangles in, each call).

The point of it: update_targets() is meant to be called several times a
second from a detection loop, and a naive version of that would tear down
and redraw a fresh box every single call — which reads as flicker, not
motion. Here a box is created once, matched to whichever new detection is
nearest it on every later call, and eased toward that new position by its
own animation timer in between — so it visibly *moves* rather than jumps,
however often update_targets() itself is actually called.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, QTimer
from PySide6.QtGui import QColor, QPainter, QPen

from app.ui.game_layer import GameLayer

_FILL   = QColor(255, 59, 59, 40)
_BORDER = QColor(255, 59, 59, 230)
_BORDER_W = 2

_TICK_MS   = 16     # ~60fps easing
_EASE      = 0.35   # fraction of the remaining distance closed per tick
_MAX_MATCH_DIST = 80   # a detection further than this from any existing
                       # box is a new defender, not the same one having moved
_MAX_MISSES = 3        # ticks a box can go unmatched before it is dropped


class _Box:
    __slots__ = ("current", "target", "misses")

    def __init__(self, rect: QRectF):
        self.current = QRectF(rect)
        self.target  = QRectF(rect)
        self.misses  = 0


class TrackingOverlay(GameLayer):
    """Click-through, sized to the whole game window like every other
    calibration/marker overlay here — the boxes themselves are what moves,
    not the widget."""

    def __init__(self, window_manager=None, reference=None):
        super().__init__(window_manager, click_through=True)
        self._reference = reference
        self._boxes: list[_Box] = []
        self._anim_timer = None

    def update_targets(self, rects: list[QRect]):
        """`rects` — the latest detections, screen coordinates. Every call
        after the first one moves existing boxes rather than replacing
        them, as long as a detection lands close enough to one."""
        origin_rect = self.game_rect()
        if origin_rect is not None:
            self.setGeometry(origin_rect)
        elif self.geometry().isEmpty():
            return   # nowhere to place boxes yet, and no prior geometry either
        origin = self.geometry().topLeft()
        locals_ = [QRectF(r.left() - origin.x(), r.top() - origin.y(),
                          r.width(), r.height()) for r in rects]

        unmatched = list(range(len(locals_)))
        for box in self._boxes:
            best_i, best_d = None, _MAX_MATCH_DIST
            bc = box.target.center()
            for i in unmatched:
                rc = locals_[i].center()
                d = ((rc.x() - bc.x()) ** 2 + (rc.y() - bc.y()) ** 2) ** 0.5
                if d < best_d:
                    best_i, best_d = i, d
            if best_i is None:
                box.misses += 1
            else:
                box.target = locals_[best_i]
                box.misses = 0
                unmatched.remove(best_i)

        self._boxes = [b for b in self._boxes if b.misses <= _MAX_MISSES]
        for i in unmatched:
            self._boxes.append(_Box(locals_[i]))   # a new box pops in at
                                                    # the right spot, no fly-in

        if not self.isVisible():
            self.show()
            self.raise_()
        if self._reference is not None:
            self.adopt(self._reference)

        if self._anim_timer is None:
            self._anim_timer = QTimer(self)
            self._anim_timer.timeout.connect(self._tick)
            self._anim_timer.start(_TICK_MS)

    def clear(self):
        if self._anim_timer is not None:
            self._anim_timer.stop()
            self._anim_timer = None
        self._boxes = []
        self.hide()

    def _tick(self):
        if not self._boxes:
            return
        for box in self._boxes:
            c, t = box.current, box.target
            left   = c.left()   + (t.left()   - c.left())   * _EASE
            top    = c.top()    + (t.top()    - c.top())    * _EASE
            width  = c.width()  + (t.width()  - c.width())  * _EASE
            height = c.height() + (t.height() - c.height()) * _EASE
            box.current = QRectF(left, top, width, height)
        self.update()

    def paintEvent(self, event):
        if not self._boxes:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(_BORDER, _BORDER_W))
        painter.setBrush(_FILL)
        for box in self._boxes:
            painter.drawRect(box.current)
        painter.end()
