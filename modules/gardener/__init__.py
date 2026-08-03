# modules/gardener/__init__.py
from app.ui import theme
from modules.base import ModuleBase


class GardenerModule(ModuleBase):
    name        = "Садовник"
    description = "Уход за садом"
    icon        = "❦"
    config_key  = "gardener"
    color       = theme.GD_OLIVE
    panel_slot  = "bottom"   # sits under the not-yet-built placeholders

    def create_window(self, config, save_fn, window_manager, parent_overlay=None,
                      stats=None):
        from modules.gardener.window import GardenerWindow
        return GardenerWindow(config, save_fn, window_manager, parent_overlay, stats)
