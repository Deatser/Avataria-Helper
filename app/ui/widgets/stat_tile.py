# app/ui/widgets/stat_tile.py
"""A single number worth looking at, in a bracketed card."""
import random

from PySide6.QtWidgets import QWidget, QSizePolicy
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient
from PySide6.QtCore import Qt, QRectF, QTimer

from app.ui import theme

_RADIUS      = 10
_BRACKET     = 14    # length of each corner bracket arm
_BRACKET_PAD = 7     # inset of the brackets from the card edge
_MIN_W       = 96
_MIN_H       = 84

# Text glow — both the value and the caption get one, see _draw_glow_text.
_GLOW_PASSES = 5
_GLOW_REACH  = 1.6   # px the outermost pass's ring sits out at
_GLOW_RING   = ((1, 0), (-1, 0), (0, 1), (0, -1),
               (0.7, 0.7), (-0.7, 0.7), (0.7, -0.7), (-0.7, -0.7))

# Same noise LogPanel's own reveal uses — one scramble look across the app,
# not a tile-specific one.
_SCRAMBLE = "!@#$%^&*<>?/|[]{}~+=_-"

# Frame count is duration ÷ interval rather than tied to the value's own
# length (a 1-digit change would otherwise finish in well under the full
# span), so every tile takes the same 0.3s regardless of how many digits
# it's revealing.
_SCRAMBLE_DURATION_MS = 300
_SCRAMBLE_INTERVAL_MS = 40


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
        self._display = value   # what paintEvent draws — differs from
                                 # _value only mid-animation
        self._anim_gen = 0      # bumped on every set/animate, so a stale
                                 # scheduled frame from a superseded
                                 # animation knows to no-op instead of
                                 # painting over a newer value
        self._accent  = accent or theme.ACCENT
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_value(self, value: str):
        self._anim_gen += 1
        self._value   = value
        self._display = value
        self.update()

    def animate_value(self, value: str):
        """Reveal a changed value left-to-right through scramble noise —
        the same reveal LogPanel does for a new line, so a number that just
        moved catches the eye instead of silently jumping."""
        if value == self._value:
            return
        self._anim_gen += 1
        gen = self._anim_gen
        self._value = value
        total_frames = max(1, _SCRAMBLE_DURATION_MS // _SCRAMBLE_INTERVAL_MS)

        def tick(f):
            if gen != self._anim_gen:
                return   # superseded by a newer value before this frame ran
            revealed = int((f / total_frames) * len(value))
            self._display = value[:revealed] + "".join(
                random.choice(_SCRAMBLE) for _ in value[revealed:])
            self.update()
            if f < total_frames:
                QTimer.singleShot(_SCRAMBLE_INTERVAL_MS, lambda: tick(f + 1))

        tick(1)

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
        painter.setFont(theme.get_display_font(theme.FONT_SIZE_L))
        value_rect = rect.adjusted(6, 10, -6, -rect.height() * 0.34)
        self._draw_glow_text(painter, value_rect, self._display, accent,
                            Qt.AlignCenter)

        painter.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        caption_rect = rect.adjusted(4, rect.height() * 0.62, -4, -8)
        self._draw_glow_text(painter, caption_rect, self._caption,
                            QColor(theme.TEXT_PRIMARY),
                            Qt.AlignHCenter | Qt.AlignTop)
        painter.end()

    def _draw_glow_text(self, painter: QPainter, rect: QRectF, text: str,
                        colour: QColor, align):
        """A soft halo behind the crisp text — several low-alpha copies at
        a small ring of offsets, the same "many passes, rising alpha"
        trick NeonSection's own border glow uses, just applied to drawn
        text instead of a stroked rectangle rather than a real Gaussian
        blur (QPainter has no cheap way to blur a run of text on its
        own)."""
        for i in range(_GLOW_PASSES, 0, -1):
            glow = QColor(colour)
            glow.setAlpha(int(6 * (_GLOW_PASSES - i + 1)))
            painter.setPen(glow)
            reach = i * _GLOW_REACH / _GLOW_PASSES
            for dx, dy in _GLOW_RING:
                painter.drawText(rect.translated(dx * reach, dy * reach),
                                 align, text)
        painter.setPen(QColor(colour))
        painter.drawText(rect, align, text)

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
