# app/core/config.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path

from app.core.json_store import load_dataclass, save_dataclass


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
    # Which reward line ends the run — "gold" or "silver". Gold by default:
    # it is the shorter farm, so ending on it is the safer surprise.
    finish_on: str = "gold"
    # After the exit is clicked, also click Повтор and Старт to open the
    # next round. On by default — farming is the point of the module.
    auto_restart: bool = True


@dataclass
class GardenerConfig:
    favorite: bool = False
    position_saved: bool = False
    x: int = 420
    y: int = 500
    width: int = 380
    height: int = 800
    # The six per-kind debug buttons — off by default, since they are a
    # troubleshooting tool rather than something a normal run needs.
    show_kind_buttons: bool = False
    # Once a way to read the game's own cooldown timer exists, this is what
    # will let the bot switch to Садовник on its own the moment it runs
    # out, wherever the avatar happens to be. Off until that switch-over
    # exists.
    auto_clean: bool = False


@dataclass
class JanitorConfig:
    favorite: bool = False
    position_saved: bool = False
    x: int = 420
    y: int = 500
    width: int = 380
    height: int = 800
    # Same two toggles Садовник's settings sheet has — see GardenerConfig.
    show_kind_buttons: bool = False
    auto_clean: bool = False


@dataclass
class SnowboardConfig:
    favorite: bool = False
    position_saved: bool = False
    x: int = 370
    y: int = 10
    width: int = 420
    height: int = 570
    background: str = ""            # explicit backdrop path; empty → auto-pick
    video_background: bool = True   # off → still image instead of the video


@dataclass
class HockeyConfig:
    favorite: bool = False
    position_saved: bool = False
    x: int = 370
    y: int = 10
    width: int = 420
    height: int = 480
    background: str = ""            # explicit backdrop path; empty → auto-pick
    video_background: bool = True   # off → still image instead of the video


@dataclass
class StatsWindowConfig:
    """Geometry and autostart — the numbers themselves live in stats.json."""
    favorite: bool = False      # open together with the helper at startup
    position_saved: bool = False
    x: int = 420
    y: int = 60
    width: int = 460
    height: int = 560


@dataclass
class AppConfig:
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    ava_dancers: AvaDancersConfig = field(default_factory=AvaDancersConfig)
    gardener: GardenerConfig = field(default_factory=GardenerConfig)
    janitor: JanitorConfig = field(default_factory=JanitorConfig)
    snowboard: SnowboardConfig = field(default_factory=SnowboardConfig)
    hockey: HockeyConfig = field(default_factory=HockeyConfig)
    stats_window: StatsWindowConfig = field(default_factory=StatsWindowConfig)


class ConfigManager:
    CONFIG_FILE = Path("config.json")

    def __init__(self):
        self.data = load_dataclass(self.CONFIG_FILE, AppConfig)

    def save(self):
        save_dataclass(self.CONFIG_FILE, self.data)
