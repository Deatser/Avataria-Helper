# modules/snowboard/__init__.py
from app.ui import theme
from modules.base import ModuleBase


class SnowboardModule(ModuleBase):
    name        = "Сноуборд"
    description = "Фарм золота на спуске"
    icon        = "🏂"
    config_key  = "snowboard"
    color       = theme.SB_STEEL
    panel_slot  = "top"

    def create_window(self, config, save_fn, window_manager, parent_overlay=None,
                      stats=None):
        from modules.snowboard.window import SnowboardWindow
        return SnowboardWindow(config, save_fn, window_manager, parent_overlay)
