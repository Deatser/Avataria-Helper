# modules/hockey/settings_panel.py
from __future__ import annotations
from PySide6.QtWidgets import QVBoxLayout

from app.ui import theme
from app.ui.sheet_panel import SheetPanel

_W = 340
_H = 180


class HockeySettingsPanel(SheetPanel):
    """Empty for now — nothing to switch until the shot side of the
    module exists. Kept as its own sheet, the same shape every other
    module's settings have, so real toggles have somewhere to go without
    the window needing to change."""

    def __init__(self, host):
        super().__init__(host, "НАСТРОЙКИ", (_W, _H),
                         accent=theme.HK_ICE, close_accent=theme.HK_ICE)

    def _build_body(self, layout: QVBoxLayout):
        layout.addWidget(self.hint(
            "Настройки появятся, когда будет готова логика бота."))
