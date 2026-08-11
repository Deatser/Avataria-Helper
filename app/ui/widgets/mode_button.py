# app/ui/widgets/mode_button.py
"""A bracketed card you can press — one of a set, one of them lit.

Built to read as the same family as StatTile rather than as a button:
corner brackets instead of a border, and text that glows in the card's own
accent. Where a StatTile shows a number that happens to be coloured, this
shows a choice whose colour *is* the state — lit accent for the one in
force, a dimmer one for the rest — so a row of these needs no label
explaining which is active.

The brackets are drawn heavier than StatTile's: a tile sits in a grid of
tiles and only has to separate itself from its neighbours, while these have
to hold their own next to full-width buttons above and below them.
"""
from __future__ import annotations

from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient
from PySide6.QtCore import Qt, QRectF, Signal

from app.ui import theme

_RADIUS      = 10
_BRACKET     = 18    # length of each corner bracket arm
_BRACKET_PAD = 6     # inset of the brackets from the card edge
_BRACKET_W   = 2.6   # heavier than StatTile's hairline — see module note

# Text glow, same construction as StatTile's: several low-alpha copies on a
# small ring behind the crisp text. QPainter has no cheap blur for a run of
# text, and this reads the same at these sizes.
_GLOW_PASSES = 5
_GLOW_REACH  = 1.8
_GLOW_RING   = ((1, 0), (-1, 0), (0, 1), (0, -1),
                (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7))

_ALIGN = Qt.AlignCenter | Qt.TextWordWrap


class ModeButton(QWidget):
    """One choice in a row of them. Set `active` on exactly one."""

    clicked = Signal()

    def __init__(self, text: str, accent: str = None, parent=None):
        super().__init__(parent)
        self._text    = text
        self._accent  = accent or theme.ACCENT
        self._active  = False
        self._hovered = False
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setFont(theme.get_mono_font(theme.FONT_SIZE_M, bold=True))

    # ── Public API ───────────────────────────────────────────────────────────

    def set_active(self, active: bool):
        if active == self._active:
            return
        self._active = active
        self.update()

    def set_accent(self, accent: str):
        self._accent = accent
        self.update()

    def setText(self, text: str):
        self._text = text
        self.update()

    def text(self) -> str:
        return self._text

    # ── Mouse ────────────────────────────────────────────────────────────────

    def enterEvent(self, event):
        super().enterEvent(event)
        self._hovered = True
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hovered = False
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        accent = QColor(self._accent)
        rect   = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)

        # Body: lit from above, and washed with the accent while it is the
        # one in force so the card itself carries the state, not just its text.
        body = QLinearGradient(0, 0, 0, self.height())
        top    = QColor(theme.BG_ELEVATED)
        bottom = QColor(theme.BG_SURFACE)
        if self._active:
            top    = _blend(top, accent, 0.16)
            bottom = _blend(bottom, accent, 0.08)
        elif self._hovered:
            top    = _blend(top, accent, 0.08)
            bottom = _blend(bottom, accent, 0.04)
        body.setColorAt(0.0, top)
        body.setColorAt(1.0, bottom)
        painter.setPen(Qt.NoPen)
        painter.setBrush(body)
        painter.drawRoundedRect(rect, _RADIUS, _RADIUS)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawRoundedRect(rect, _RADIUS, _RADIUS)

        self._draw_brackets(painter, rect, accent)

        painter.setFont(self.font())
        text_rect = rect.adjusted(8, 6, -8, -6)
        colour = QColor(accent)
        if not self._active:
            # Dimmed rather than greyed: the choice not taken is still its
            # own colour, just quieter.
            colour.setAlpha(200 if self._hovered else 165)
        self._draw_glow_text(painter, text_rect, self._text, colour)
        painter.end()

    def _draw_brackets(self, painter: QPainter, rect: QRectF, accent: QColor):
        colour = QColor(accent)
        colour.setAlpha(255 if self._active else 200 if self._hovered else 130)
        painter.setPen(QPen(colour, _BRACKET_W, Qt.SolidLine, Qt.FlatCap))
        left,  top    = rect.left() + _BRACKET_PAD, rect.top() + _BRACKET_PAD
        right, bottom = rect.right() - _BRACKET_PAD, rect.bottom() - _BRACKET_PAD
        arm = min(_BRACKET, rect.width() / 3, rect.height() / 3)
        for x, y, dx, dy in ((left, top, 1, 1), (right, top, -1, 1),
                             (left, bottom, 1, -1), (right, bottom, -1, -1)):
            painter.drawLine(x, y, x + arm * dx, y)
            painter.drawLine(x, y, x, y + arm * dy)

    def _draw_glow_text(self, painter: QPainter, rect: QRectF, text: str,
                        colour: QColor):
        strength = 9 if self._active else 5
        for i in range(_GLOW_PASSES, 0, -1):
            glow = QColor(colour)
            glow.setAlpha(int(strength * (_GLOW_PASSES - i + 1)))
            painter.setPen(glow)
            reach = i * _GLOW_REACH / _GLOW_PASSES
            for dx, dy in _GLOW_RING:
                painter.drawText(rect.translated(dx * reach, dy * reach),
                                 _ALIGN, text)
        painter.setPen(colour)
        painter.drawText(rect, _ALIGN, text)


def _blend(base: QColor, tint: QColor, t: float) -> QColor:
    return QColor(
        int(base.red()   + (tint.red()   - base.red())   * t),
        int(base.green() + (tint.green() - base.green()) * t),
        int(base.blue()  + (tint.blue()  - base.blue())  * t),
    )
