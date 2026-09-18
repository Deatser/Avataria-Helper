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

    def __init__(self, window_manager=None, reference=None,
                fill: QColor = _FILL, border: QColor = _BORDER):
        super().__init__(window_manager)
        self._reference = reference
        self._fill = fill
        self._border = border
        # Эталонный прямоугольник, а не тот, что выставлен на экране: игра
        # может поменять размер, пока область висит, и пересчитать её тогда
        # можно только из исходных чисел (см. refit).
        self._area: QRect | None = None

    def show_area(self, rect: QRect):
        """`rect` — в эталонных координатах, как всё, что считают моды."""
        self._area = QRect(rect)
        self.setGeometry(self.live_rect(rect))
        if not self.isVisible():
            self.show()
            self.raise_()
        if self._reference is not None:
            self.adopt(self._reference)
        self.update()

    def clear(self):
        self.hide()

    def refit(self):
        """Игра стала другого размера — переехать вместе с ней.

        Область выставляется один раз на весь проход мода, и без этого она
        осталась бы висеть там, где игра была раньше: жёлтая рамка не над
        тем местом, которое мод на самом деле смотрит.
        """
        if self._area is not None and self.isVisible():
            self.setGeometry(self.live_rect(self._area))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._fill)
        painter.setPen(self._border)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()
