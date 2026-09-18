# app/ui/widgets/nt_button.py
from PySide6.QtWidgets import QPushButton
from PySide6.QtGui import (QPainter, QColor, QPen, QLinearGradient, QBrush,
                           QPainterPath)
from PySide6.QtCore import Qt, QRectF, QTimer
from app.ui import theme

_SMALL_W = 40   # below this: centered text, no accent bar, no sweep
_RADIUS  = 8    # corner rounding
_BAR_W   = 3    # left accent bar width
_BAR_X   = 7    # bar inset from the left edge
_BAR_PAD = 8    # bar inset from top/bottom edge

# ── «В разработке» ───────────────────────────────────────────────────────────
# Кнопка мода, за которым ещё ничего нет. Не setEnabled(False): выключенный
# виджет в Qt не получает событий мыши, а значит ни подсветки при наведении,
# ни подсказки — а именно они и должны объяснить, почему тайл не нажимается.
# Поэтому кнопка живая, просто нарисована вполсилы и глотает клики.
WIP_TEXT           = "В разработке"
_WIP_OPACITY       = 0.42   # весь тайл — заметно тусклее соседей
_WIP_HOVER_OPACITY = 0.92   # …кроме надписи под курсором: она загорается

def _blend(base: QColor, tint: QColor, t: float) -> QColor:
    """Mix `tint` into `base` by factor t (0..1)."""
    return QColor(
        int(base.red()   + (tint.red()   - base.red())   * t),
        int(base.green() + (tint.green() - base.green()) * t),
        int(base.blue()  + (tint.blue()  - base.blue())  * t),
    )


class NtButton(QPushButton):
    """
    Nothing-styled button with:
    - solid rounded body (no corner cut-outs)
    - vertical gradient tinted by the accent on hover/active
    - left accent bar + sweep animation on hover
    - accent= custom accent colour
    - upper=False to disable text uppercasing
    - outline=True keeps an accent-coloured border at rest
    - filled=True keeps the accent wash at rest
    - wip=True dims the whole button, swallows clicks and says why on hover
    """

    def __init__(self, text: str = "", parent=None,
                 accent: str = None, upper: bool = True,
                 outline: bool = False, filled: bool = False,
                 wip: bool = False):
        super().__init__(text, parent)
        self._active  = False
        self._hovered = False
        self._accent  = accent or theme.ACCENT
        self._upper   = upper
        self._outline = outline
        self._filled  = filled
        self._wip     = wip

        # Hover sweep
        self._sweep_x     = -60.0
        self._sweep_dir   = 0
        self._sweep_timer = QTimer(self)
        self._sweep_timer.setInterval(14)
        self._sweep_timer.timeout.connect(self._tick_sweep)

        self.setFlat(True)
        self.setCursor(Qt.ArrowCursor if wip else Qt.PointingHandCursor)
        self.setMinimumHeight(34)
        self.setFont(theme.get_mono_font(theme.FONT_SIZE_M))
        if wip:
            self.setToolTip(WIP_TEXT)
            self.setFocusPolicy(Qt.NoFocus)   # и с клавиатуры тоже не нажать

    # ── Public API ────────────────────────────────────────────────────────────

    def set_active(self, active: bool):
        self._active = active
        self._force_repaint()

    def setText(self, text: str):
        # repaint(), not update(): label changes must land even when the window
        # sits inside a foreign parent whose deferred flushes are unreliable.
        super().setText(text)
        self._force_repaint()

    def _force_repaint(self):
        # ...but never on a hidden or dying widget, that warns and can recurse
        if self.isVisible():
            self.repaint()
        else:
            self.update()

    def set_accent(self, accent: str):
        self._accent = accent
        self.update()

    # ── Hover + sweep ─────────────────────────────────────────────────────────

    def enterEvent(self, event):
        super().enterEvent(event)
        self._hovered = True
        if self.width() >= _SMALL_W and not self._wip:
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

    # ── Clicks ────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if self._wip:
            event.accept()   # съеден здесь: ни clicked, ни окно под тайлом
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._wip:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ── Sweep ─────────────────────────────────────────────────────────────────

    def _tick_sweep(self):
        self._sweep_x += self.width() * 0.06
        if self._sweep_x > self.width() + 60:
            self._sweep_timer.stop()
            self._sweep_dir = 0
        self.update()

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h   = self.width(), self.height()
        small  = w < _SMALL_W
        accent = QColor(self._accent)

        # Тайл «в разработке» рисуется тем же кодом, просто вполсилы — он
        # держит своё место и свой цвет на доске, но читается как погасший.
        if self._wip:
            painter.setOpacity(_WIP_OPACITY)

        path = QPainterPath()
        path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), _RADIUS, _RADIUS)

        # ── Body: flat surface, lifted a step on hover / active ───────────────
        base = QColor(theme.BG_BUTTON_HOVER if self._hovered else theme.BG_BUTTON)
        if self._active:
            base = _blend(QColor(theme.BG_BUTTON_HOVER), accent, 0.14)
        elif self._filled:
            base = _blend(base, accent, 0.09)
        painter.fillPath(path, QBrush(base))

        # ── Inner decorations, clipped to the body ────────────────────────────
        painter.save()
        painter.setClipPath(path)

        if not small:
            # Accent wash: smooth alpha falloff from the left edge (no banding)
            if self._active or self._hovered or self._filled:
                peak = 62 if self._active else 34 if self._hovered else 28
                wash = QLinearGradient(0, 0, w * 0.6, 0)
                c0 = QColor(accent); c0.setAlpha(peak)
                c1 = QColor(accent); c1.setAlpha(0)
                wash.setColorAt(0.0, c0)
                wash.setColorAt(1.0, c1)
                painter.fillPath(path, QBrush(wash))

            # Left accent bar — inset pill, the state indicator
            bar = QColor(accent)
            bar.setAlpha(255 if self._active else 190 if self._hovered else 110)
            painter.setPen(Qt.NoPen)
            painter.setBrush(bar)
            painter.drawRoundedRect(
                QRectF(_BAR_X, _BAR_PAD, _BAR_W, h - _BAR_PAD * 2),
                _BAR_W / 2, _BAR_W / 2,
            )

            # Sweep stripe
            if self._sweep_dir and self._sweep_x > -60:
                sx = int(self._sweep_x)
                sweep = QLinearGradient(sx - 70, 0, sx + 70, 0)
                sweep.setColorAt(0.0, QColor(255, 255, 255, 0))
                sweep.setColorAt(0.5, QColor(255, 255, 255, 20))
                sweep.setColorAt(1.0, QColor(255, 255, 255, 0))
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(sweep))
                painter.drawRect(max(0, sx - 70), 0, 140, h)

        painter.restore()

        # ── Text ──────────────────────────────────────────────────────────────
        # Под курсором тайл «в разработке» меняет своё имя на причину, по
        # которой он не нажимается, и она горит ярче остального тайла.
        wip_hover = self._wip and self._hovered
        if wip_hover:
            text_c = QColor(theme.ACCENT_AMBER)
        elif self._active or self._hovered:
            text_c = QColor(theme.ACCENT_WHITE)
        else:
            text_c = QColor(theme.TEXT_PRIMARY)

        painter.save()
        if wip_hover:
            painter.setOpacity(_WIP_HOVER_OPACITY)
        painter.setPen(text_c)
        painter.setFont(self.font())

        label = WIP_TEXT if wip_hover else self.text()
        if small:
            painter.drawText(self.rect(), Qt.AlignCenter, label)
        else:
            display = label.upper() if self._upper else label
            painter.drawText(
                self.rect().adjusted(_BAR_X + _BAR_W + 11, 0, -12, 0),
                Qt.AlignVCenter | Qt.AlignLeft,
                display,
            )
        painter.restore()

        # ── Border ────────────────────────────────────────────────────────────
        # Every button gets its own accent-tinted border now, not just the
        # ones passing outline=True — a flat neutral edge only for hover/
        # active, which already have their own strong solid tint. At rest,
        # a diagonal gradient (bright corner fading to dim) reads as a
        # deliberate accent edge rather than flat paint; outline=True just
        # asks for a brighter version of the same gradient.
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self._active:
            bc = QColor(accent); bc.setAlpha(140)
            pen = QPen(bc, 1)
        elif self._hovered:
            bc = QColor(accent); bc.setAlpha(80)
            pen = QPen(bc, 1)
        else:
            peak = 150 if self._outline else 90
            grad = QLinearGradient(0, 0, w, h)
            c0 = QColor(accent); c0.setAlpha(peak)
            c1 = QColor(accent); c1.setAlpha(int(peak * 0.35))
            grad.setColorAt(0.0, c0)
            grad.setColorAt(1.0, c1)
            pen = QPen(QBrush(grad), 1)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        painter.end()
