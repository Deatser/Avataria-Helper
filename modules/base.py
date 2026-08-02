# modules/base.py
from __future__ import annotations
from abc import ABC, abstractmethod


class ModuleBase(ABC):
    name: str        = "Unnamed"
    description: str = ""
    icon: str        = "◈"
    config_key: str  = ""   # field name in AppConfig, e.g. "ava_dancers"
    # Where the module's button goes in the overlay, relative to the buttons
    # for modules that do not exist yet: "top" above them, "bottom" below.
    panel_slot: str  = "top"

    @abstractmethod
    def create_window(self, config, save_fn, window_manager, parent_overlay=None):
        """Instantiate and return the module's control window (ModuleWindow subclass)."""
        ...
