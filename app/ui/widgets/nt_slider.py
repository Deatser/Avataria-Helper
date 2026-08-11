# app/ui/widgets/nt_slider.py
"""Dotted slider — a value picked by dragging along a row of ticks.

Painted by hand rather than styling QSlider: every other control in the app
(NtButton, NtSwitch, NtCheckbox) draws itself the same way, and a stylesheet
on a QSlider never quite matches them at the edges.
"""
from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtGui import QPainter, QColor, QPen, QBrush
from PySide6.QtCore import Qt, QPointF, QRectF, Signal

from app.ui import theme

_HEIGHT     = 34
_TRACK_H    = 4
_HANDLE_R   = 9
_HANDLE_HOT = 11    # …while dragged or hovered
_TICK_R     = 2.0
_SIDE       = _HANDLE_HOT + 2   # room for the handle at either end


class NtSlider(QWidget):
    """Value from `minimum` to `maximum`, snapped to `step`.

    valueChanged fires for every change, wherever it came from — dragging,
    a click on the track, the wheel, or set_value() from the outside — so a
    number field next to it can simply follow along.
    """

    valueChanged = Signal(int)

    def __init__(self, minimum: int = 0, maximum: int = 1000,
                 step: int = 10, ticks: int = 11,
                 accent: str = None, parent=None):
        super().__init__(parent)
        self._min    = minimum
        self._max    = max(maximum, minimum + 1)
        self._step   = max(1, step)
        self._ticks  = max(2, ticks)
        self._accent = accent or theme.ACCENT
        self._value  = minimum
        self._hover  = False
        self._drag   = False

        self.setFixedHeight(_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)

    # ── Public API ────────────────────────────────────────────────────────────

    def value(self) -> int:
        return self._value

    def set_value(self, value: int):
        """Clamped to the range and snapped to the step — a number typed in
        by hand goes through here too, which is why over-max simply parks
        the handle at the right end instead of refusing the input."""
        snapped = self._snap(value)
        if snapped == self._value:
            return
        self._value = snapped
        self.update()
        self.valueChanged.emit(snapped)

    def maximum(self) -> int:
        return self._max

    def minimum(self) -> int:
        return self._min

    # ── Value ↔ position ──────────────────────────────────────────────────────

    def _snap(self, value: int) -> int:
        value = max(self._min, min(self._max, int(value)))
        offset = round((value - self._min) / self._step) * self._step
        return max(self._min, min(self._max, self._min + offset))

    def _span(self) -> float:
        return max(1.0, self.width() - _SIDE * 2)

    def _x_of(self, value: int) -> float:
        share = (value - self._min) / (self._max - self._min)
        return _SIDE + share * self._span()

    def _value_at(self, x: float) -> int:
        share = (x - _SIDE) / self._span()
        return self._snap(round(self._min + share * (self._max - self._min)))

    # ── Interaction ───────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._drag = True
        self.set_value(self._value_at(event.position().x()))
        self.update()

    def mouseMoveEvent(self, event):
        if self._drag:
            self.set_value(self._value_at(event.position().x()))

    def mouseReleaseEvent(self, event):
        self._drag = False
        self.update()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._hover = True
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hover = False
        self.update()

    def wheelEvent(self, event):
        notches = event.angleDelta().y() / 120
        if notches:
            self.set_value(self._value + int(notches) * self._step)

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        accent = QColor(self._accent)
        mid_y  = self.height() / 2
        head_x = self._x_of(self._value)

        # Track: the whole run dim, the part already chosen lit
        rest = QColor(theme.BORDER_BRIGHT)
        painter.setPen(Qt.NoPen)
        painter.setBrush(rest)
        painter.drawRoundedRect(
            QRectF(_SIDE, mid_y - _TRACK_H / 2, self._span(), _TRACK_H),
            _TRACK_H / 2, _TRACK_H / 2)

        filled = QColor(accent)
        filled.setAlpha(210)
        painter.setBrush(filled)
        painter.drawRoundedRect(
            QRectF(_SIDE, mid_y - _TRACK_H / 2, head_x - _SIDE, _TRACK_H),
            _TRACK_H / 2, _TRACK_H / 2)

        # Ticks: the dots one drags between
        for index in range(self._ticks):
            x = _SIDE + self._span() * index / (self._ticks - 1)
            passed = x <= head_x + 0.5
            dot = QColor(accent) if passed else QColor(theme.TEXT_DIM)
            dot.setAlpha(255 if passed else 200)
            painter.setBrush(dot)
            painter.drawEllipse(QPointF(x, mid_y), _TICK_R, _TICK_R)

        # Handle: a lit disc with a soft halo while it is being worked
        radius = _HANDLE_HOT if (self._hover or self._drag) else _HANDLE_R
        if self._hover or self._drag:
            halo = QColor(accent)
            halo.setAlpha(55)
            painter.setBrush(halo)
            painter.drawEllipse(QPointF(head_x, mid_y), radius + 5, radius + 5)

        painter.setBrush(QBrush(QColor(theme.BG_ELEVATED)))
        painter.setPen(QPen(accent, 2))
        painter.drawEllipse(QPointF(head_x, mid_y), radius, radius)

        core = QColor(accent)
        core.setAlpha(230 if (self._hover or self._drag) else 170)
        painter.setPen(Qt.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(QPointF(head_x, mid_y), radius * 0.42, radius * 0.42)
        painter.end()
