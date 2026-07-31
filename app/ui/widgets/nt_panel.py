# app/ui/widgets/nt_panel.py
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtCore import Qt
from app.ui import theme


class NtPanel(QWidget):
    """Panel with dot-grid, PCB traces, scanlines, and glowing corner brackets."""

    def __init__(self, parent=None):
        super().__init__(parent)

    def paintEvent(self, event):
        painter = QPainter(self)
        w, h = self.width(), self.height()

        # 1. Background
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(self.rect(), QColor(theme.BG_SURFACE))

        # 2. Dot grid
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.DOT_COLOR))
        sp, r = theme.DOT_SPACING, theme.DOT_RADIUS
        for x in range(sp, w - sp, sp):
            for y in range(sp, h - sp, sp):
                painter.drawEllipse(x - r, y - r, r * 2, r * 2)

        # 3. PCB circuit traces
        self._draw_circuits(painter, w, h)

        # 4. Scanlines (every 3px, very subtle)
        scan_c = QColor(0, 0, 0, 18)
        painter.setPen(QPen(scan_c, 1))
        for y in range(0, h, 3):
            painter.drawLine(0, y, w, y)

        # 5. Corner brackets with neon glow
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._draw_corner(painter, 2,     2,     False, False)
        self._draw_corner(painter, w - 2, 2,     True,  False)
        self._draw_corner(painter, 2,     h - 2, False, True)
        self._draw_corner(painter, w - 2, h - 2, True,  True)

        # 6. Border
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        painter.end()

    def _draw_circuits(self, painter, w, h):
        painter.setRenderHint(QPainter.Antialiasing, False)
        c = QColor(theme.CIRCUIT_COLOR)
        painter.setPen(QPen(c, 1))
        painter.setBrush(c)

        # Relative trace positions (x1%, y1%, x2%, y2%)
        traces = [
            (0.06, 0.22, 0.28, 0.22),
            (0.28, 0.22, 0.28, 0.30),
            (0.28, 0.30, 0.16, 0.30),
            (0.72, 0.55, 0.94, 0.55),
            (0.72, 0.48, 0.72, 0.55),
            (0.60, 0.48, 0.72, 0.48),
            (0.08, 0.68, 0.32, 0.68),
            (0.32, 0.68, 0.32, 0.78),
            (0.32, 0.78, 0.20, 0.78),
            (0.80, 0.18, 0.92, 0.18),
            (0.80, 0.18, 0.80, 0.28),
        ]
        for x1r, y1r, x2r, y2r in traces:
            painter.drawLine(int(x1r * w), int(y1r * h),
                             int(x2r * w), int(y2r * h))

        # Junction solder dots
        painter.setPen(Qt.NoPen)
        for xr, yr in [(0.28, 0.22), (0.28, 0.30), (0.72, 0.55),
                       (0.72, 0.48), (0.32, 0.68), (0.80, 0.18)]:
            x, y = int(xr * w), int(yr * h)
            painter.drawEllipse(x - 2, y - 2, 4, 4)

    def _draw_corner(self, painter, x, y, flip_x, flip_y, size=14):
        dx = -1 if flip_x else 1
        dy = -1 if flip_y else 1

        # Glow halos (outermost → innermost)
        for i in range(5, 0, -1):
            c = QColor(theme.ACCENT_RED)
            c.setAlpha(10 * i)
            painter.setPen(QPen(c, i + 1))
            painter.drawLine(x, y, x + dx * (size + i), y)
            painter.drawLine(x, y, x, y + dy * (size + i))

        # Solid bracket line
        painter.setPen(QPen(QColor(theme.ACCENT_RED), 1))
        painter.drawLine(x, y, x + dx * size, y)
        painter.drawLine(x, y, x, y + dy * size)
