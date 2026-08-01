# app/ui/widgets/nt_panel.py
import math
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import (QPainter, QColor, QPen, QPainterPath, QLinearGradient,
                           QBrush)
from PySide6.QtCore import Qt, QRectF, QTimer
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

_DIM   = QColor(theme.CIRCUIT_DIM)
_GLOW  = QColor(theme.CIRCUIT_GLOW)


class NtPanel(QWidget):
    """Rounded surface with slow, ambient PCB traces behind the content."""

    def __init__(self, parent=None, traces: bool = True):
        super().__init__(parent)
        self._traces = traces   # off for small surfaces like dialogs
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        if not self._traces:
            return
        self._phase = (self._phase + 0.012) % 1.0
        self.update()

    def _circuit_color(self, offset: float) -> QColor:
        t = 0.5 + 0.5 * math.sin(2 * math.pi * (self._phase + offset))
        c = QColor(
            int(_DIM.red()   + (_GLOW.red()   - _DIM.red())   * t),
            int(_DIM.green() + (_GLOW.green() - _DIM.green()) * t),
            int(_DIM.blue()  + (_GLOW.blue()  - _DIM.blue())  * t),
        )
        c.setAlpha(int(70 + 90 * t))
        return c

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()

        body = QPainterPath()
        body.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1),
                            theme.PANEL_RADIUS, theme.PANEL_RADIUS)

        # 1. Surface — soft vertical falloff instead of a flat black slab
        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0.0, QColor(theme.BG_SURFACE))
        grad.setColorAt(1.0, QColor(theme.BG_BASE))
        painter.fillPath(body, QBrush(grad))

        painter.save()
        painter.setClipPath(body)

        # 2. Dot grid — texture, not pattern
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.DOT_COLOR))
        sp, r = theme.DOT_SPACING, theme.DOT_RADIUS
        for x in range(sp, w - sp, sp):
            for y in range(sp, h - sp, sp):
                painter.drawEllipse(x - r, y - r, r * 2, r * 2)

        # 3. Ambient PCB traces
        if self._traces:
            self._draw_maze(painter, w, h)
        painter.restore()

        # 4. Hairline edge + a single accent highlight along the top
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawPath(body)

        edge = QLinearGradient(0, 0, w, 0)
        a1 = QColor(theme.ACCENT); a1.setAlpha(0)
        a2 = QColor(theme.ACCENT); a2.setAlpha(110)
        edge.setColorAt(0.0, a1)
        edge.setColorAt(0.35, a2)
        edge.setColorAt(1.0, a1)
        painter.setPen(QPen(QBrush(edge), 1))
        painter.drawLine(theme.PANEL_RADIUS, 1, w - theme.PANEL_RADIUS, 1)

        painter.end()

    def _draw_maze(self, painter, w, h):
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(Qt.NoBrush)

        for offset, waypoints in _MAZE:
            c   = self._circuit_color(offset)
            pts = [(int(xr * w), int(yr * h)) for xr, yr in waypoints]

            # Soft halo, then a thin core — reads as depth, not as neon tubing
            halo = QColor(c); halo.setAlpha(c.alpha() // 5)
            painter.setPen(QPen(halo, 4))
            for i in range(len(pts) - 1):
                painter.drawLine(*pts[i], *pts[i + 1])

            painter.setPen(QPen(c, 1))
            for i in range(len(pts) - 1):
                painter.drawLine(*pts[i], *pts[i + 1])

            # Junction markers at interior corners
            painter.setPen(Qt.NoPen)
            painter.setBrush(c)
            for px, py in pts[1:-1]:
                painter.drawEllipse(px - 2, py - 2, 4, 4)
            painter.setBrush(Qt.NoBrush)
