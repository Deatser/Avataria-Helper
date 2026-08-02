# app/ui/settings_panel.py
from __future__ import annotations
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import Qt, QPoint, Signal

from app.core.template_match import FINISH_GOLD, FINISH_SILVER
from app.ui import theme
from app.ui.widgets.nt_panel import NtPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_switch import NtSwitch

_W = 360
_H = 232

_GOLD_TEXT   = "Заканчивать на фарме золота"
_SILVER_TEXT = "Заканчивать на фарме серебра"
_RESTART_TEXT = "Автоматически начинать новую игру"


class SettingsPanel(QWidget):
    """Settings sheet that floats in the middle of its host module window.

    A child widget, not a window: an earlier version was a real top-level
    window re-parented onto the host with SetParent, and it flashed and
    vanished. Qt does not know about a native re-parent, so the first time it
    re-applied its own geometry it used screen coordinates on a window whose
    coordinates had become parent-relative — several hundred pixels outside a
    438x535 parent, i.e. clipped away. As a plain child it simply moves with
    the host, and nothing has to track anything.

    Not modal either: the run keeps going while it is open, and the toggle
    takes effect immediately rather than on an OK button.
    """

    finish_target_changed = Signal(str)    # FINISH_GOLD / FINISH_SILVER
    auto_restart_changed  = Signal(bool)

    def __init__(self, config, save_fn, host: QWidget):
        super().__init__(host)
        self.config   = config
        self.save_fn  = save_fn
        self._drag_at: QPoint | None = None   # grab point, panel-local
        self._moved   = False   # dragged at least once → stop re-centring it

        self.setFixedSize(_W, _H)
        # The sheet drags by its background too, so it says so everywhere its
        # own surface shows; its buttons and switches override this.
        self.setCursor(Qt.SizeAllCursor)
        self._build_ui()
        self.hide()

    # ── UI ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._panel = NtPanel(self, traces=False)
        self._panel.setGeometry(0, 0, self.width(), self.height())

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING + 2, theme.PADDING,
                                  theme.PADDING + 2, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        header = QHBoxLayout()
        title = QLabel("НАСТРОЙКИ")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_S))
        title.setStyleSheet(f"color:{theme.TEXT_PRIMARY}; background:transparent;")
        # The title is the obvious grab handle, same as every other window
        # here. The panel's own bare background drags too — see mousePressEvent.
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent   = self._drag_press
        title.mouseMoveEvent    = self._drag_move
        title.mouseReleaseEvent = self._drag_release
        close_btn = NtButton("×", accent=theme.VW_MAGENTA)
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.hide)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_btn)
        layout.addLayout(header)

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.BORDER_DIM};")
        layout.addWidget(sep)

        caption = QLabel("Когда заканчивать игру")
        caption.setFont(theme.get_mono_font(theme.FONT_SIZE_S, bold=True))
        caption.setStyleSheet(f"color:{theme.TEXT_SECONDARY}; background:transparent;")
        layout.addWidget(caption)

        # One lever, two farms: off is gold — the default, and the currency
        # the module's own start button is named after.
        silver = self.target == FINISH_SILVER
        self._switch = NtSwitch(_SILVER_TEXT if silver else _GOLD_TEXT,
                                accent=theme.VW_CYAN)
        self._switch.set_checked_silently(silver)
        self._switch.toggled.connect(self._on_toggled)
        layout.addWidget(self._switch)

        hint = QLabel("Бот завершит забег, когда увидит эту награду на экране.")
        hint.setWordWrap(True)
        hint.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        hint.setStyleSheet(f"color:{theme.TEXT_DIM}; background:transparent;")
        layout.addWidget(hint)

        self._restart_switch = NtSwitch(_RESTART_TEXT, accent=theme.VW_CYAN)
        self._restart_switch.set_checked_silently(self.auto_restart)
        self._restart_switch.toggled.connect(self._on_restart_toggled)
        layout.addWidget(self._restart_switch)

        restart_hint = QLabel("Выключено — бот только выйдет из забега "
                              "и остановится.")
        restart_hint.setWordWrap(True)
        restart_hint.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        restart_hint.setStyleSheet(f"color:{theme.TEXT_DIM}; background:transparent;")
        layout.addWidget(restart_hint)
        layout.addStretch()

    # ── Placement ────────────────────────────────────────────────────────────

    def centre_on_host(self):
        host = self.parentWidget()
        if host is None:
            return
        self.move(max(0, (host.width()  - self.width())  // 2),
                  max(0, (host.height() - self.height()) // 2))

    def keep_inside_host(self):
        """Called when the host resizes: re-centre, or re-clamp if dragged.

        Snapping a hand-placed panel back to the middle on every resize step
        would fight the user, so once it has been moved it only gets pulled
        back far enough to stay fully inside.
        """
        if self._moved:
            self.move(*self._clamped(self.x(), self.y()))
        else:
            self.centre_on_host()

    def _clamped(self, x: int, y: int) -> tuple[int, int]:
        """Position kept fully inside the host window.

        A host smaller than the panel clamps to 0 rather than to a negative
        limit — better pinned to the top-left corner than parked off-screen.
        """
        host = self.parentWidget()
        if host is None:
            return x, y
        return (max(0, min(x, host.width()  - self.width())),
                max(0, min(y, host.height() - self.height())))

    def toggle(self):
        if self.isVisible():
            self.hide()
            return
        self.keep_inside_host()
        self.raise_()   # above the module's backdrop panel, which is a sibling
        self.show()

    # ── Drag ─────────────────────────────────────────────────────────────────
    # The panel is a child widget, so dragging is plain arithmetic in the
    # host's coordinates — no win32, and the clamp is what keeps it from being
    # dragged out from under its own window.

    def _drag_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_at = event.position().toPoint()

    def _drag_move(self, event):
        if self._drag_at is None or not (event.buttons() & Qt.LeftButton):
            return
        target = (self.pos() + event.position().toPoint() - self._drag_at)
        self.move(*self._clamped(target.x(), target.y()))
        self._moved = True

    def _drag_release(self, event):
        self._drag_at = None

    # Bare panel background: NtPanel does not take mouse events, so they fall
    # through to here and the whole sheet drags, not just its title.
    def mousePressEvent(self, event):
        self._drag_press(event)

    def mouseMoveEvent(self, event):
        self._drag_move(event)

    def mouseReleaseEvent(self, event):
        self._drag_release(event)

    # ── Target ───────────────────────────────────────────────────────────────

    @property
    def target(self) -> str:
        stored = getattr(self.config, "finish_on", FINISH_GOLD)
        return FINISH_SILVER if stored == FINISH_SILVER else FINISH_GOLD

    @property
    def auto_restart(self) -> bool:
        return bool(getattr(self.config, "auto_restart", True))

    def _on_restart_toggled(self, enabled: bool):
        self.config.auto_restart = enabled
        self.save_fn()
        self.auto_restart_changed.emit(enabled)

    def _on_toggled(self, silver: bool):
        target = FINISH_SILVER if silver else FINISH_GOLD
        self.config.finish_on = target
        self.save_fn()
        self._switch.setText(_SILVER_TEXT if silver else _GOLD_TEXT)
        # The host owns the logging — this is its own setting, and it belongs
        # in its own log, not the main one.
        self.finish_target_changed.emit(target)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._panel.setGeometry(0, 0, self.width(), self.height())
