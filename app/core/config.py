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
    background: str = ""            # explicit backdrop path; empty → auto-pick
    # Every window with its own backdrop carries this same field, one per
    # section — there is no single shared flag. SettingsWindow's own
    # "Видеофон" switch is what keeps them all in step: it writes this
    # value into every section at once (see SettingsWindow._toggle_video),
    # not just this one. A window's own _apply_backdrop() still only ever
    # reads its own section's copy.
    video_background: bool = True   # off → still image instead of the video
    # The wires SettingsWindow's own "Линии между окнами" switch controls —
    # purely decorative (NodeLinkCanvas), so nothing else reads this.
    show_links: bool = True
    # Scramble reveal and split-flap wipe in every log panel. Off → lines
    # appear whole and clearing is instant; the animation is per-character
    # on a timer, so this is the switch to reach for when the UI drags.
    # One flag for the whole app, unlike video_background — see
    # app/ui/widgets/log_panel.py.
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
    height: int = 620
    background: str = ""            # explicit backdrop path; empty → auto-pick
    video_background: bool = True   # off → still image instead of the video

    # ── Rink geometry, absolute screen coordinates ───────────────────────
    # Seeded from the hand calibration of 2026-08-03 (780x666 at 891,385)
    # and the fractions modules/hockey/rink_area.py used to hardcode. Every
    # one of these is meant to be corrected with the module's own
    # "Область экрана" button rather than edited by hand — the buttons write
    # here directly.
    rink_left:   int = 891
    rink_top:    int = 385
    rink_width:  int = 780
    rink_height: int = 666

    # Rows the defenders patrol, kept sorted top to bottom so a row's index
    # always means "how far up the rink it is":
    #   [{"y": strip centre, "height": ..., "wall_left": ..., "wall_right": ...,
    #     "body_w": ..., "body_h": ..., "body_dy": ...}]
    # body_* is the defender's whole outline, marked by hand: what gets
    # detected is the helmet, but what blocks a shot is the body under it.
    # body_dy is where that outline starts relative to the row's own line,
    # so it can be hung off a helmet wherever the helmet turns up. 0 width
    # means it has never been marked and goalie_half_w stands in.
    # The walls are per row because each row's defender turns round at its
    # own place (confirmed 2026-08-09) — one pair of boards for the whole
    # rink was simply wrong. A row without them falls back to the two below.
    # Empty until "Найти ряды" has run or rows are marked by hand; discovery
    # *adds* rather than replacing, because level 1 only ever shows one
    # defender and the rest are only learnable on a level that has them.
    lanes: list = field(default_factory=list)

    # Fallback walls for a row that has none of its own — a seed, not the
    # truth: see the per-row values above.
    wall_left:  int = 941
    wall_right: int = 1621

    # Where the puck starts — bottom-centre of the rink, a little short of
    # the very edge.
    shooter_x: int = 1281
    shooter_y: int = 984

    # The goal mouth: a line, not a point — the shot has to land between
    # goal_left and goal_right at goal_y.
    goal_y:     int = 452
    goal_left:  int = 1141
    goal_right: int = 1421

    # What actually blocks a shot is the defender's body and stick, while
    # what gets detected is the helmet — so this is deliberately wider than
    # any helmet blob.
    goalie_half_w: int = 50
    puck_radius:   int = 10

    # ── Helmet detection ─────────────────────────────────────────────────
    # The one thing every defender has in common whatever their avatar
    # looks like is a red helmet (confirmed 2026-08-08), so detection is a
    # red blob inside a row's own strip rather than a sprite match.
    red_sat_min:   int = 120   # below this a red-ish pixel is washed-out ice
    red_val_min:   int = 80    # below this it is shadow, not a helmet
    blob_area_min: int = 150   # below this it is a speck, not a helmet
    # No upper bound here on purpose: the rink is drawn in perspective, so
    # what counts as "too big" depends on the row, and a fixed ceiling set
    # by guess (4000) turned out to reject the near rows' own helmets. The
    # sanity ceiling lives in detect.py, and shape and the photo score do
    # the actual discriminating.

    # Red alone catches things that are not defenders, so a candidate is
    # also scored against templates/hockey_player*.png and dropped below
    # this. Kept low because those photos are three specific avatars and a
    # fourth player is not going to look like any of them — the score is
    # here to reject red scenery, not to insist on a known face. 0 turns
    # the check off entirely.
    player_match_min: float = 0.30

    # Strip height for a row entry that has none of its own — hand-marked
    # rows carry the height of the box they were drawn with, so this only
    # covers a row hand-edited into the file without one.
    lane_height: int = 56

    # Mask out rink pixels that are red in every single frame. Markings
    # being mistaken for a helmet outright is already handled by shape, so
    # what this actually buys is that a helmet passing *over* a red line
    # does not merge with it into one long blob and vanish for exactly as
    # long as it overlaps. Off by default: it costs an 80-frame warm-up, and
    # whether this rink has red lines at all is still unconfirmed — switch it
    # on if "Тест детекции" shows defenders blinking out mid-run.
    static_mask: bool = False

    # Draw the prediction for *now* instead of for the moment a shot would
    # arrive. A checking mode, not a working one: at zero the box has to
    # ride exactly on its defender, so leading or lagging is obvious at a
    # glance in a way it never is at a second and a half. Shooting is
    # refused while it is on — the model's own accuracy check compares a
    # prediction against the same instant it was made for, which at zero is
    # true by construction and proves nothing.
    predict_now: bool = False

    # Whether the watch narrates itself: the head count it takes before
    # calibrating, the per-row accuracy while it settles, the breakdown of a
    # refused shot. Off leaves the log with the shots themselves and the
    # errors. On by default — the numbers are how the model is judged.
    verbose_log: bool = True

    # Consider pulls between the three that were measured — 0.15, 0.35 and
    # so on — by scaling the measured arc. Off by default because it is the
    # one thing in the shot that is interpolated rather than seen: the arc's
    # shape is measured, how it grows with the pull is assumed linear.
    fine_aim: bool = False

    # Cut the log down to what a run is actually doing: the level, the head
    # count, one line per row as it is calibrated, and the shot. Everything
    # still happens — the running commentary just stops being printed. Only
    # means anything while verbose_log is on.
    light_log: bool = False

    # Stop drawing the boxes and zones over the game. They keep being
    # computed and used; they are simply not painted, for playing over.
    hide_overlays: bool = False

    # Predict every row from its board zone and nothing else — no tracking,
    # no autocorrelation. Each row gets a stretch of ice at one end, every
    # defender that turns round in it is timed separately, and the gap
    # between two of his turns is his period. The same method for every row
    # whatever is standing in it and however many are moving.
    orange_mode: bool = False

    # Shoot as soon as every row is calibrated, without waiting for the
    # button. The same plan the button would draw up — nothing about the
    # refusals or the thresholds changes, only who presses.
    auto_shot: bool = False

    # The nine cells of the progress strip above the rink, left to right, as
    # {"left","top","width","height"} in screen coordinates. Each one holds
    # templates/hockey_nice.png or hockey_bad.png once its level has been
    # played, so the count of filled ones is the level you are on minus one:
    # nine cells are exactly enough to tell levels 1 through 10 apart.
    # Marked by hand with the module's own button.
    level_cells: list = field(default_factory=list)


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
    video_background: bool = True   # off → still image instead of the video


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
    video_background: bool = True   # off → still image instead of the video
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


class ConfigManager:
    CONFIG_FILE = Path("config.json")

    def __init__(self):
        self.data = load_dataclass(self.CONFIG_FILE, AppConfig)

    def save(self):
        save_dataclass(self.CONFIG_FILE, self.data)
