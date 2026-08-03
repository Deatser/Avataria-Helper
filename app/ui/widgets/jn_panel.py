# app/ui/widgets/jn_panel.py
"""Amber panel for the Уборщик module.

The same job GdPanel does for Садовник: a surface recognisable at a glance.
Where that one is a greenhouse at dusk, this is a wood-deck café at night —
a warm gradient underfoot and a string of café lights along the top, each
one breathing slightly out of phase with the rest.
"""
import math

from PySide6.QtWidgets import QWidget
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QLinearGradient,
                           QPainterPath, QRadialGradient)
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer

from app.ui import theme

_TICK_MS   = 60
_PULSE_HZ  = 0.16   # how fast a light's own glow breathes
_LIGHT_N   = 7


def _lights(count: int) -> list[tuple[float, float]]:
    """Fixed positions along the top edge, x share and phase — decided once,
    never random on screen, so the panel looks the same every time it opens."""
    return [((i + 0.5) / count, i * 0.9) for i in range(count)]


_LIGHTS = _lights(_LIGHT_N)


class JnPanel(QWidget):
    """Rounded amber surface with a warm floor gradient and café lights."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        self._phase = (self._phase + _TICK_MS / 1000.0 * _PULSE_HZ) % 1.0
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
        surface.setColorAt(0.0, QColor(theme.JN_SURFACE))
        surface.setColorAt(1.0, QColor(theme.JN_BG))
        painter.fillPath(body, QBrush(surface))

        painter.save()
        painter.setClipPath(body)
        self._draw_planks(painter, w, h)
        self._draw_lights(painter, w, h)
        painter.restore()

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(theme.JN_BORDER), 1))
        painter.drawPath(body)

        edge = QLinearGradient(0, 0, w, 0)
        clear = QColor(theme.JN_AMBER); clear.setAlpha(0)
        lit   = QColor(theme.JN_AMBER); lit.setAlpha(120)
        edge.setColorAt(0.0, clear)
        edge.setColorAt(0.35, lit)
        edge.setColorAt(1.0, clear)
        painter.setPen(QPen(QBrush(edge), 1))
        painter.drawLine(theme.PANEL_RADIUS, 1, w - theme.PANEL_RADIUS, 1)
        painter.end()

    def _draw_planks(self, painter: QPainter, w: int, h: int):
        """Barely-there deck seams — texture, not wallpaper."""
        colour = QColor(theme.JN_BORDER)
        colour.setAlpha(60)
        painter.setPen(QPen(colour, 1))
        step = max(28, h // 10)
        y = step
        while y < h:
            painter.drawLine(0, y, w, y)
            y += step

    def _draw_lights(self, painter: QPainter, w: int, h: int):
        """A café string along the top, each bulb breathing on its own clock."""
        wire_y = h * 0.06
        wire = QColor(theme.JN_WOOD); wire.setAlpha(140)
        painter.setPen(QPen(wire, 1))
        painter.drawLine(0, int(wire_y), w, int(wire_y))

        painter.setPen(Qt.NoPen)
        for x_share, offset in _LIGHTS:
            x = w * x_share
            sag = math.sin(x_share * math.pi) * h * 0.02
            y = wire_y + sag
            glow = 0.5 + 0.5 * math.sin((self._phase + offset) * math.tau)

            halo = QRadialGradient(QPointF(x, y), h * 0.09)
            warm = QColor(theme.JN_AMBER); warm.setAlpha(int(40 + 55 * glow))
            gone = QColor(theme.JN_AMBER); gone.setAlpha(0)
            halo.setColorAt(0.0, warm)
            halo.setColorAt(1.0, gone)
            painter.setBrush(halo)
            painter.drawEllipse(QPointF(x, y), h * 0.09, h * 0.09)

            bulb = QColor(theme.JN_AMBER_SOFT)
            bulb.setAlpha(int(160 + 90 * glow))
            painter.setBrush(bulb)
            r = h * 0.012
            painter.drawEllipse(QPointF(x, y), r, r)
