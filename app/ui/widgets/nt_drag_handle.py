# app/ui/widgets/nt_drag_handle.py
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtCore import Qt
from app.ui import theme


class NtDragHandle(QWidget):
    """
    Unified drag-handle bar — a single continuous element with grip dots.
    Replaces the old three-cluster braille label.
    """

    def __init__(self, parent=None, dot_color: str = None):
        super().__init__(parent)
        self._dot_color = dot_color or theme.TEXT_DIM
        self.setFixedHeight(14)
        self.setCursor(Qt.SizeAllCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        cy = h // 2

        # Background track (full-width subtle line)
        track = QColor(theme.BORDER_DIM)
        painter.setPen(QPen(track, 1))
        painter.drawLine(20, cy, w - 20, cy)

        # Central grip dots (7 dots, fading from center outward)
        n   = 7
        gap = 12
        total_w = (n - 1) * gap
        start_x = (w - total_w) // 2

        painter.setPen(Qt.NoPen)
        for i in range(n):
            dist = abs(i - (n - 1) / 2) / ((n - 1) / 2)   # 0=center, 1=edge
            alpha = int(180 - 100 * dist)
            c = QColor(self._dot_color)
            c.setAlpha(alpha)
            painter.setBrush(c)
            x = start_x + i * gap
            painter.drawEllipse(x - 2, cy - 2, 4, 4)

        # Tiny end caps
        cap = QColor(theme.BORDER)
        painter.setPen(QPen(cap, 1))
        painter.drawLine(20, cy - 3, 20, cy + 3)
        painter.drawLine(w - 20, cy - 3, w - 20, cy + 3)

        painter.end()
