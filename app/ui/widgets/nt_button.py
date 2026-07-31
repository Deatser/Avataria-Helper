# app/ui/widgets/nt_button.py
from PySide6.QtWidgets import QPushButton
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtCore import Qt, QRect
from app.ui import theme


class NtButton(QPushButton):
    """Nothing-styled button with neon glow on active state and hover repaint."""

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

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
        rect = self.rect()
        w, h = rect.width(), rect.height()
        hovered = self.underMouse()

        # Background
        if self._active:
            bg = QColor(theme.BG_BUTTON_ACTIVE)
        elif hovered:
            bg = QColor(theme.BG_BUTTON_HOVER)
        else:
            bg = QColor(theme.BG_ELEVATED)
        painter.fillRect(rect, bg)

        # Left accent
        if self._active:
            # Neon glow: semi-transparent layers spreading right
            for i in range(10, 0, -2):
                glow = QColor(theme.ACCENT_RED)
                glow.setAlpha(i * 6)
                painter.fillRect(QRect(0, 0, i * 4, h), glow)
            # Solid 3px bar
            painter.fillRect(QRect(0, 0, 3, h), QColor(theme.ACCENT_RED))
        elif hovered:
            c = QColor(theme.BORDER_BRIGHT)
            c.setAlpha(180)
            painter.fillRect(QRect(0, 0, 2, h), c)

        # Text
        if self._active:
            text_color = QColor(theme.ACCENT_RED)
        elif hovered:
            text_color = QColor(theme.TEXT_PRIMARY)
        else:
            text_color = QColor(theme.TEXT_SECONDARY)
        painter.setPen(text_color)
        painter.setFont(self.font())
        indent = 14 if self._active else 10
        painter.drawText(
            rect.adjusted(indent, 0, -8, 0),
            Qt.AlignVCenter | Qt.AlignLeft,
            self.text().upper(),
        )

        # Border
        if self._active:
            border_c = QColor(theme.ACCENT_RED)
            border_c.setAlpha(80)
        elif hovered:
            border_c = QColor(theme.BORDER_BRIGHT)
        else:
            border_c = QColor(theme.BORDER)
        painter.setPen(QPen(border_c, 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

        painter.end()
