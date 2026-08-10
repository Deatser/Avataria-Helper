# modules/hockey/level_strip.py
"""The ten levels of a run, as ten chips.

Every number is on screen from the start, so the strip reads as a route
rather than as a counter that grows: the ones behind you carry a tick or a
cross, the one you are on is lit, the ones ahead wait their turn. That is
the whole state of a run in one glance, and it replaces a line of text and a
row of coloured digits that said the same thing worse.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from app.ui import theme

_LEVELS = 10

_GAP     = 4          # between chips
_HEIGHT  = 46
_RADIUS  = 7
_MARK_H  = 15         # the strip of the chip the tick or cross sits in
_MARK_W  = 11         # and how wide the mark itself is drawn


class LevelStrip(QWidget):
    """Ten chips. `set_reading` is the only thing that changes them."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._states: list[str | None] = [None] * (_LEVELS - 1)
        self._level: int | None = None
        self.setMinimumHeight(_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    # ── What it shows ────────────────────────────────────────────────────

    def set_reading(self, states: list, level: int | None):
        """`states` is "nice", "bad" or None for levels 1..9 — the tenth has
        no cell of its own, since nine of them are enough to tell ten levels
        apart. `level` is the one being played, or None when the strip has
        never been read."""
        self._states = list(states)[:_LEVELS - 1]
        self._states += [None] * (_LEVELS - 1 - len(self._states))
        self._level = level
        self.update()

    @property
    def level(self) -> int | None:
        return self._level

    # ── Painting ─────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        width = (self.width() - _GAP * (_LEVELS - 1)) / _LEVELS
        for index in range(_LEVELS):
            left = index * (width + _GAP)
            self._chip(painter, QRectF(left, 0, width, _HEIGHT), index + 1)
        painter.end()

    def _chip(self, painter: QPainter, box: QRectF, number: int):
        state = self._states[number - 1] if number <= len(self._states) else None
        playing = self._level == number
        # A level with no cell of its own is only ever "now" or "not yet".
        colour, glyph = _look(state, playing)

        if playing:
            painter.setBrush(QColor(theme.HK_ICE).darker(260))
        elif state is not None:
            painter.setBrush(QColor(colour).darker(340))
        else:
            painter.setBrush(QColor(theme.HK_ELEVATED))
        painter.setPen(QPen(QColor(colour), 1.4 if playing or state else 1.0))
        painter.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5),
                                _RADIUS, _RADIUS)

        painter.setPen(QColor(colour))
        painter.setFont(theme.get_round_font(theme.FONT_SIZE_L, bold=True))
        painter.drawText(
            QRectF(box.left(), box.top() + 3, box.width(),
                   box.height() - _MARK_H - 3),
            Qt.AlignCenter, str(number))

        # Drawn rather than typed: a tick is two strokes in any font that has
        # one and a hollow box in every font that does not, and this widget
        # has to look the same wherever it runs.
        mark = QRectF(box.center().x() - _MARK_W / 2,
                      box.bottom() - _MARK_H + 1, _MARK_W, _MARK_W)
        painter.setPen(QPen(QColor(colour), 2.0, Qt.SolidLine, Qt.RoundCap,
                            Qt.RoundJoin))
        if glyph == "tick":
            painter.drawPolyline([
                QPointF(mark.left(), mark.center().y()),
                QPointF(mark.center().x() - 1, mark.bottom() - 1),
                QPointF(mark.right(), mark.top() + 1)])
        elif glyph == "cross":
            painter.drawLine(mark.topLeft(), mark.bottomRight())
            painter.drawLine(mark.topRight(), mark.bottomLeft())
        elif glyph == "dot":
            painter.setBrush(QColor(colour))
            painter.drawEllipse(mark.center(), 2.6, 2.6)


def _look(state: str | None, playing: bool) -> tuple[str, str]:
    """Colour and mark for one chip. Played levels keep their verdict even
    while another is being played — the run's history is the point."""
    if state == "nice":
        return theme.ACCENT_GREEN, "tick"
    if state == "bad":
        return theme.ACCENT_RED, "cross"
    if playing:
        return theme.HK_ICE, "dot"
    return theme.TEXT_DIM, ""
