# app/ui/widgets/nt_status_dot.py
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCore import Qt, QRectF
from app.ui import theme

_SIZE       = 20    # widget side — must fit the halo, or it clips to a square
_CORE_RATIO = 0.20  # dot radius as a share of the widget side
_RINGS      = 4     # halo steps between the core and the widget edge


class NtStatusDot(QWidget):
    """Round status indicator with a soft halo for running/stopped states."""

    def __init__(self, parent=None, size: int = _SIZE, accent: str = None,
                 idle: str = None):
        super().__init__(parent)
        self._size   = size
        self._accent = accent or theme.ACCENT_GREEN   # colour of the lit state
        self._idle   = idle or theme.TEXT_DIM
        self._color  = self._idle
        self._glow   = None  # None → no halo
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_size(self, size: int):
        """Rescale with the window — the dot is part of the tile readout."""
        self._size = size
        self.setFixedSize(size, size)
        self.update()

    def flash(self, colour: str): self._set(colour, colour)

    def set_offline(self): self._set(self._idle,       None)
    def set_stopped(self): self._set(theme.ACCENT_RED, theme.ACCENT_RED)
    def set_running(self): self._set(self._accent,     self._accent)
    def set_error(self):   self._set(theme.ACCENT_RED, theme.ACCENT_RED)

    def _set(self, color: str, glow):
        self._color = color
        self._glow  = glow
        # repaint() only while on screen: forcing it on a hidden or dying
        # widget triggers recursive-repaint warnings during teardown
        if self.isVisible():
            self.repaint()
        else:
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        c    = self._size / 2
        core = self._size * _CORE_RATIO

        # Halo: rings grow from the core out to the widget edge, never past it
        if self._glow:
            for i in range(_RINGS, 0, -1):
                r = core + (c - core) * i / _RINGS
                colour = QColor(self._glow)
                colour.setAlpha(10 + 9 * (_RINGS - i))
                painter.setBrush(colour)
                painter.drawEllipse(QRectF(c - r, c - r, r * 2, r * 2))

        painter.setBrush(QColor(self._color))
        painter.drawEllipse(QRectF(c - core, c - core, core * 2, core * 2))
        painter.end()
