# app/ui/widgets/nt_checkbox.py
from PySide6.QtWidgets import QCheckBox
from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath, QPolygonF
from PySide6.QtCore import Qt, QRectF, QPointF, QSize
from app.ui import theme

_BOX  = 16   # indicator side
_GAP  = 10   # indicator → label gap


class NtCheckBox(QCheckBox):
    """Checkbox drawn in the panel style: rounded box, accent fill, white tick."""

    def __init__(self, text: str = "", parent=None, accent: str = None):
        super().__init__(text, parent)
        self._accent = accent or theme.ACCENT
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        self.toggled.connect(
            lambda: self.repaint() if self.isVisible() else self.update()
        )

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(_BOX + _GAP + fm.horizontalAdvance(self.text()) + 2,
                     max(_BOX + 6, fm.height() + 4))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        accent  = QColor(self._accent)
        checked = self.isChecked()
        hovered = self.underMouse()

        top = (self.height() - _BOX) / 2
        box = QPainterPath()
        box.addRoundedRect(QRectF(0.5, top + 0.5, _BOX, _BOX), 4, 4)

        painter.fillPath(box, accent if checked else QColor(theme.BG_BUTTON))
        if checked:
            bc = accent
        elif hovered:
            bc = QColor(accent); bc.setAlpha(150)
        else:
            bc = QColor(theme.BORDER_BRIGHT)
        painter.setPen(QPen(bc, 1))
        painter.drawPath(box)

        if checked:
            painter.setPen(QPen(QColor(theme.BG_BASE), 2,
                                Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPolyline(QPolygonF([
                QPointF(4.5,  top + 8.5),
                QPointF(7.0,  top + 11.0),
                QPointF(12.0, top + 5.5),
            ]))

        painter.setPen(QColor(theme.TEXT_PRIMARY if hovered else theme.TEXT_SECONDARY))
        painter.setFont(self.font())
        painter.drawText(
            self.rect().adjusted(_BOX + _GAP, 0, 0, 0),
            Qt.AlignVCenter | Qt.AlignLeft,
            self.text(),
        )
        painter.end()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()
