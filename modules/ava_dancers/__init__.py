# modules/ava_dancers/__init__.py
from modules.base import ModuleBase


class AvaDancersModule(ModuleBase):
    name        = "Ava Dancers"
    description = "Автоматизация мини-игры с танцами"
    icon        = "◈"
    config_key  = "ava_dancers"
    color       = "#ff00dd"   # VW_MAGENTA — module accent for overlay button

    def create_window(self, config, save_fn, window_manager, parent_overlay=None,
                      stats=None):
        from modules.ava_dancers.window import AvaDancersWindow
        return AvaDancersWindow(config, save_fn, window_manager, parent_overlay, stats)
