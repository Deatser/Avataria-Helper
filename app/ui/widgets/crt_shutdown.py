# app/ui/widgets/crt_shutdown.py
"""The way a CRT television switches off, borrowed for closing a window.

Three beats, one after the other:

  1. the picture is crushed vertically into a bright horizontal band, the
     way the electron beam gives up its vertical sweep first;
  2. the band, now a line, is pulled in from both ends toward the middle;
  3. what is left is a dot of afterglow that fades out.

Drawn from a snapshot of the window rather than by shrinking the window
itself: relayouting the real widgets forty times a second would fight the
animation and look like a stutter, and a frozen picture is exactly what a
dying tube shows anyway.
"""
from __future__ import annotations

import time

from PySide6.QtCore import (Qt, QEasingCurve, QPointF, QRectF, QTimer,
                            QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

DURATION_MS = 520

# Switching on is quicker than switching off — a tube warms up in a snap and
# dies slowly, and a window you asked for should not keep you waiting.
POWER_ON_MS = 380

# How long the switch-on will hold for a backdrop that is still loading, and
# how often it asks. The cap matters more than the interval: a video that
# never decodes must not leave the window invisible forever.
READY_TIMEOUT_MS = 900
READY_POLL_MS    = 25

# Where each beat ends, as a share of the whole run.
_P_COLLAPSE = 0.50
_P_LINE     = 0.82

_LINE_H   = 3.0    # thickness the picture never squeezes below
_GLOW     = QColor(255, 255, 255)
_TINT     = QColor(210, 235, 255)   # the cold white of a phosphor tube
_DOT_R    = 4.0
_DOT_BLOOM = 26.0


class CrtShutdown(QWidget):
    """Covers its parent window and plays the switch-off over a snapshot."""

    finished = Signal()

    def __init__(self, target: QWidget):
        super().__init__(target)
        # Both before the grab, and in this order: grab() repaints the whole
        # tree, this widget included, so it has to be out of the picture and
        # already able to paint itself if it is asked to.
        self._t = 0.0
        self._hidden: list[QWidget] = []
        self.hide()
        self._snapshot = target.grab()

        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setGeometry(0, 0, target.width(), target.height())

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(DURATION_MS)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_tick)
        self._anim.finished.connect(self.finished.emit)

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self):
        self._take_over()
        self._anim.start()

    def _take_over(self):
        """Put the snapshot on screen in place of the live widgets.

        Which ones were up is remembered rather than assumed — a window has
        children that are deliberately hidden, the settings sheet among
        them, and handing the window back must not put those on screen.
        """
        self._hidden = [child for child in self.parentWidget().children()
                        if isinstance(child, QWidget) and child is not self
                        and child.isVisible()]
        for child in self._hidden:
            child.hide()
        self.raise_()
        self.show()

    def _retake_snapshot(self):
        """Photograph the window again, without anyone seeing it happen.

        The widgets come back, the picture is taken and they go away again,
        all inside one call — nothing returns to the event loop in between,
        so no half-dressed frame ever reaches the screen.
        """
        hidden = list(self._hidden)
        self.hide()
        for child in hidden:
            child.show()
        self._snapshot = self.parentWidget().grab()
        for child in hidden:
            child.hide()
        self.raise_()
        self.show()

    def _on_tick(self, value):
        self._t = float(value)
        self.update()

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        if self._t < _P_COLLAPSE:
            self._paint_collapse(painter, self._t / _P_COLLAPSE)
        elif self._t < _P_LINE:
            span = (self._t - _P_COLLAPSE) / (_P_LINE - _P_COLLAPSE)
            self._paint_line(painter, span)
        else:
            self._paint_dot(painter, (self._t - _P_LINE) / (1 - _P_LINE))
        painter.end()

    def _paint_collapse(self, painter: QPainter, raw: float):
        """The picture, squeezed toward its own middle and blowing out white."""
        k = QEasingCurve(QEasingCurve.Type.InCubic).valueForProgress(raw)
        height = max(_LINE_H, self.height() * (1.0 - k))
        band   = QRectF(0, (self.height() - height) / 2, self.width(), height)

        painter.drawPixmap(band, self._snapshot,
                           QRectF(self._snapshot.rect()))

        # As the sweep collapses the same light lands on less and less glass
        wash = QColor(_TINT)
        wash.setAlpha(int(150 * k))
        painter.fillRect(band, wash)

        edge = QColor(_GLOW)
        edge.setAlpha(int(220 * k))
        painter.setPen(QPen(edge, 1.2))
        painter.drawLine(QPointF(band.left(), band.top()),
                         QPointF(band.right(), band.top()))
        painter.drawLine(QPointF(band.left(), band.bottom()),
                         QPointF(band.right(), band.bottom()))

    def _paint_line(self, painter: QPainter, raw: float):
        """A bright bar drawn in from both ends."""
        # Even in the middle, quick at both ends — OutCubic ate most of the
        # width in the first few frames and left the rest crawling.
        k    = QEasingCurve(QEasingCurve.Type.InOutCubic).valueForProgress(raw)
        half = (self.width() / 2) * (1.0 - k)
        mid  = self.height() / 2
        cx   = self.width() / 2

        # Three passes: a wide dim bloom, a tighter one, then the core
        for spread, alpha in ((5.0, 45), (2.4, 110), (0.0, 255)):
            colour = QColor(_GLOW if spread == 0.0 else _TINT)
            colour.setAlpha(alpha)
            bar = QRectF(cx - half, mid - _LINE_H / 2 - spread,
                         half * 2, _LINE_H + spread * 2)
            painter.setPen(Qt.NoPen)
            painter.setBrush(colour)
            painter.drawRoundedRect(bar, bar.height() / 2, bar.height() / 2)

    def _paint_dot(self, painter: QPainter, raw: float):
        """The last of the charge, bleeding off the phosphor."""
        fade   = max(0.0, 1.0 - raw)
        centre = QPointF(self.width() / 2, self.height() / 2)

        bloom = QRadialGradient(centre, _DOT_BLOOM)
        inner = QColor(_TINT); inner.setAlpha(int(150 * fade))
        outer = QColor(_TINT); outer.setAlpha(0)
        bloom.setColorAt(0.0, inner)
        bloom.setColorAt(1.0, outer)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bloom)
        painter.drawEllipse(centre, _DOT_BLOOM, _DOT_BLOOM)

        core = QColor(_GLOW)
        core.setAlpha(int(255 * fade))
        painter.setBrush(core)
        radius = _DOT_R * fade
        painter.drawEllipse(centre, radius, radius)


class CrtPowerOn(CrtShutdown):
    """The same three beats played backwards, for a window opening.

    Struck dot, line thrown out to full width, picture unfolding from it —
    which is a tube finding its sweep again. Unlike the switch-off this one
    has to clean up after itself: the window stays, so the live widgets come
    back and the effect takes itself off the screen.
    """

    def __init__(self, target: QWidget):
        super().__init__(target)
        self._t = 1.0   # so the very first frame is the dot, not the picture
        self._anim.setDuration(POWER_ON_MS)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self._hand_back)
        self._ready = None
        self._deadline = 0.0

    def start(self, ready=None):
        """Play now, or hold the screen dark until `ready()` says go.

        A window whose backdrop is a video has nothing worth photographing
        for the first moments of its life — the panel is still drawing its
        fallback scene — and unfolding that into view is exactly the stale
        picture this animation is supposed to reveal. Waiting costs nothing
        visible: until the animation runs, the first beat of it is an unlit
        screen anyway.
        """
        self._take_over()
        if ready is None or ready():
            self._anim.start()
            return
        self._ready    = ready
        self._deadline = time.monotonic() + READY_TIMEOUT_MS / 1000
        self._wait_for_ready()

    def _wait_for_ready(self):
        if self._ready() or time.monotonic() >= self._deadline:
            # Whatever it looks like now is the best there is going to be
            self._retake_snapshot()
            self._anim.start()
            return
        QTimer.singleShot(READY_POLL_MS, self._wait_for_ready)

    def _hand_back(self):
        for child in self._hidden:
            child.show()
        self._hidden = []
        self.hide()
        self.deleteLater()
