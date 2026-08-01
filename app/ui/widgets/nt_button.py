# app/ui/widgets/nt_button.py
from PySide6.QtWidgets import QPushButton
from PySide6.QtGui import QPainter, QColor, QPen, QLinearGradient, QBrush, QPolygon, QRegion
from PySide6.QtCore import Qt, QRect, QPoint, QTimer
from app.ui import theme

_SMALL_W = 40   # below this: center text, no chamfer, no sweep
_CUT     = 7    # chamfer size in pixels


class NtButton(QPushButton):
    """
    Nothing-styled button with:
    - chamfered (angled) corners
    - sweep animation on hover
    - accent= custom active/hover colour
    - upper=False to disable text uppercasing
    """

    def __init__(self, text: str = "", parent=None,
                 accent: str = None, upper: bool = True):
        super().__init__(text, parent)
        self._active  = False
        self._hovered = False
        self._accent  = accent or theme.ACCENT_RED
        self._upper   = upper

        # Hover sweep
        self._sweep_x     = -60.0
        self._sweep_dir   = 0
        self._sweep_timer = QTimer(self)
        self._sweep_timer.setInterval(14)
        self._sweep_timer.timeout.connect(self._tick_sweep)

        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(34)
        self.setFont(theme.get_mono_font(theme.FONT_SIZE_M))
        # Always fill own bg → no parent erase artefacts on transparent windows
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_active(self, active: bool):
        self._active = active
        self.repaint()

    def set_accent(self, accent: str):
        self._accent = accent
        self.update()

    # ── Hover + sweep ─────────────────────────────────────────────────────────

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
        self._sweep_x += self.width() * 0.06
        if self._sweep_x > self.width() + 60:
            self._sweep_timer.stop()
            self._sweep_dir = 0
        self.update()

    # ── Geometry helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _chamfer(w: int, h: int, c: int) -> QPolygon:
        return QPolygon([
            QPoint(c,     0),     QPoint(w-1-c, 0),
            QPoint(w-1,   c),     QPoint(w-1,   h-1-c),
            QPoint(w-1-c, h-1),   QPoint(c,     h-1),
            QPoint(0,     h-1-c), QPoint(0,     c),
        ])

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect()
        w, h = rect.width(), rect.height()
        small = w < _SMALL_W
        cut = 0 if small else _CUT

        poly = self._chamfer(w, h, cut)

        # ── Clip all fills to chamfer shape ───────────────────────────────────
        painter.setClipRegion(QRegion(poly))
        painter.setRenderHint(QPainter.Antialiasing, False)

        # Background
        if self._active:
            bg = QColor(theme.BG_BUTTON_ACTIVE)
        elif self._hovered:
            bg = QColor(theme.BG_BUTTON_HOVER)
        else:
            bg = QColor(theme.BG_ELEVATED)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawPolygon(poly)

        # Left accent
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

        # Sweep stripe
        if not small and self._sweep_dir and self._sweep_x > -60:
            sx = int(self._sweep_x)
            grad = QLinearGradient(sx - 60, 0, sx + 60, 0)
            grad.setColorAt(0.0, QColor(255, 255, 255, 0))
            grad.setColorAt(0.5, QColor(255, 255, 255, 28))
            grad.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.setBrush(QBrush(grad))
            painter.drawRect(max(0, sx - 60), 0, 120, h)

        # Text
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
            display = self.text().upper() if self._upper else self.text()
            indent  = 14 if self._active else 10
            painter.drawText(
                rect.adjusted(indent, 0, -8, 0),
                Qt.AlignVCenter | Qt.AlignLeft,
                display,
            )

        # ── Border on chamfer outline (no clip) ───────────────────────────────
        painter.setClipping(False)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self._active:
            bc = QColor(self._accent); bc.setAlpha(80)
        elif self._hovered:
            bc = QColor(theme.BORDER_BRIGHT)
        else:
            bc = QColor(theme.BORDER)
        painter.setPen(QPen(bc, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(poly)

        painter.end()
