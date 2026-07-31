# app/ui/widgets/vw_panel.py
"""Vaporwave-aesthetic panel for the Ava Dancers module window."""
import math
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient, QBrush
from PySide6.QtCore import Qt, QTimer
from app.ui import theme

# Fixed star field: (x%, y_in_top_44%, radius_px)
_STARS = [
    (0.08, 0.06, 1), (0.15, 0.18, 1), (0.22, 0.04, 2), (0.30, 0.12, 1),
    (0.38, 0.22, 1), (0.45, 0.03, 1), (0.52, 0.14, 2), (0.60, 0.08, 1),
    (0.68, 0.20, 1), (0.75, 0.05, 1), (0.82, 0.16, 2), (0.90, 0.10, 1),
    (0.12, 0.28, 1), (0.25, 0.32, 1), (0.35, 0.38, 1), (0.50, 0.30, 2),
    (0.65, 0.24, 1), (0.80, 0.35, 1), (0.93, 0.28, 1), (0.04, 0.40, 1),
    (0.58, 0.42, 1), (0.72, 0.08, 1), (0.42, 0.26, 1),
]

_GRID_N_VERT  = 10
_GRID_N_HORIZ = 9


class VwPanel(QWidget):
    """Vaporwave panel: dark purple bg, scrolling perspective grid, star field."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        self._phase = (self._phase + 0.003) % 1.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        w, h = self.width(), self.height()

        # 1. Background gradient
        painter.setRenderHint(QPainter.Antialiasing, False)
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor("#0f0a28"))
        grad.setColorAt(1.0, QColor(theme.VW_BG))
        painter.fillRect(self.rect(), QBrush(grad))

        # 2. Star field (twinkling)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        sky_h = int(h * 0.44)
        for i, (xr, yr, sz) in enumerate(_STARS):
            a = int(80 + 90 * (0.5 + 0.5 * math.sin(
                2 * math.pi * (self._phase * 2.5 + i * 0.41)
            )))
            painter.setBrush(QColor(210, 200, 255, a))
            painter.drawEllipse(int(xr * w) - sz, int(yr * sky_h) - sz, sz * 2, sz * 2)

        # 3. Perspective grid
        painter.setRenderHint(QPainter.Antialiasing, False)
        self._draw_grid(painter, w, h)

        # 4. Corner brackets (magenta glow)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._draw_corner(painter, 2,     2,     False, False)
        self._draw_corner(painter, w - 2, 2,     True,  False)
        self._draw_corner(painter, 2,     h - 2, False, True)
        self._draw_corner(painter, w - 2, h - 2, True,  True)

        # 5. Neon borders
        painter.setRenderHint(QPainter.Antialiasing, False)
        bc = QColor(theme.VW_MAGENTA); bc.setAlpha(120)
        painter.setPen(QPen(bc, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        ic = QColor(theme.VW_CYAN); ic.setAlpha(40)
        painter.setPen(QPen(ic, 1))
        painter.drawRect(self.rect().adjusted(3, 3, -4, -4))

        painter.end()

    def _draw_grid(self, painter, w, h):
        vp_x = w * 0.5
        vp_y = h * 0.38

        # Vertical lines (static, fan out from VP)
        for i in range(_GRID_N_VERT + 1):
            x_bot = w * i / _GRID_N_VERT
            t = abs(i - _GRID_N_VERT / 2) / (_GRID_N_VERT / 2)
            c = QColor(int(40 * t), int(80 - 60 * t), int(200 + 55 * t), 80)
            painter.setPen(QPen(c, 1))
            painter.drawLine(int(vp_x), int(vp_y), int(x_bot), h)

        # Horizontal lines (scrolling)
        for j in range(_GRID_N_HORIZ + 2):
            z = ((j / _GRID_N_HORIZ) + self._phase) % 1.0
            if z < 0.02:
                continue
            y = int(vp_y + (h - vp_y) * (z ** 1.7))
            if y > h:
                continue
            alpha = int(30 + 100 * (z ** 0.8))
            t = z
            c2 = QColor(int(255 * t), int(160 - 140 * t), int(255 - 100 * t), alpha)
            painter.setPen(QPen(c2, 1))
            t_y = (y - vp_y) / (h - vp_y) if h > vp_y else 0
            x_left  = int(vp_x + (0 - vp_x) * t_y)
            x_right = int(vp_x + (w - vp_x) * t_y)
            painter.drawLine(x_left, y, x_right, y)

    def _draw_corner(self, painter, x, y, flip_x, flip_y, size=14):
        dx = -1 if flip_x else 1
        dy = -1 if flip_y else 1
        for i in range(5, 0, -1):
            c = QColor(theme.VW_MAGENTA); c.setAlpha(10 * i)
            painter.setPen(QPen(c, i + 1))
            painter.drawLine(x, y, x + dx * (size + i), y)
            painter.drawLine(x, y, x, y + dy * (size + i))
        painter.setPen(QPen(QColor(theme.VW_MAGENTA), 1))
        painter.drawLine(x, y, x + dx * size, y)
        painter.drawLine(x, y, x, y + dy * size)
