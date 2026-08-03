# modules/janitor/settings_panel.py
from __future__ import annotations
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout

from app.ui import theme
from app.ui.sheet_panel import SheetPanel
from app.ui.widgets.nt_switch import NtSwitch

_W = 340
_H = 300

_KIND_BUTTONS_TEXT = "Кнопки по видам мусора"
_AUTO_CLEAN_TEXT   = "Автоматически запускать уборку"


class JanitorSettingsPanel(SheetPanel):
    """Уборщик's settings sheet — the same two toggles Садовник's has."""

    kind_buttons_toggled = Signal(bool)

    def __init__(self, config, save_fn, host):
        self.config  = config
        self.save_fn = save_fn
        super().__init__(host, "НАСТРОЙКИ", (_W, _H),
                         accent=theme.JN_AMBER, close_accent=theme.JN_AMBER)

    def _build_body(self, layout: QVBoxLayout):
        layout.addWidget(self.caption("Отладка"))

        self._kind_switch = NtSwitch(_KIND_BUTTONS_TEXT, accent=theme.JN_AMBER)
        self._kind_switch.set_checked_silently(self.show_kind_buttons)
        self._kind_switch.toggled.connect(self._on_kind_buttons_toggled)
        layout.addWidget(self._kind_switch)
        layout.addWidget(self.hint(
            "Отдельная кнопка на каждый вид мусора — сколько его и с каким "
            "процентом совпадения найдено прямо сейчас на экране."))

        layout.addWidget(self.caption("Автозапуск"))

        self._auto_switch = NtSwitch(_AUTO_CLEAN_TEXT, accent=theme.JN_AMBER)
        self._auto_switch.set_checked_silently(self.auto_clean)
        self._auto_switch.toggled.connect(self._on_auto_clean_toggled)
        layout.addWidget(self._auto_switch)
        layout.addWidget(self.hint(
            "Бот будет следить, когда парк снова станет доступен для "
            "уборки, и в этот момент сам переключится на Уборщика и "
            "начнёт уборку — где бы аватар ни находился."))

    # ── Settings ─────────────────────────────────────────────────────────────

    @property
    def show_kind_buttons(self) -> bool:
        return bool(getattr(self.config, "show_kind_buttons", False))

    def _on_kind_buttons_toggled(self, enabled: bool):
        self.config.show_kind_buttons = enabled
        self.save_fn()
        self.kind_buttons_toggled.emit(enabled)

    @property
    def auto_clean(self) -> bool:
        return bool(getattr(self.config, "auto_clean", False))

    def _on_auto_clean_toggled(self, enabled: bool):
        self.config.auto_clean = enabled
        self.save_fn()
