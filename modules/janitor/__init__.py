# modules/janitor/__init__.py
from app.ui import theme
from modules.base import ModuleBase


class JanitorModule(ModuleBase):
    name        = "Уборщик"
    description = "Уборка мусора в парке"
    icon        = "🧹"
    config_key  = "janitor"
    color       = theme.JN_AMBER
    panel_slot  = "bottom"

    def create_window(self, config, save_fn, window_manager, parent_overlay=None,
                      stats=None):
        from modules.janitor.window import JanitorWindow
        return JanitorWindow(config, save_fn, window_manager, parent_overlay, stats)
