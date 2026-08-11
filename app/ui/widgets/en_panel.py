# app/ui/widgets/en_panel.py
"""Dark surface for the Энергия window.

Built the same way GdPanel is — one painted surface per window so it is
recognisable at a glance — but where the garden is a greenhouse at dusk,
this is a room lit by a single lamp: near-black walls, a warm well of light
high on the left, and motes of energy drifting up through it slowly enough
that nothing on top of them ever has to compete.
"""
import math
import random

from PySide6.QtWidgets import QWidget
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QLinearGradient,
                           QPainterPath, QRadialGradient)
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer

from app.ui import theme

_TICK_MS = 60
_RISE    = 0.0011   # how far a mote climbs per tick, in panel heights
_MOTE_N  = 22
_GLOW    = 0.42     # radius of the lamp's well, as a share of the height


def _motes(count: int) -> list[tuple[float, float, float, float, float]]:
    """Fixed scatter: x, y, size, sway rate, phase — decided once, so the
    panel looks the same every time the window is opened."""
    rng = random.Random(11)
    return [(rng.random(), rng.random(), rng.uniform(1.4, 3.4),
             rng.uniform(0.6, 1.8), rng.uniform(0, math.tau))
            for _ in range(count)]


_MOTES = _motes(_MOTE_N)


class EnPanel(QWidget):
    """Rounded near-black surface with a lamp well and rising motes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        self._phase = (self._phase + _RISE) % 1.0
        self.update()

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()

        body = QPainterPath()
        body.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1),
                            theme.PANEL_RADIUS, theme.PANEL_RADIUS)

        surface = QLinearGradient(0, 0, 0, h)
        surface.setColorAt(0.0, QColor(theme.EN_SURFACE))
        surface.setColorAt(1.0, QColor(theme.EN_BG))
        painter.fillPath(body, QBrush(surface))

        painter.save()
        painter.setClipPath(body)
        self._draw_lamp(painter, w, h)
        self._draw_motes(painter, w, h)
        painter.restore()

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(theme.EN_BORDER), 1))
        painter.drawPath(body)

        # One lit stroke along the top edge, brightest where the lamp is
        edge = QLinearGradient(0, 0, w, 0)
        clear = QColor(theme.EN_YELLOW); clear.setAlpha(0)
        lit   = QColor(theme.EN_YELLOW); lit.setAlpha(110)
        edge.setColorAt(0.0, clear)
        edge.setColorAt(0.3, lit)
        edge.setColorAt(1.0, clear)
        painter.setPen(QPen(QBrush(edge), 1))
        painter.drawLine(theme.PANEL_RADIUS, 1, w - theme.PANEL_RADIUS, 1)
        painter.end()

    def _draw_lamp(self, painter: QPainter, w: int, h: int):
        """The one warm light in the room — everything else is its falloff."""
        well = QRadialGradient(QPointF(w * 0.3, -h * 0.05), h * _GLOW * 2.2)
        warm = QColor(theme.EN_EMBER); warm.setAlpha(64)
        gone = QColor(theme.EN_EMBER); gone.setAlpha(0)
        well.setColorAt(0.0, warm)
        well.setColorAt(1.0, gone)
        painter.setPen(Qt.NoPen)
        painter.setBrush(well)
        painter.drawRect(0, 0, w, int(h * _GLOW * 1.8))

    def _draw_motes(self, painter: QPainter, w: int, h: int):
        """Specks of energy rising and fading out near the ceiling."""
        painter.setPen(Qt.NoPen)
        for x_share, y_share, size, sway_rate, phase in _MOTES:
            # Climbing means the offset is subtracted; the modulo wraps a
            # mote that has reached the top back around to the floor.
            rise = (y_share - self._phase) % 1.0
            sway = math.sin(self._phase * math.tau * sway_rate + phase)
            x = w * x_share + sway * w * 0.015
            y = h * rise
            # Brightest mid-flight, gone at both ends, so nothing pops in
            fade = math.sin(rise * math.pi)
            colour = QColor(theme.EN_YELLOW_SOFT)
            colour.setAlpha(int(120 * fade))
            painter.setBrush(colour)
            painter.drawEllipse(QPointF(x, y), size, size)

            halo = QColor(theme.EN_YELLOW)
            halo.setAlpha(int(34 * fade))
            painter.setBrush(halo)
            painter.drawEllipse(QPointF(x, y), size * 2.6, size * 2.6)
