# modules/gardener/settings_panel.py
from __future__ import annotations
from PySide6.QtWidgets import QVBoxLayout

from app.ui import theme
from app.ui.sheet_panel import SheetPanel

_W = 340
_H = 170


class GardenerSettingsPanel(SheetPanel):
    """Садовник's settings sheet — the frame, waiting for something to hold.

    Deliberately empty rather than filled with switches that do nothing:
    the cleaning routine is not written yet, so there is nothing to
    configure, and a switch that changes no behaviour is worse than a note
    saying so.
    """

    def __init__(self, config, save_fn, host):
        self.config  = config
        self.save_fn = save_fn
        super().__init__(host, "НАСТРОЙКИ", (_W, _H),
                         accent=theme.GD_OLIVE, close_accent=theme.GD_OLIVE)

    def _build_body(self, layout: QVBoxLayout):
        layout.addWidget(self.caption("Уборка"))
        layout.addWidget(self.hint(
            "Настраивать пока нечего — сама уборка ещё не написана. "
            "Здесь появятся её параметры."))
