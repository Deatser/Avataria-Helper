# app/ui/widgets/nt_panel.py
import math
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtCore import Qt, QTimer
from app.ui import theme

# Maze-like PCB labyrinth: each entry is (phase_offset, [(x%, y%), ...])
_MAZE = [
    (0.00, [(0.08, 0.08), (0.08, 0.25), (0.22, 0.25), (0.22, 0.18)]),
    (0.13, [(0.92, 0.08), (0.92, 0.20), (0.78, 0.20), (0.78, 0.32), (0.62, 0.32)]),
    (0.25, [(0.05, 0.40), (0.20, 0.40), (0.20, 0.53), (0.35, 0.53), (0.35, 0.45)]),
    (0.38, [(0.40, 0.33), (0.60, 0.33), (0.60, 0.48), (0.48, 0.48), (0.48, 0.40)]),
    (0.50, [(0.88, 0.38), (0.88, 0.55), (0.70, 0.55), (0.70, 0.63), (0.82, 0.63)]),
    (0.63, [(0.05, 0.65), (0.05, 0.80), (0.25, 0.80), (0.25, 0.72), (0.38, 0.72)]),
    (0.75, [(0.42, 0.62), (0.42, 0.80), (0.60, 0.80), (0.60, 0.68), (0.72, 0.68)]),
    (0.88, [(0.82, 0.72), (0.82, 0.90), (0.65, 0.90), (0.65, 0.82)]),
]

_DIM_RGB   = (12,  32,  16)
_GLOW_RGB  = (0,  204,  68)


class NtPanel(QWidget):
    """Background panel with animated PCB-labyrinth traces."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        self._phase = (self._phase + 0.025) % 1.0
        self.update()

    def _circuit_color(self, offset: float) -> QColor:
        t = 0.5 + 0.5 * math.sin(2 * math.pi * (self._phase + offset))
        r = int(_DIM_RGB[0] + (_GLOW_RGB[0] - _DIM_RGB[0]) * t)
        g = int(_DIM_RGB[1] + (_GLOW_RGB[1] - _DIM_RGB[1]) * t)
        b = int(_DIM_RGB[2] + (_GLOW_RGB[2] - _DIM_RGB[2]) * t)
        return QColor(r, g, b)

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

        # 3. Animated PCB labyrinth
        self._draw_maze(painter, w, h)

        # 4. Scanlines (subtle, every 3 px)
        scan_c = QColor(0, 0, 0, 18)
        painter.setPen(QPen(scan_c, 1))
        for y in range(0, h, 3):
            painter.drawLine(0, y, w, y)

        # 5. Glowing corner brackets
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

    def _draw_maze(self, painter, w, h):
        painter.setRenderHint(QPainter.Antialiasing, False)

        for offset, waypoints in _MAZE:
            c = self._circuit_color(offset)
            pts = [(int(xr * w), int(yr * h)) for xr, yr in waypoints]

            # Glow halo (2px wider, lower alpha)
            halo = QColor(c); halo.setAlpha(60)
            painter.setPen(QPen(halo, 3))
            for i in range(len(pts) - 1):
                painter.drawLine(*pts[i], *pts[i + 1])

            # Core trace
            painter.setPen(QPen(c, 1))
            for i in range(len(pts) - 1):
                painter.drawLine(*pts[i], *pts[i + 1])

            # Junction dots at interior corners
            painter.setPen(Qt.NoPen)
            painter.setBrush(c)
            for px, py in pts[1:-1]:
                painter.drawEllipse(px - 3, py - 3, 6, 6)

    def _draw_corner(self, painter, x, y, flip_x, flip_y, size=14):
        dx = -1 if flip_x else 1
        dy = -1 if flip_y else 1
        for i in range(5, 0, -1):
            c = QColor(theme.ACCENT_RED); c.setAlpha(10 * i)
            painter.setPen(QPen(c, i + 1))
            painter.drawLine(x, y, x + dx * (size + i), y)
            painter.drawLine(x, y, x, y + dy * (size + i))
        painter.setPen(QPen(QColor(theme.ACCENT_RED), 1))
        painter.drawLine(x, y, x + dx * size, y)
        painter.drawLine(x, y, x, y + dy * size)
