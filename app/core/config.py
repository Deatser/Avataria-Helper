# app/core/config.py
from __future__ import annotations
from dataclasses import dataclass, field

from app.core.json_store import load_dataclass, save_dataclass
from app.core.paths import data_path


@dataclass
class OverlayConfig:
    x: int = 10
    y: int = 10
    # Под доску плиток, а не под старую колонку кнопок: на чистой
    # установке config.json ещё нет, и окно раскрывается по этим числам.
    width: int = 713
    height: int = 828
    opacity: int = 220
    skip_close_confirm: bool = False   # "Не спрашивать снова" on the close dialog
    background: str = ""            # explicit backdrop path; empty → auto-pick
    # The wires SettingsWindow's own "Линии между окнами" switch controls —
    # purely decorative (NodeLinkCanvas), so nothing else reads this.
    show_links: bool = True
    # Scramble reveal and split-flap wipe in every log panel. Off → lines
    # appear whole and clearing is instant; the animation is per-character
    # on a timer, so this is the switch to reach for when the UI drags.
    # Один флаг на всё приложение — см. app/ui/widgets/log_panel.py.
    log_animation: bool = True


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


@dataclass
class HockeyConfig:
    """Только рамка окна — мод переписывается с нуля.

    Здесь было тридцать полей: геометрия катка, ряды с бортами и габаритами
    вратарей, пороги детекции, четыре режима работы, две градации логов и
    девять ячеек полосы уровней. Всё это снято вместе с кнопками окна и
    лежит в коммите «LEGACY: хоккей — рабочая, но не идеальная версия»;
    новая версия заведёт свои поля тогда, когда они ей понадобятся.

    Оставлено ровно то, без чего окно перестаёт быть окном, — тот же набор,
    что у SnowboardConfig. `favorite` читает Overlay.restore_favorite_windows.
    """
    favorite: bool = False
    position_saved: bool = False
    x: int = 370
    y: int = 10
    width: int = 420
    height: int = 380
    background: str = ""            # explicit backdrop path; empty → auto-pick


@dataclass
class StatsWindowConfig:
    """Geometry and autostart — the numbers themselves live in stats.json."""
    favorite: bool = False      # open together with the helper at startup
    position_saved: bool = False
    x: int = 420
    y: int = 60
    width: int = 460
    height: int = 560
    background: str = ""            # explicit backdrop path; empty → auto-pick


@dataclass
class PromoConfig:
    """Geometry same as every other module window. No favorite flag:
    unlike the other windows this one has no star button — it is reopened
    from its own launcher button, not restored at startup."""
    position_saved: bool = False
    x: int = 420
    y: int = 60
    width: int = 380
    height: int = 420
    background: str = ""            # explicit backdrop path; empty → auto-pick
    # "Запустить автодетект промокодов" — on, PromoAutoLoop re-reads the
    # open Firebase base (filled by vkapi.py) once a minute and plays
    # through the in-game activation screen for whatever is pending (see
    # promo_activate.is_pending). Kept here so the button shows the same
    # state after the window (or the whole helper) is closed and reopened.
    # No "last seen" bookmark is needed: each cycle re-reads the base
    # fresh, and activated_promo_log is what actually keeps a code from
    # being submitted twice.
    detect_enabled: bool = False


@dataclass
class EnergyConfig:
    """Энергия — geometry, plus the one number the window is about.

    Carries the same star as the module windows: starred → the overlay
    reopens it at startup (see Overlay.restore_favorite_windows).
    """
    favorite: bool = False
    position_saved: bool = False
    x: int = 420
    y: int = 60
    width: int = 560
    height: int = 480
    # How much energy the bar run should buy. A setting, not a tally — what
    # actually gets bought will be counted in stats.json when the run exists.
    bar_amount: int = 200
    # Which of the bar's two prices to pay — "silver" or "gold". See
    # app/core/energy_bar.py for the menu itself.
    pay_with: str = "gold"


@dataclass
class GameConfig:
    """Эталонный размер игры — тот, при котором калибровалось всё остальное.

    Нули значат «ещё не записан»: помощник запишет его сам, когда впервые
    увидит игру развёрнутой, и до тех пор считает всё ровно как раньше.
    Хранится картинка игры в экранных координатах — то, что осталось от
    клиентской области окна после отрезания полей по краям (см.
    app/core/game_geometry.py).
    """
    reference_left: int = 0
    reference_top: int = 0
    reference_width: int = 0
    reference_height: int = 0


@dataclass
class AppConfig:
    overlay: OverlayConfig = field(default_factory=OverlayConfig)
    ava_dancers: AvaDancersConfig = field(default_factory=AvaDancersConfig)
    gardener: GardenerConfig = field(default_factory=GardenerConfig)
    janitor: JanitorConfig = field(default_factory=JanitorConfig)
    snowboard: SnowboardConfig = field(default_factory=SnowboardConfig)
    hockey: HockeyConfig = field(default_factory=HockeyConfig)
    stats_window: StatsWindowConfig = field(default_factory=StatsWindowConfig)
    promo: PromoConfig = field(default_factory=PromoConfig)
    energy: EnergyConfig = field(default_factory=EnergyConfig)
    game: GameConfig = field(default_factory=GameConfig)


class ConfigManager:
    CONFIG_FILE = data_path("config.json")

    def __init__(self):
        self.data = load_dataclass(self.CONFIG_FILE, AppConfig)

    def save(self):
        save_dataclass(self.CONFIG_FILE, self.data)
