# app/ui/widgets/nt_status_dot.py
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCore import Qt
from app.ui import theme

_OFFLINE = "#2a2a2a"
_RUNNING = theme.ACCENT_GREEN
_ERROR   = theme.ACCENT_RED


class NtStatusDot(QWidget):
    """8x8 colored status indicator dot."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = _OFFLINE
        self.setFixedSize(8, 8)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_offline(self): self._set(_OFFLINE)
    def set_running(self): self._set(_RUNNING)
    def set_error(self):   self._set(_ERROR)

    def _set(self, color: str):
        self._color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(0, 0, 8, 8)
        painter.end()
