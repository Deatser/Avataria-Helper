# app/ui/widgets/neon_section.py
"""A titled group of rows in a neon-bordered, slightly darkened card.

For grouping several modules together under one label — "Игры" holding
Ava Dancers/Хоккей/Сноуборд, "Профессии" holding Уборщик/Садовник — inside
StatsWindow, on top of whatever backdrop (video, photo, or the panel's own
drawn scene) sits behind it.
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtCore import Qt, QRectF

from app.ui import theme

_RADIUS      = 14
_TITLE_H     = 30    # strip reserved at the top for the section's own label
_FILL_ALPHA  = 130   # a wash, not a solid card — the backdrop behind the
                     # stats window should still read through
_GLOW_PASSES = 4


class NeonSection(QWidget):
    """Content goes into `.body()`, a QVBoxLayout reserved below the title
    strip painted along the card's own top edge."""

    def __init__(self, title: str, accent: str, parent=None):
        super().__init__(parent)
        self._title  = title
        self._accent = accent
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        self._body = QVBoxLayout()
        self._body.setSpacing(theme.SPACING)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, _TITLE_H + 6, 14, 14)
        outer.addLayout(self._body)

    def body(self) -> QVBoxLayout:
        return self._body

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        rect = QRectF(1, 1, w - 2, h - 2)
        accent = QColor(self._accent)

        # Slight darkening — a wash under the content, not a solid fill,
        # so a video or photo backdrop behind the card still reads through.
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(6, 4, 14, _FILL_ALPHA))
        painter.drawRoundedRect(rect, _RADIUS, _RADIUS)

        # Neon edge: a soft halo widening outward under a crisp core line —
        # the same glow trick StatTile's corner brackets use, just closed
        # into a full border here.
        for i in range(_GLOW_PASSES, 0, -1):
            glow = QColor(accent)
            glow.setAlpha(10 * (_GLOW_PASSES - i + 1))
            painter.setPen(QPen(glow, i * 2.2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect, _RADIUS, _RADIUS)
        painter.setPen(QPen(accent, 1.4))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, _RADIUS, _RADIUS)

        # Title sits directly on the top edge, like a section tab
        painter.setPen(QColor(accent))
        painter.setFont(theme.get_display_font(theme.FONT_SIZE_S))
        title_rect = QRectF(18, 2, w - 36, _TITLE_H)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter,
                         self._title.upper())
        painter.end()
