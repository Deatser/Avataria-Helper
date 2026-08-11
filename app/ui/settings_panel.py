# app/ui/settings_panel.py
from __future__ import annotations
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtCore import Signal

from app.ui import theme
from app.ui.sheet_panel import SheetPanel
from app.ui.widgets.nt_switch import NtSwitch

_W = 360
_H = 190

_RESTART_TEXT = "Автоматически начинать новую игру"


class SettingsPanel(SheetPanel):
    """Ava Dancers' own settings: what happens after a run, and how the
    screen is read."""

    # Which reward ends a run is no longer here — it is one of three farm
    # modes chosen from the buttons on the module's own window, where it is
    # visible without opening anything.
    auto_restart_changed  = Signal(bool)

    def __init__(self, config, save_fn, host):
        self.config  = config
        self.save_fn = save_fn
        super().__init__(host, "НАСТРОЙКИ", (_W, _H), accent=theme.VW_CYAN)

    # ── Body ─────────────────────────────────────────────────────────────────

    def _build_body(self, layout: QVBoxLayout):
        layout.addWidget(self.caption("После забега"))

        self._restart_switch = NtSwitch(_RESTART_TEXT, accent=theme.VW_CYAN)
        self._restart_switch.set_checked_silently(self.auto_restart)
        self._restart_switch.toggled.connect(self._on_restart_toggled)
        layout.addWidget(self._restart_switch)
        layout.addWidget(self.hint(
            "Выключено — бот только выйдет из забега и остановится."))


    # ── Settings ─────────────────────────────────────────────────────────────

    @property
    def auto_restart(self) -> bool:
        return bool(getattr(self.config, "auto_restart", True))


    def _on_restart_toggled(self, enabled: bool):
        self.config.auto_restart = enabled
        self.save_fn()
        self.auto_restart_changed.emit(enabled)
