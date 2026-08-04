# app/ui/widgets/segmented_control.py
"""A row of buttons where exactly one is ever active — press another and
it takes over, the same way NtButton.set_active already lights a single
button up elsewhere in the app, just wired as a group here."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QWidget
from PySide6.QtGui import QColor, QPainter
from PySide6.QtCore import Qt, Signal

from app.ui import theme
from app.ui.widgets.nt_button import NtButton, _BAR_W, _BAR_X, _SMALL_W

# Text glow — same multi-pass halo StatTile's own numbers use. A plain
# QGraphicsDropShadowEffect on the whole button was tried first and
# rejected: NtButton's body is an opaque filled shape, so the effect just
# blurs the button's outer silhouette, not the letters inside it — nothing
# a widget-level shadow can fix, since it has no idea which pixels are
# "the text" and which are "the body" once they're flattened into one
# rendered image.
_GLOW_PASSES = 5
_GLOW_REACH  = 1.4
_GLOW_RING   = ((1, 0), (-1, 0), (0, 1), (0, -1),
               (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7))


class _GlowButton(NtButton):
    """NtButton, with its own label redrawn afterwards through a text
    glow — see the module docstring for why that has to happen here
    rather than through a graphics effect."""

    def __init__(self, text: str, accent: str):
        super().__init__(text, accent=accent, upper=False)
        self._glow_accent = QColor(accent)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setFont(self.font())

        w = self.width()
        if w < _SMALL_W:
            rect = self.rect()
            align = Qt.AlignCenter
        else:
            rect = self.rect().adjusted(_BAR_X + _BAR_W + 11, 0, -12, 0)
            align = Qt.AlignVCenter | Qt.AlignLeft

        for i in range(_GLOW_PASSES, 0, -1):
            glow = QColor(self._glow_accent)
            glow.setAlpha(int(10 * (_GLOW_PASSES - i + 1)))
            painter.setPen(glow)
            reach = i * _GLOW_REACH / _GLOW_PASSES
            for dx, dy in _GLOW_RING:
                painter.drawText(rect.translated(dx * reach, dy * reach),
                                 align, self.text())
        painter.end()


class SegmentedControl(QWidget):
    changed = Signal(int)   # index of the option that just became active

    def __init__(self, options: list[str], accent: str = None,
                active: int = 0, parent=None):
        super().__init__(parent)
        accent = accent or theme.ACCENT
        self._buttons: list[NtButton] = []
        self._active = active

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for i, label in enumerate(options):
            btn = _GlowButton(label, accent)
            btn.setMinimumHeight(28)
            btn.set_active(i == active)
            btn.clicked.connect(lambda _checked=False, idx=i: self._select(idx))
            row.addWidget(btn)
            self._buttons.append(btn)

    def _select(self, idx: int):
        if idx == self._active:
            return
        self._active = idx
        for i, btn in enumerate(self._buttons):
            btn.set_active(i == idx)
        self.changed.emit(idx)

    def active(self) -> int:
        return self._active

    def set_active(self, idx: int):
        """Change the active option without emitting `changed` — for
        restoring a saved choice on build, not for a real switch."""
        self._active = idx
        for i, btn in enumerate(self._buttons):
            btn.set_active(i == idx)
