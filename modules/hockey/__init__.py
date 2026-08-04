# modules/hockey/__init__.py
from app.ui import theme
from modules.base import ModuleBase


class HockeyModule(ModuleBase):
    name        = "Хоккей"
    description = "Заброс шайбы мимо защитников"
    icon        = "🏒"
    config_key  = "hockey"
    color       = theme.HK_ICE
    panel_slot  = "top"

    def create_window(self, config, save_fn, window_manager, parent_overlay=None,
                      stats=None):
        from modules.hockey.window import HockeyWindow
        return HockeyWindow(config, save_fn, window_manager, parent_overlay)
