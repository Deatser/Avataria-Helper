# app/ui/widgets/neon_bar.py
"""One filling bar: a caption, a count, a track and a percentage.

The fill is animated toward whatever it is told rather than jumping to it.
Progress arrives one piece of litter at a time — in steps of a tenth or a
thirtieth of the whole — and a bar that snapped between those steps would
read as a stutter rather than as work being done.
"""
from __future__ import annotations

from PySide6.QtCore import (Qt, QEasingCurve, QRectF, QVariantAnimation,
                            Signal)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

from app.ui import theme

# Neon lime, for a bar that has reached the end. Deliberately not any kind's
# own colour: "finished" has to read the same whichever bar it is.
DONE_COLOUR = "#7CFC00"

_ROW_H     = 30
_BAR_H     = 8
_RADIUS    = 4.0
_GLIDE_MS  = 420        # how long a fill from empty to full would take
_MIN_MS    = 120        # ...and the least a single step may take
_LABEL_GAP = 3


class NeonBar(QWidget):
    """A labelled progress track that glides, and lights up when full."""

    filled = Signal()    # reached the end, once
    ROW_H  = _ROW_H      # what one row costs, for whoever makes room for it

    def __init__(self, caption: str, total: int, colour: str, parent=None):
        super().__init__(parent)
        self._caption = caption
        self._total   = max(0, total)
        self._colour  = colour
        self._done    = 0
        self._shown   = 0.0     # the animated fraction actually painted
        self._lit     = False

        self.setFixedHeight(_ROW_H)

        self._glide = QVariantAnimation(self)
        # Out, not InOut: the glide is restarted on every step, so it only
        # ever plays its opening. An easing that opens slowly makes a bar
        # that is fed quickly trail further and further behind the count.
        self._glide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._glide.valueChanged.connect(self._on_glide)

    # ── Public API ───────────────────────────────────────────────────────────

    @property
    def done(self) -> int:
        return self._done

    @property
    def total(self) -> int:
        return self._total

    @property
    def complete(self) -> bool:
        return self._total > 0 and self._done >= self._total

    def set_total(self, total: int):
        """One fewer counted after all — a pick that turned out to be a
        false positive, taken back out of what this bar is measured
        against rather than left to sit as litter that will never clear."""
        self._total = max(0, total)
        self.set_done(self._done)   # re-clamps, re-glides, re-checks complete
        self.update()               # the (done/total) label reads _total directly

    def set_done(self, done: int):
        self._done = max(0, min(done, self._total))
        target = self._fraction()
        self._glide.stop()
        # A one-piece step is a short glide, the jump to full at the end of a
        # run is a long one: distance sets the time, so both move at the same
        # speed rather than both taking the same 420 ms.
        self._glide.setDuration(
            max(_MIN_MS, int(_GLIDE_MS * abs(target - self._shown))))
        self._glide.setStartValue(self._shown)
        self._glide.setEndValue(target)
        self._glide.start()
        if self.complete and not self._lit:
            self._lit = True
            self.filled.emit()

    def _fraction(self) -> float:
        return self._done / self._total if self._total else 1.0

    def _on_glide(self, value):
        self._shown = float(value)
        self.update()

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        colour = QColor(DONE_COLOUR if self._lit else self._colour)

        self._draw_labels(painter, colour)
        self._draw_track(painter, colour)
        painter.end()

    def _draw_labels(self, painter: QPainter, colour: QColor):
        painter.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        top = QRectF(0, 0, self.width(), _ROW_H - _BAR_H - _LABEL_GAP)

        painter.setPen(QColor(theme.TEXT_SECONDARY))
        painter.drawText(top, Qt.AlignLeft | Qt.AlignVCenter,
                         f"{self._caption} ({self._done}/{self._total})")

        # The percentage follows the fill, so it is never ahead of the bar
        painter.setPen(colour if self._lit else QColor(theme.TEXT_PRIMARY))
        painter.drawText(top, Qt.AlignRight | Qt.AlignVCenter,
                         f"{100 * self._shown:3.0f}%")

    def _draw_track(self, painter: QPainter, colour: QColor):
        track = QRectF(0, _ROW_H - _BAR_H, self.width(), _BAR_H)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BG_SURFACE))
        painter.drawRoundedRect(track, _RADIUS, _RADIUS)

        width = track.width() * max(0.0, min(1.0, self._shown))
        if width > 1:
            fill = QRectF(track.left(), track.top(), width, track.height())
            sheen = QLinearGradient(fill.left(), 0, fill.right(), 0)
            dim = QColor(colour); dim.setAlpha(150)
            sheen.setColorAt(0.0, dim)
            sheen.setColorAt(1.0, colour)
            painter.setBrush(sheen)
            painter.drawRoundedRect(fill, _RADIUS, _RADIUS)

            glow = QColor(colour); glow.setAlpha(70)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(glow, 2.5))
            painter.drawRoundedRect(fill, _RADIUS, _RADIUS)

        edge = QColor(colour if self._lit else theme.BORDER)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(edge, 1.6 if self._lit else 1.0))
        painter.drawRoundedRect(track, _RADIUS, _RADIUS)
