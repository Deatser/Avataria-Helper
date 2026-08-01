# app/ui/widgets/nt_switch.py
from PySide6.QtWidgets import QAbstractButton
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtCore import (Qt, QRectF, QPointF, QSize, QVariantAnimation,
                            QEasingCurve)
from app.ui import theme

_TRACK_W = 44
_TRACK_H = 22
_KNOB_R  = 8.0
_GAP     = 12   # track → label gap
_SLIDE_MS = 150


class NtSwitch(QAbstractButton):
    """Lever switch: pill track, sliding knob, label on the right."""

    def __init__(self, text: str = "", parent=None, accent: str = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        self.setText(text)
        self._accent = accent or theme.ACCENT
        self._pos    = 0.0   # knob travel, 0 = off, 1 = on

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(_SLIDE_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.valueChanged.connect(self._on_slide)
        self.toggled.connect(self._start_slide)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_checked_silently(self, checked: bool):
        """Restore a stored state without animating or emitting toggled."""
        blocked = self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(blocked)
        self._pos = 1.0 if checked else 0.0
        self.update()

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(_TRACK_W + _GAP + metrics.horizontalAdvance(self.text()) + 2,
                     max(_TRACK_H + 4, metrics.height() + 4))

    # ── Animation ─────────────────────────────────────────────────────────────

    def _start_slide(self, checked: bool):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _on_slide(self, value):
        self._pos = float(value)
        self.update()

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        accent = QColor(self._accent)
        top    = (self.height() - _TRACK_H) / 2
        track  = QRectF(0.5, top + 0.5, _TRACK_W - 1, _TRACK_H - 1)

        # Track: dark when off, accent-tinted when on
        fill = QColor(theme.BG_BUTTON)
        if self._pos:
            tint = QColor(accent)
            tint.setAlpha(int(150 * self._pos))
            painter.setBrush(fill)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(track, _TRACK_H / 2, _TRACK_H / 2)
            painter.setBrush(tint)
        else:
            painter.setBrush(fill)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(track, _TRACK_H / 2, _TRACK_H / 2)

        border = QColor(accent if self._pos > 0.5 else theme.BORDER_BRIGHT)
        border.setAlpha(200 if self._pos > 0.5 else 255)
        painter.setPen(QPen(border, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(track, _TRACK_H / 2, _TRACK_H / 2)

        # Knob
        travel = _TRACK_W - _TRACK_H
        centre = QPointF(_TRACK_H / 2 + travel * self._pos, top + _TRACK_H / 2)
        knob   = QColor(accent) if self._pos > 0.5 else QColor(theme.TEXT_DIM)
        if self.underMouse():
            knob = knob.lighter(120)
        painter.setPen(Qt.NoPen)
        painter.setBrush(knob)
        painter.drawEllipse(centre, _KNOB_R, _KNOB_R)

        # Label
        painter.setPen(QColor(theme.TEXT_PRIMARY if self.isChecked()
                              else theme.TEXT_SECONDARY))
        painter.setFont(self.font())
        painter.drawText(self.rect().adjusted(_TRACK_W + _GAP, 0, 0, 0),
                         Qt.AlignVCenter | Qt.AlignLeft, self.text())
        painter.end()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()
