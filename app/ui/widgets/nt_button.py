# app/ui/widgets/nt_button.py
from PySide6.QtWidgets import QPushButton
from PySide6.QtGui import QPainter, QColor
from PySide6.QtCore import Qt, QRect
from app.ui import theme


class NtButton(QPushButton):
    """Nothing-styled flat button. Call set_active(True) for red accent state."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._active = False
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(34)
        self.setFont(theme.get_mono_font(theme.FONT_SIZE_M))
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    def set_active(self, active: bool):
        self._active = active
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect()

        # Background
        if self._active:
            bg = QColor("#180000")
        elif self.underMouse():
            bg = QColor("#242424")
        else:
            bg = QColor(theme.BG_ELEVATED)
        painter.fillRect(rect, bg)

        # Active: 3px red left bar
        if self._active:
            painter.fillRect(QRect(0, 0, 3, rect.height()), QColor(theme.ACCENT_RED))

        # Text
        text_color = QColor(theme.ACCENT_RED) if self._active else QColor(theme.TEXT_PRIMARY)
        painter.setPen(text_color)
        painter.setFont(self.font())
        text_rect = rect.adjusted(14 if self._active else 10, 0, -8, 0)
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text().upper())

        # 1px border
        painter.setPen(QColor(theme.BORDER))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        painter.end()
