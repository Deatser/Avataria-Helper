# app/ui/settings_panel.py
from __future__ import annotations
from PySide6.QtWidgets import QVBoxLayout
from PySide6.QtCore import Signal

from app.core.template_match import FINISH_GOLD, FINISH_SILVER
from app.ui import theme
from app.ui.sheet_panel import SheetPanel
from app.ui.widgets.nt_switch import NtSwitch

_W = 360
_H = 232

_GOLD_TEXT    = "Заканчивать на фарме золота"
_SILVER_TEXT  = "Заканчивать на фарме серебра"
_RESTART_TEXT = "Автоматически начинать новую игру"


class SettingsPanel(SheetPanel):
    """Ava Dancers' own settings: which reward ends a run, and what then."""

    finish_target_changed = Signal(str)    # FINISH_GOLD / FINISH_SILVER
    auto_restart_changed  = Signal(bool)

    def __init__(self, config, save_fn, host):
        self.config  = config
        self.save_fn = save_fn
        super().__init__(host, "НАСТРОЙКИ", (_W, _H), accent=theme.VW_CYAN)

    # ── Body ─────────────────────────────────────────────────────────────────

    def _build_body(self, layout: QVBoxLayout):
        layout.addWidget(self.caption("Когда заканчивать игру"))

        # One lever, two farms: off is gold — the default, and the currency
        # the module's own start button is named after.
        silver = self.target == FINISH_SILVER
        self._switch = NtSwitch(_SILVER_TEXT if silver else _GOLD_TEXT,
                                accent=theme.VW_CYAN)
        self._switch.set_checked_silently(silver)
        self._switch.toggled.connect(self._on_toggled)
        layout.addWidget(self._switch)
        layout.addWidget(self.hint(
            "Бот завершит забег, когда увидит эту награду на экране."))

        self._restart_switch = NtSwitch(_RESTART_TEXT, accent=theme.VW_CYAN)
        self._restart_switch.set_checked_silently(self.auto_restart)
        self._restart_switch.toggled.connect(self._on_restart_toggled)
        layout.addWidget(self._restart_switch)
        layout.addWidget(self.hint(
            "Выключено — бот только выйдет из забега и остановится."))

    # ── Settings ─────────────────────────────────────────────────────────────

    @property
    def target(self) -> str:
        stored = getattr(self.config, "finish_on", FINISH_GOLD)
        return FINISH_SILVER if stored == FINISH_SILVER else FINISH_GOLD

    @property
    def auto_restart(self) -> bool:
        return bool(getattr(self.config, "auto_restart", True))

    def _on_toggled(self, silver: bool):
        target = FINISH_SILVER if silver else FINISH_GOLD
        self.config.finish_on = target
        self.save_fn()
        self._switch.setText(_SILVER_TEXT if silver else _GOLD_TEXT)
        # The host owns the logging — this is its own setting, and it belongs
        # in its own log, not the main one.
        self.finish_target_changed.emit(target)

    def _on_restart_toggled(self, enabled: bool):
        self.config.auto_restart = enabled
        self.save_fn()
        self.auto_restart_changed.emit(enabled)
