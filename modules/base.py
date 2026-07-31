# modules/base.py
from __future__ import annotations
from abc import ABC, abstractmethod


class ModuleBase(ABC):
    name: str        = "Unnamed"
    description: str = ""
    icon: str        = "◈"
    config_key: str  = ""   # field name in AppConfig, e.g. "ava_dancers"

    @abstractmethod
    def create_window(self, config, save_fn, window_manager, parent_overlay=None):
        """Instantiate and return the module's control window (ModuleWindow subclass)."""
        ...
