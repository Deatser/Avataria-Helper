# app/ui/widgets/nt_panel.py
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCore import Qt
from app.ui import theme


class NtPanel(QWidget):
    """Base panel with dot-grid texture and border. Use as background widget."""

    def __init__(self, parent=None):
        super().__init__(parent)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        # 1. Solid background
        painter.fillRect(self.rect(), QColor(theme.BG_SURFACE))

        # 2. Dot grid overlay
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.DOT_COLOR))
        sp = theme.DOT_SPACING
        r  = theme.DOT_RADIUS
        w, h = self.width(), self.height()
        for x in range(sp, w - sp, sp):
            for y in range(sp, h - sp, sp):
                painter.drawEllipse(x - r, y - r, r * 2, r * 2)

        # 3. 1px border
        painter.setPen(QColor(theme.BORDER))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        painter.end()
