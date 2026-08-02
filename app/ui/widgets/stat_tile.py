# app/ui/widgets/stat_tile.py
"""A single number worth looking at, in a bracketed card."""
from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient
from PySide6.QtCore import Qt, QRectF

from app.ui import theme

_RADIUS      = 10
_BRACKET     = 14    # length of each corner bracket arm
_BRACKET_PAD = 7     # inset of the brackets from the card edge
_MIN_W       = 96
_MIN_H       = 84


class StatTile(QWidget):
    """Big value, small caption, corner brackets in the tile's own accent.

    The accent is what separates one number from the next at a glance —
    gold amber, silver steel, counts violet — so the value, the brackets and
    the top hairline all take it, and everything else stays neutral.
    """

    def __init__(self, caption: str, value: str = "0",
                 accent: str = None, parent=None):
        super().__init__(parent)
        self._caption = caption
        self._value   = value
        self._accent  = accent or theme.ACCENT
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_value(self, value: str):
        self._value = value
        self.update()

    @property
    def value(self) -> str:
        return self._value

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        accent = QColor(self._accent)
        rect   = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)

        # Body: a touch lighter at the top, so the card reads as lit from above
        body = QLinearGradient(0, 0, 0, self.height())
        body.setColorAt(0.0, QColor(theme.BG_ELEVATED))
        body.setColorAt(1.0, QColor(theme.BG_SURFACE))
        painter.setPen(Qt.NoPen)
        painter.setBrush(body)
        painter.drawRoundedRect(rect, _RADIUS, _RADIUS)

        edge = QColor(theme.BORDER)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(edge, 1))
        painter.drawRoundedRect(rect, _RADIUS, _RADIUS)

        # Accent hairline across the top, fading out at both ends
        line = QLinearGradient(rect.left(), 0, rect.right(), 0)
        faded = QColor(accent); faded.setAlpha(0)
        mid   = QColor(accent); mid.setAlpha(160)
        line.setColorAt(0.0, faded)
        line.setColorAt(0.5, mid)
        line.setColorAt(1.0, faded)
        painter.setPen(QPen(line, 1.4))
        painter.drawLine(rect.left() + _RADIUS, rect.top() + 1.5,
                         rect.right() - _RADIUS, rect.top() + 1.5)

        self._draw_brackets(painter, rect, accent)

        # Value — the reason the tile exists, so it gets the display face
        painter.setPen(QColor(accent))
        painter.setFont(theme.get_display_font(theme.FONT_SIZE_L))
        value_rect = rect.adjusted(6, 10, -6, -rect.height() * 0.34)
        painter.drawText(value_rect, Qt.AlignCenter, self._value)

        painter.setPen(QColor(theme.TEXT_DIM))
        painter.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        caption_rect = rect.adjusted(4, rect.height() * 0.62, -4, -8)
        painter.drawText(caption_rect, Qt.AlignHCenter | Qt.AlignTop,
                         self._caption)
        painter.end()

    def _draw_brackets(self, painter, rect, accent: QColor):
        """Four corner arms — the frame is implied rather than drawn."""
        colour = QColor(accent)
        colour.setAlpha(190)
        painter.setPen(QPen(colour, 1.6, Qt.SolidLine, Qt.FlatCap))
        left, top    = rect.left() + _BRACKET_PAD, rect.top() + _BRACKET_PAD
        right, bottom = rect.right() - _BRACKET_PAD, rect.bottom() - _BRACKET_PAD
        for x, y, dx, dy in ((left, top, 1, 1), (right, top, -1, 1),
                             (left, bottom, 1, -1), (right, bottom, -1, -1)):
            painter.drawLine(x, y, x + _BRACKET * dx, y)
            painter.drawLine(x, y, x, y + _BRACKET * dy)
