# app/ui/widgets/nt_button.py
from PySide6.QtWidgets import QPushButton
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient, QBrush
from PySide6.QtCore import Qt, QRect, QTimer
from app.ui import theme

_SMALL_W = 40   # below this width: center text, no sweep


class NtButton(QPushButton):
    """Nothing-styled button. accent= overrides the active/hover colour."""

    def __init__(self, text: str = "", parent=None, accent: str = None):
        super().__init__(text, parent)
        self._active  = False
        self._hovered = False
        self._accent  = accent or theme.ACCENT_RED

        # Hover sweep state
        self._sweep_x   = -60.0   # current centre x of sweep stripe
        self._sweep_dir = 0       # +1 sweeping right, 0 = idle
        self._sweep_timer = QTimer(self)
        self._sweep_timer.setInterval(14)
        self._sweep_timer.timeout.connect(self._tick_sweep)

        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(34)
        self.setFont(theme.get_mono_font(theme.FONT_SIZE_M))
        # Opaque: we always fill our own background → no parent erase needed
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    # ── Public API ──────────────────────────────────────────────────────────

    def set_active(self, active: bool):
        self._active = active
        self.repaint()   # immediate for reliable visual response

    def set_accent(self, accent: str):
        self._accent = accent
        self.update()

    # ── Hover + sweep ────────────────────────────────────────────────────────

    def enterEvent(self, event):
        super().enterEvent(event)
        self._hovered = True
        if self.width() >= _SMALL_W:
            self._sweep_x   = -60.0
            self._sweep_dir = 1
            self._sweep_timer.start()
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hovered = False
        self._sweep_timer.stop()
        self._sweep_dir = 0
        self.update()

    def _tick_sweep(self):
        self._sweep_x += self.width() * 0.06   # 17 frames to cross
        if self._sweep_x > self.width() + 60:
            self._sweep_timer.stop()
            self._sweep_dir = 0
        self.update()

    # ── Paint ────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect()
        w, h = rect.width(), rect.height()
        small = w < _SMALL_W

        # ── Background ───────────────────────────────────────────────────────
        if self._active:
            bg_str = theme.BG_BUTTON_ACTIVE
        elif self._hovered:
            bg_str = theme.BG_BUTTON_HOVER
        else:
            bg_str = theme.BG_ELEVATED
        painter.fillRect(rect, QColor(bg_str))

        # ── Left accent (active only) ─────────────────────────────────────────
        if self._active and not small:
            for i in range(10, 0, -2):
                glow = QColor(self._accent)
                glow.setAlpha(i * 6)
                painter.fillRect(QRect(0, 0, i * 4, h), glow)
            painter.fillRect(QRect(0, 0, 3, h), QColor(self._accent))
        elif self._hovered and not small:
            c = QColor(self._accent)
            c.setAlpha(60)
            painter.fillRect(QRect(0, 0, 2, h), c)

        # ── Hover sweep stripe ────────────────────────────────────────────────
        if not small and self._sweep_dir and self._sweep_x > -60:
            sx = int(self._sweep_x)
            grad = QLinearGradient(sx - 60, 0, sx + 60, 0)
            grad.setColorAt(0.0, QColor(255, 255, 255, 0))
            grad.setColorAt(0.5, QColor(255, 255, 255, 28))
            grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.NoPen)
            painter.drawRect(max(0, sx - 60), 0, 120, h)

        # ── Text ──────────────────────────────────────────────────────────────
        if self._active:
            text_c = QColor(self._accent)
        elif self._hovered:
            text_c = QColor(theme.TEXT_PRIMARY)
        else:
            text_c = QColor(theme.TEXT_SECONDARY)
        painter.setPen(text_c)
        painter.setFont(self.font())

        if small:
            painter.drawText(rect, Qt.AlignCenter, self.text())
        else:
            indent = 14 if self._active else 10
            painter.drawText(
                rect.adjusted(indent, 0, -8, 0),
                Qt.AlignVCenter | Qt.AlignLeft,
                self.text().upper(),
            )

        # ── Border ────────────────────────────────────────────────────────────
        if self._active:
            bc = QColor(self._accent); bc.setAlpha(80)
        elif self._hovered:
            bc = QColor(theme.BORDER_BRIGHT)
        else:
            bc = QColor(theme.BORDER)
        painter.setPen(QPen(bc, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        painter.end()
