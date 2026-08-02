# app/ui/marker_overlay.py
"""Coloured dots on the game, one per coordinate printed in a log.

The log says a bush is at (1412, 885); this is what makes that mean
something without counting pixels. Each dot takes the colour its own line in
the log is written in, so the two are read together.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, QPointF, QRect, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

from app.ui.game_layer import GameLayer

_FOLLOW_MS = 200    # the game window rarely moves; this is only to keep up
_DOT_R     = 6.0
_RING_R    = 11.0
_BLOOM_R   = 22.0
_PULSE_MS  = 40     # breathing, so a dot on busy scenery still catches the eye
_PULSE_AMP = 0.18


@dataclass
class Marker:
    x: int          # screen coordinates, as reported by the scan
    y: int
    colour: str
    solid: bool = True   # a near miss is drawn hollow — same colour, less claim


class MarkerOverlay(GameLayer):
    """Dots over the game window, cleared when the log that named them is."""

    def __init__(self, window_manager=None, reference: QWidget = None):
        super().__init__(window_manager)
        self._markers: list[Marker] = []
        self._reference = reference   # a window of ours, to find the game by
        self._phase = 0.0

        self._pulse = QTimer(self)
        self._pulse.setInterval(_PULSE_MS)
        self._pulse.timeout.connect(self._tick)

        self._follow = QTimer(self)
        self._follow.setInterval(_FOLLOW_MS)
        self._follow.timeout.connect(self._sync)

    # ── Public API ───────────────────────────────────────────────────────────

    def show_markers(self, markers: list[Marker]):
        self._markers = self._inside_game(markers)
        if not self._markers:
            self.clear()
            return
        self._sync()
        self._follow.start()
        self._pulse.start()

    def _inside_game(self, markers: list[Marker]) -> list[Marker]:
        """Only what is on the game's own window; the rest is not the garden."""
        area = self.game_rect()
        if area is None:
            return list(markers)
        return [m for m in markers if area.contains(QRect(m.x, m.y, 1, 1))]

    def clear(self):
        self._markers = []
        self._follow.stop()
        self._pulse.stop()
        self.hide()

    # ── Placement ────────────────────────────────────────────────────────────

    def _sync(self):
        """Sit exactly over the game, so screen coordinates are just offsets.

        Exactly, not generously: the layer is what keeps the dots inside
        Avataria's window, and anything found beyond its edges is something
        that is not in the garden at all.
        """
        area = self.game_rect()
        if area is None or not self._markers:
            self.hide()
            return
        if area != self.geometry():
            self.setGeometry(area)
        if not self.isVisible():
            self.show()
            self.raise_()
        if self._reference is not None:
            self.adopt(self._reference)   # after show — Qt resets the owner
        self.update()

    def _tick(self):
        self._phase += 0.08
        self.update()

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        import math

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        origin = self.geometry().topLeft()
        beat   = 1.0 + _PULSE_AMP * math.sin(self._phase)

        for marker in self._markers:
            centre = QPointF(marker.x - origin.x(), marker.y - origin.y())
            colour = QColor(marker.colour)

            bloom = QRadialGradient(centre, _BLOOM_R * beat)
            inner = QColor(colour); inner.setAlpha(90)
            outer = QColor(colour); outer.setAlpha(0)
            bloom.setColorAt(0.0, inner)
            bloom.setColorAt(1.0, outer)
            painter.setPen(Qt.NoPen)
            painter.setBrush(bloom)
            painter.drawEllipse(centre, _BLOOM_R * beat, _BLOOM_R * beat)

            ring = QColor(colour)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(ring, 2.0))
            painter.drawEllipse(centre, _RING_R * beat, _RING_R * beat)

            if marker.solid:
                painter.setPen(QPen(QColor(255, 255, 255, 220), 1.2))
                painter.setBrush(colour)
                painter.drawEllipse(centre, _DOT_R, _DOT_R)
        painter.end()
