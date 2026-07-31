# app/ui/widgets/nt_status_dot.py
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCore import Qt
from app.ui import theme


class NtStatusDot(QWidget):
    """16×16 status dot with glow halo for running/error states."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = theme.TEXT_DIM
        self._glow  = None  # None → no halo (offline)
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_offline(self): self._set(theme.TEXT_DIM,    None)
    def set_running(self): self._set(theme.ACCENT_GREEN, theme.ACCENT_GREEN)
    def set_error(self):   self._set(theme.ACCENT_RED,   theme.ACCENT_RED)

    def _set(self, color: str, glow):
        self._color = color
        self._glow  = glow
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        cx, cy = 8, 8

        # Glow halos
        if self._glow:
            for i in range(5, 0, -1):
                c = QColor(self._glow)
                c.setAlpha(i * 18)
                r = 4 + i * 2
                painter.setBrush(c)
                painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Core dot
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(cx - 4, cy - 4, 8, 8)
        painter.end()
