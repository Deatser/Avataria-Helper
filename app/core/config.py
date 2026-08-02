# app/core/config.py
from __future__ import annotations
from dataclasses import dataclass, field, asdict
import json
from pathlib import Path


@dataclass
class OverlayConfig:
    x: int = 10
    y: int = 10
    width: int = 330
    height: int = 1050
    opacity: int = 220
    skip_close_confirm: bool = False   # "Не спрашивать снова" on the close dialog


@dataclass
class AvaDancersConfig:
    favorite: bool = False
    position_saved: bool = False
    x: int = 370
    y: int = 10
    width: int = 420
    height: int = 330
    # Detection thresholds — see modules.ava_dancers.bot.Thresholds
    lit_share: float = 0.008
    red_share: float = 0.03
    hue_share: float = 0.15
    background: str = ""        # explicit backdrop path; empty → auto-pick
    video_background: bool = True   # off → still image instead of the video


@dataclass
class AppConfig:
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    ava_dancers: AvaDancersConfig = field(default_factory=AvaDancersConfig)


class ConfigManager:
    CONFIG_FILE = Path("config.json")

    def __init__(self):
        self.data = self._load()

    def _load(self) -> AppConfig:
        if not self.CONFIG_FILE.exists():
            return AppConfig()
        try:
            raw = json.loads(self.CONFIG_FILE.read_text(encoding="utf-8"))
            default = AppConfig()
            self._merge(default, raw)
            return default
        except Exception:
            return AppConfig()

    def _merge(self, instance, raw: dict):
        """Recursively merge raw dict into dataclass, coercing types."""
        for key, val in raw.items():
            if not hasattr(instance, key):
                continue
            current = getattr(instance, key)
            if hasattr(current, "__dataclass_fields__") and isinstance(val, dict):
                self._merge(current, val)
            else:
                try:
                    setattr(instance, key, type(current)(val))
                except (TypeError, ValueError):
                    pass  # keep default on type mismatch

    def save(self):
        self.CONFIG_FILE.write_text(
            json.dumps(asdict(self.data), indent=4, ensure_ascii=False),
            encoding="utf-8",
        )
