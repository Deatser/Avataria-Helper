# modules/snowboard/settings_panel.py
from __future__ import annotations
from PySide6.QtWidgets import QVBoxLayout

from app.ui import theme
from app.ui.sheet_panel import SheetPanel

_W = 340
_H = 180


class SnowboardSettingsPanel(SheetPanel):
    """Empty for now — nothing to switch until the lane-detection side of
    the module exists. Kept as its own sheet, the same shape Садовник's
    and Уборщик's settings have, so real toggles have somewhere to go
    without the window needing to change."""

    def __init__(self, host):
        super().__init__(host, "НАСТРОЙКИ", (_W, _H),
                         accent=theme.SB_STEEL, close_accent=theme.SB_STEEL)

    def _build_body(self, layout: QVBoxLayout):
        layout.addWidget(self.hint(
            "Настройки появятся, когда будет готова логика бота."))
