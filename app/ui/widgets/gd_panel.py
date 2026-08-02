# app/ui/widgets/gd_panel.py
"""Olive panel for the Садовник module.

The same job VwPanel does for Ava Dancers: give one module a surface of its
own so it is recognisable at a glance. Where that one is a neon sunset, this
is a greenhouse at dusk — warm light from above, moss in the corners, and a
few leaves drifting slowly enough that you only notice them if you look.
"""
import math
import random

from PySide6.QtWidgets import QWidget
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QLinearGradient,
                           QPainterPath, QRadialGradient)
from PySide6.QtCore import Qt, QRectF, QPointF, QTimer

from app.ui import theme

_TICK_MS   = 60
_DRIFT     = 0.0018   # how far the leaves travel per tick, in panel widths
_LEAF_N    = 14
_VEIN_N    = 5        # slow vertical vines behind everything
_GLOW_TOP  = 0.34     # height of the light falling from above, as a share


def _leaves(count: int) -> list[tuple[float, float, float, float, float]]:
    """Fixed scatter: x, y, size, tilt, phase — decided once, never random
    on screen, so the panel looks the same every time it is opened."""
    rng = random.Random(4)
    return [(rng.random(), rng.random(), rng.uniform(5.0, 11.0),
             rng.uniform(0, math.tau), rng.uniform(0, math.tau))
            for _ in range(count)]


_LEAVES = _leaves(_LEAF_N)
_VINES  = [(0.12, 0.9), (0.31, 1.3), (0.53, 0.7), (0.72, 1.1), (0.89, 0.8)]


class GdPanel(QWidget):
    """Rounded olive surface with a light well, vines and drifting leaves."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _tick(self):
        self._phase = (self._phase + _DRIFT) % 1.0
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
        surface.setColorAt(0.0, QColor(theme.GD_SURFACE))
        surface.setColorAt(1.0, QColor(theme.GD_BG))
        painter.fillPath(body, QBrush(surface))

        painter.save()
        painter.setClipPath(body)
        self._draw_light(painter, w, h)
        self._draw_vines(painter, w, h)
        self._draw_leaves(painter, w, h)
        painter.restore()

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(theme.GD_BORDER), 1))
        painter.drawPath(body)

        edge = QLinearGradient(0, 0, w, 0)
        clear = QColor(theme.GD_OLIVE); clear.setAlpha(0)
        lit   = QColor(theme.GD_OLIVE); lit.setAlpha(120)
        edge.setColorAt(0.0, clear)
        edge.setColorAt(0.35, lit)
        edge.setColorAt(1.0, clear)
        painter.setPen(QPen(QBrush(edge), 1))
        painter.drawLine(theme.PANEL_RADIUS, 1, w - theme.PANEL_RADIUS, 1)
        painter.end()

    def _draw_light(self, painter: QPainter, w: int, h: int):
        """Sunlight through glass — the reason anything in here grows."""
        well = QRadialGradient(QPointF(w * 0.5, -h * 0.1), h * _GLOW_TOP * 2.6)
        warm = QColor(theme.GD_MOSS); warm.setAlpha(70)
        gone = QColor(theme.GD_MOSS); gone.setAlpha(0)
        well.setColorAt(0.0, warm)
        well.setColorAt(1.0, gone)
        painter.setPen(Qt.NoPen)
        painter.setBrush(well)
        painter.drawRect(0, 0, w, int(h * _GLOW_TOP * 2))

    def _draw_vines(self, painter: QPainter, w: int, h: int):
        """Slow verticals, barely there — texture rather than decoration."""
        colour = QColor(theme.GD_BORDER)
        colour.setAlpha(85)   # texture, not wallpaper — they should be felt
        painter.setPen(QPen(colour, 1.1))
        painter.setBrush(Qt.NoBrush)
        for share, wobble in _VINES:
            path = QPainterPath(QPointF(w * share, 0))
            steps = 8
            for i in range(1, steps + 1):
                y = h * i / steps
                sway = math.sin(i * wobble + self._phase * math.tau) * w * 0.012
                path.lineTo(w * share + sway, y)
            painter.drawPath(path)

    def _draw_leaves(self, painter: QPainter, w: int, h: int):
        painter.setPen(Qt.NoPen)
        for x_share, y_share, size, tilt, phase in _LEAVES:
            drift = (y_share + self._phase) % 1.0
            centre = QPointF(w * x_share
                             + math.sin(drift * math.tau + phase) * w * 0.02,
                             h * drift)
            colour = QColor(theme.GD_OLIVE)
            colour.setAlpha(int(26 + 22 * (0.5 + 0.5 * math.sin(phase + self._phase * math.tau))))
            painter.setBrush(colour)

            painter.save()
            painter.translate(centre)
            painter.rotate(math.degrees(tilt))
            leaf = QPainterPath()
            leaf.moveTo(0, -size)
            leaf.quadTo(size * 0.75, 0, 0, size)
            leaf.quadTo(-size * 0.75, 0, 0, -size)
            painter.drawPath(leaf)
            painter.restore()
