# modules/ava_dancers/__init__.py
from modules.base import ModuleBase


class AvaDancersModule(ModuleBase):
    name        = "Ava Dancers"
    description = "Автоматизация мини-игры с танцами"
    icon        = "◈"
    config_key  = "ava_dancers"   # matches AppConfig.ava_dancers field

    def create_window(self, config, save_fn, window_manager, parent_overlay=None):
        from modules.ava_dancers.window import AvaDancersWindow
        return AvaDancersWindow(config, save_fn, window_manager, parent_overlay)
