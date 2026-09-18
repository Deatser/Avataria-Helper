# app/ui/overlay.py
from __future__ import annotations
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QApplication, QDialog, QSizePolicy)
from PySide6.QtCore import Qt, QPoint, QRect, QTimer

from app.core.freeze_watch import CHECK_INTERVAL as FREEZE_INTERVAL_S
from app.core.freeze_watch import FreezeWatch
from app.core.pause_watch import (BREAK_REPEAT_S, PLACES_WAIT_S, GameReadyWatch,
                                  PauseRecovery, PauseWatch)
from app.core.daily_reward import DailyRewardCollect, DailyRewardWatch
from app.core.promo_activate import PromoAutoLoop
from app.core.restart_state import (HELPER_LOG, REASON_AFK, REASON_BREAK,
                                    REASON_MANUAL, REASON_PAUSE, REASON_REWARD,
                                    REASON_STUCK,
                                    RestartInfo, take_logs, write_logs,
                                    write_restart)
from app.core.stats import StatsManager
from app.ui.promo_window import PromoWindow
from app.ui import theme
from app.ui.calibration_overlay import CalibrationOverlay
from app.ui.crt_power_mixin import CrtPowerMixin
from app.ui.energy_window import EnergyWindow
from app.ui.helper_settings_panel import HelperSettingsPanel
from app.ui.module_window import _SAVE_DELAY_MS
from app.ui.drag_mixin import BackgroundDragMixin
from app.ui.game_fit import GameFitMixin
from app.ui.game_layer import GameLayer
from app.ui.node_links import NodeLinkCanvas
from app.ui.resize_mixin import ResizeMixin
from app.ui.stats_window import StatsWindow
from app.ui.collapse_mixin import CollapseMixin
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.log_actions import build_log_actions
from app.ui.widgets import log_panel as log_panel_mod
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.nt_confirm_dialog import NtConfirmDialog
from app.ui.widgets.vw_panel import VwPanel
from app.core.game_watch import GameWatch
from app.module_registry import MODULES

# Same auto-pick rule every module's own backdrop uses — see
# modules.ava_dancers.window._default_backdrop.
_BACKDROP_STEM  = "AvaHelper"
_PROJECT_ROOT   = Path(__file__).resolve().parents[2]
_TEMPLATES      = _PROJECT_ROOT / "templates"
_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def _default_backdrop() -> str:
    """First existing templates/AvaHelper.* file."""
    for suffix in _STILL_SUFFIXES:
        candidate = _TEMPLATES / f"{_BACKDROP_STEM}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return ""


# The board of tiles owns the window and the log gets what is left — 70/30
# of the height below the header, held by the layout's own stretch factors.
_BOARD_SHARE = 7
_LOG_SHARE   = 3

# A tile is a button, not a panel: it keeps its own size and the three
# columns are spread across the window — first flush left, last flush right,
# the slack shared out between them — so the backdrop breathes around them
# instead of being papered over edge to edge.
# One size for every tile on the board, whatever column it is in and however
# many its column holds — a taller window gives air below them, not bigger
# buttons in the short columns.
_TILE_W = 210   # fits the longest face, "PROMO CODES", without clipping
_TILE_H = 58

# Кулинар has no module behind it yet — it holds its slot, and the game's
# own rose-red, until there is something to switch on. (Энергия has its own
# window now; its yellow lives in the theme as EN_YELLOW.)
_COOK_ROSE = "#ef4b6b"

# Моды, за которыми код есть, но доводить его ещё есть куда. Их тайлы стоят
# на доске погашенными (NtButton(wip=True)): тусклее соседей, под курсором
# пишут «В разработке» и не открываются ни по клику, ни из автозагрузки, ни
# при возврате модов после перезапуска — см. _toggle_module.
_WIP_MODULES = ("Сноуборд", "Хоккей", "Садовник", "Уборщик")

# Как часто можно перезапускаться из-за техперерыва. Перерыв длится часами и
# перезапуском не лечится — это просто регулярная проверка, не кончился ли он.
BREAK_RETRY_S = 300.0

# Сколько мод после перезапуска имеет на то, чтобы дойти до игры и начать в
# неё играть. Щедро: вход в игру — это несколько экранов с анимациями, плюс
# сама игра стартует не мгновенно.
PLAYING_CHECK_MS = 180_000

_BOARD_TOP_GAP = 22   # separator → column captions
_TILE_GAP      = 14   # between tiles inside a column
_ICON_BTN   = 26    # < NtButton._SMALL_W → centred glyph, no accent bar

# Разметка окна ежедневной награды — см. Overlay.mark_zone. Две области: по
# чему это окно узнать и куда в нём нажать. Рамка появляется посреди игры и
# такого размера, чтобы её было за что схватить.
_ZONE_SLOTS = 2
_ZONE_W     = 240
_ZONE_H     = 120

# Button faces are English; module_cls.name stays Russian — it is the key
# for open windows, logs and config, and none of that is on screen here.
_DISPLAY_NAMES = {
    "Ava Dancers": "Ava Dancers",
    "Сноуборд":    "Snowboard",
    "Хоккей":      "Hockey",
    "Садовник":    "Садовник",
    "Уборщик":     "Уборщик",
}


class Overlay(GameFitMixin, CollapseMixin, CrtPowerMixin, BackgroundDragMixin,
              ResizeMixin, QWidget):
    _RESIZE_MIN_W = 3 * _TILE_W + 2 * theme.SPACING + theme.PADDING * 2
    _RESIZE_MIN_H = 430   # header + three tile rows + log + handle

    def __init__(self, config, window_manager, stats=None, parent=None):
        super().__init__(parent)
        self.config = config
        self.wm = window_manager
        self.stats = stats if stats is not None else StatsManager()
        self._stats_window: StatsWindow | None = None
        self._settings_panel: HelperSettingsPanel | None = None
        # Разметка двух областей окна ежедневной награды — см. mark_zone.
        self._zone_calib: CalibrationOverlay | None = None
        self._zone_slot = 0
        self._zones_marked: dict[int, QRect] = {}
        # Игру можно свернуть в окно любого размера и любой формы; всё, что
        # помощник знает про её координаты, считано при развёрнутой. Здесь
        # заводится то, что замечает смену размера, а fit_to_game — то, что
        # переставляет под неё окна. См. app/core/game_watch.py.
        self._game_watch = GameWatch(window_manager, config, self)
        self._game_watch.changed.connect(self._on_game_resized)
        self._game_watch.recorded.connect(self._on_reference_recorded)
        self._game_watch.hint.connect(
            lambda text: self.add_log(text, level="error"))
        self._links = NodeLinkCanvas(window_manager)
        self._links.set_enabled(getattr(config.data.overlay, "show_links", True))
        # Before any LogPanel is built, so the very first startup line
        # already obeys the saved choice.
        log_panel_mod.set_animation_enabled(
            getattr(config.data.overlay, "log_animation", True))
        self._drag_origin = QPoint()
        self._drag_from   = QPoint()
        self._save_later  = QTimer(self)
        self._save_later.setSingleShot(True)
        self._save_later.setInterval(_SAVE_DELAY_MS)
        self._save_later.timeout.connect(self.config.save)
        self._open_windows: dict[str, QWidget] = {}
        self._startup_shown = False
        self._startup_done  = False
        self._game_ok       = None   # tri-state: unknown until first check
        self._pending_logs: list[tuple[list, str]] = []
        self._panel: VwPanel | None = None
        self._promo_auto: PromoAutoLoop | None = None
        self._pause_watch = PauseWatch(get_hwnd=self.wm.get_game_hwnd)
        self._pause_watch.pause_seen.connect(self._on_pause_detected)
        self._pause_watch.break_seen.connect(self._on_break_detected)
        self._pause_watch.error.connect(
            lambda msg: self.add_log(f"[Пауза] {msg}", level="error"))
        self._freeze_watch = FreezeWatch(get_hwnd=self.wm.get_game_hwnd)
        self._freeze_watch.frozen.connect(self._on_game_afk)
        self._freeze_watch.error.connect(
            lambda msg: self.add_log(f"[Пауза] {msg}", level="error"))
        self._pause_recovery: PauseRecovery | None = None
        self._paused_modules: list[str] = []
        # Окно ежедневного подарка: вотч смотрит всегда, сборщик появляется
        # только на время двух кликов — см. _on_reward_seen.
        self._reward_watch = DailyRewardWatch(get_hwnd=self.wm.get_game_hwnd)
        self._reward_watch.seen.connect(self._on_reward_seen)
        self._reward_watch.error.connect(
            lambda msg: self.add_log(f"[Подарок] {msg}", level="error"))
        self._reward_collect: DailyRewardCollect | None = None
        self._reward_modules: list[str] = []
        self._reward_outcome: str | None = None
        self._break_seen_at = 0.0   # monotonic последней заставки техперерыва
        # Записка от прошлого запуска (см. app/core/restart_state.py) и
        # признак того, что мы сами сейчас уходим на перезапуск.
        self._restart_info = None
        self._restarting   = False
        self._saved_logs: dict[str, str] = {}   # логи окон до перезапуска
        self._ready_watch: GameReadyWatch | None = None
        self._promo_window: PromoWindow | None = None
        self._energy_window: EnergyWindow | None = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(config.data.overlay.width, config.data.overlay.height)
        self._build_ui()
        self.move(config.data.overlay.x, config.data.overlay.y)
        self._init_resize()
        self._init_background_drag()
        self._init_crt_power()
        self._init_collapse(self._panel, self.wm)

        # Keep the header dot honest if the game window disappears.
        # Не _game_watch: под этим именем выше уже лежит слежение за
        # размером игры, и второе присваивание затирало его целиком.
        # Оверлей при этом ничего не замечал — GameWatch остаётся жив как
        # ребёнок QObject, — но start_game_watch() из main.py запускал
        # вместо него вот этот таймер, размер игры не мерил никто, и весь
        # перевод координат оставался тождественным: клики модов уходили
        # по числам развёрнутой игры куда бы её ни ужали.
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(2000)
        self._status_timer.timeout.connect(self.refresh_game_status)
        self._status_timer.start()

        self.set_promo_watch_enabled(
            getattr(config.data.promo, "detect_enabled", False))

        # Зависания игры караулятся всегда, а не только пока включён какой-то
        # мод: окно «Пауза» встаёт поверх игры само по себе, и увидеть его
        # некому, если каждый мод смотрит только за своим экраном. По той же
        # причине глобален и FreezeWatch — застывший экран не привязан к моду.
        self._pause_watch.start()
        self._freeze_watch.start()
        # И окно ежедневного подарка — по той же причине: оно встаёт поверх
        # игры само, чаще всего после перезапуска, но и на смене суток тоже.
        self._reward_watch.start()

    def _build_ui(self):
        self._panel = VwPanel(self)
        # Same tall-and-narrow crop StatsWindow deals with, biased the other
        # way — just a slight nudge left, not the full swing StatsWindow's
        # own backdrop needed.
        self._panel.focus_x = -0.2
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING, theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        # ── Header ──────────────────────────────────────────────────────────
        header = QHBoxLayout()
        self._status_dot = NtStatusDot()
        self._status_dot.set_stopped()   # red until the game window is found
        title = QLabel("AVATARIA HELPER")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_S))
        title.setStyleSheet(f"color:{theme.TEXT_PRIMARY}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self._drag_press
        title.mouseMoveEvent  = self._drag_move
        # Settings is a gear up in the title bar: pressed once in a while, and
        # it has no business taking a slab on the board.
        settings_btn = NtButton("⚙", accent=theme.ACCENT, filled=True)
        settings_btn.setFixedSize(_ICON_BTN, _ICON_BTN)
        settings_btn.setToolTip("Настройки")
        settings_btn.clicked.connect(self._toggle_settings)
        self._collapse_btn = NtButton("▲", accent=theme.ACCENT)
        self._collapse_btn.setFixedSize(_ICON_BTN, _ICON_BTN)
        self._collapse_btn.clicked.connect(self._toggle_collapse)
        close_btn = NtButton("×", accent=theme.ACCENT_RED)
        close_btn.setFixedSize(_ICON_BTN, _ICON_BTN)
        close_btn.clicked.connect(self.close)
        header.addWidget(self._status_dot)
        header.addSpacing(6)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(settings_btn)
        header.addWidget(self._collapse_btn)
        header.addWidget(close_btn)
        layout.addLayout(header)

        # ── Separator ───────────────────────────────────────────────────────
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.BORDER_DIM};")
        layout.addWidget(sep)
        # Air between the title bar's rule and the GAMES / JOBS / PANEL
        # captions — only above them; the gap to their own tiles stays tight.
        layout.addSpacing(_BOARD_TOP_GAP)

        # ── Three columns of big tiles ──────────────────────────────────────
        # GAMES — the modules that play something, one under another.
        # JOBS  — the two that grind a profession.
        # PANEL — the helper's own windows.
        # Every tile stretches to fill the board, so the buttons are the
        # window's main surface and the log takes the rest.
        self._module_buttons: dict[str, NtButton] = {}
        board = QHBoxLayout()
        board.setSpacing(theme.SPACING)

        games = [m for m in MODULES if getattr(m, "panel_slot", "top") == "top"]
        jobs  = [m for m in MODULES if getattr(m, "panel_slot", "top") == "bottom"]

        def module_tile(module_cls) -> NtButton:
            wip = module_cls.name in _WIP_MODULES
            btn = self._tile(self._module_label(module_cls.name, False),
                             getattr(module_cls, "color", None), wip=wip)
            if wip:
                # Ни обработчика, ни места в _module_buttons: тайл ничего не
                # открывает, а значит и «активным» ему становиться не с чего.
                return btn
            btn.clicked.connect(lambda _, m=module_cls: self._toggle_module(m))
            self._module_buttons[module_cls.name] = btn
            return btn

        # Not modules yet — placeholders holding their slot (and their colour)
        # on the board until there is something behind them to switch on.
        cook   = self._tile("Кулинар", _COOK_ROSE, wip=True)
        energy = self._tile("Энергия", theme.EN_YELLOW, self._toggle_energy)

        columns = [
            ("GAMES", [module_tile(m) for m in games]),
            ("JOBS",  [module_tile(m) for m in jobs] + [cook]),
            ("PANEL", [self._tile("Статистика", theme.ACCENT_CYAN,
                                  self._toggle_stats),
                       self._tile("Промокоды", theme.ACCENT_CYAN,
                                  self._toggle_promo_window),
                       energy]),
        ]
        # A column of its own per group; the tiles keep their own height, so a
        # short column simply ends earlier instead of stretching its buttons.
        for index, (caption, tiles) in enumerate(columns):
            if index:
                board.addStretch(1)   # slack shared evenly between columns
            strip = QVBoxLayout()
            strip.setSpacing(6)          # caption sits close to its column
            strip.addWidget(self._caption(caption))
            stack = QVBoxLayout()        # …the tiles themselves breathe more
            stack.setSpacing(_TILE_GAP)
            for tile in tiles:
                stack.addWidget(tile)
            strip.addLayout(stack)
            strip.addStretch()
            board.addLayout(strip, 0)
        layout.addLayout(board, stretch=_BOARD_SHARE)

        # ── Log section ─────────────────────────────────────────────────────
        # The bottom third: clearing and copying stay as small buttons on the
        # heading row.
        self.log_panel = LogPanel()
        self.log_panel.setMinimumHeight(90)
        self.log_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        log_head = QHBoxLayout()
        log_head.addWidget(self._caption("LOG"))
        log_head.addStretch()
        log_head.addLayout(build_log_actions(self.log_panel, theme.BORDER_BRIGHT))
        layout.addLayout(log_head)

        layout.addWidget(self.log_panel, stretch=_LOG_SHARE)

        # ── Backdrop: templates/AvaHelper.* by default ───────────────────────
        self._panel.background_failed.connect(
            lambda msg: self.add_log(msg, level="error")
        )
        self._apply_backdrop(fade=False)

        # ── Drag handle ──────────────────────────────────────────────────────
        drag = NtDragHandle()
        drag.mousePressEvent = self._drag_press
        drag.mouseMoveEvent  = self._drag_move
        layout.addWidget(drag)

    # ── Layout helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _caption(text: str) -> QLabel:
        """Small dim heading over a group — МОДЫ, LOG."""
        label = QLabel(text)
        label.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        label.setStyleSheet(
            f"color:{theme.TEXT_SECONDARY}; background:transparent;")
        return label

    @staticmethod
    def _tile(text: str, accent: str, on_click=None,
              wip: bool = False) -> NtButton:
        """One slab on the board — the launcher's main surface."""
        btn = NtButton(text, accent=accent, wip=wip)
        btn.setFixedSize(_TILE_W, _TILE_H)
        btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        btn.setFont(theme.get_mono_font(theme.FONT_SIZE_M))
        if on_click is not None:
            btn.clicked.connect(on_click)
        return btn

    # ── Backdrop ─────────────────────────────────────────────────────────────

    def _apply_backdrop(self, fade: bool = True):
        cfg      = self.config.data.overlay
        backdrop = getattr(cfg, "background", "") or _default_backdrop()
        if backdrop and not self._panel.set_background(backdrop, fade=fade):
            self.add_log(f"Фон не загружен: {backdrop}", level="error")

    def _crt_open_ready(self) -> bool:
        """Hold the switch-on until the video backdrop has a frame to show."""
        return self._panel.backdrop_ready

    # ── ResizeMixin hooks ────────────────────────────────────────────────────

    def _on_resize_panel(self):
        self.layout_panel()
        if self._settings_panel is not None:
            self._settings_panel.keep_inside_host()

    def _on_resize_done(self):
        # Эталонный размер, а не нынешний — см. app/ui/game_fit.py.
        (self.config.data.overlay.width,
         self.config.data.overlay.height) = self.to_reference_size(
            self.width(), self.height())
        self.config.save()

    # ── Размер игры ──────────────────────────────────────────────────────────

    def start_game_watch(self):
        """Начать следить за размером игры — зовётся из main, когда игра
        найдена и объявлена основной."""
        self._game_watch.start()

    def _fit_config(self):
        """GameFitMixin спрашивает, где эталонные числа этого окна.

        У окон модов в self.config лежит их собственная секция, а здесь —
        весь ConfigManager, поэтому ответ свой.
        """
        return self.config.data.overlay

    def live_geometry(self) -> tuple[int, int, int, int]:
        """Где и какого размера окно помощника должно лечь прямо сейчас.

        config хранит эталонные числа; на игре другого размера от них
        считается это. См. app/ui/game_fit.py.
        """
        box = self.fit_box()
        if box is not None:
            return box
        # Игры нет или эталон не записан — эталонные числа и есть ответ.
        cfg = self.config.data.overlay
        return cfg.x, cfg.y, cfg.width, cfg.height

    def _on_game_resized(self):
        """Игра стала другого размера — переставить всё, что над ней висит."""
        self.fit_to_game()
        for window in list(self._open_windows.values()):
            fit = getattr(window, "fit_to_game", None)
            if fit is not None:
                fit()
        for window in (self._stats_window, self._promo_window,
                       self._energy_window):
            if window is not None and hasattr(window, "fit_to_game"):
                window.fit_to_game()
        if self._settings_panel is not None:
            self._settings_panel.keep_inside_host()
        # Прямоугольники, нарисованные поверх самой игры, принадлежат модам,
        # а не этому окну, и списка их тут нет — зато все они верхнеуровневые
        # и все GameLayer. Те, что перерисовываются тактом мода, на refit не
        # делают ничего.
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, GameLayer):
                widget.refit()
        # Провода между окнами перечитывают геометрию своим тактом — см.
        # NodeLinkCanvas._sync, догонят сами.

    def _on_reference_recorded(self, frame):
        self.add_log(f"Эталонный размер игры записан: "
                     f"{frame.width}×{frame.height}")

    def record_game_reference(self):
        """«Запомнить нынешний размер игры как эталонный» — из настроек."""
        if self._game_watch.record_reference() is None:
            self.add_log("Не удалось измерить окно игры", level="error")
            return
        self._on_game_resized()

    # ── Module management ────────────────────────────────────────────────────

    def _toggle_module(self, module_cls):
        name = module_cls.name
        if name in _WIP_MODULES:
            # Одна проверка на все три входа сюда: клик по тайлу,
            # автозагрузка избранного и возврат модов после перезапуска.
            return
        if name in self._open_windows and self._open_windows[name].isVisible():
            self._open_windows[name].close()
            return

        module      = module_cls()
        config_sect = getattr(self.config.data, module_cls.config_key, None)
        window      = module.create_window(
            config         = config_sect,
            save_fn        = self.config.save,
            window_manager = self.wm,
            parent_overlay = self,
            stats          = self.stats,
        )
        self._open_windows[name] = window
        # Если этот мод был открыт до перезапуска — вернуть ему его лог.
        self._restore_window_log(window)

        # Attach BEFORE the first show. Re-parenting an already-shown window
        # leaves Qt's deferred update() path dead: repaint() still draws, but
        # update() never flushes — hover sweeps and text changes freeze.
        # main.py attaches the overlay the same way, which is why it stays live.
        if self.wm.get_game_hwnd():
            self.wm.attach_child(int(window.winId()))
        window.show()
        self._links.connect_windows(self, window,
                                    getattr(module_cls, "color", None))

        self.add_log(f"Запуск мода {name}")
        self._set_module_active(name, True)

    def own_windows(self) -> list[QWidget]:
        """Every window the helper owns — this launcher bar, every open
        module window, and Statistics if it is up. Anything wanting to grab
        a clean frame of the game needs to know what of ours could be
        sitting over it, and that is every one of these, not just itself.
        """
        windows: list[QWidget] = [self, *self._open_windows.values()]
        if self._stats_window is not None:
            windows.append(self._stats_window)
        if self._promo_window is not None:
            windows.append(self._promo_window)
        if self._energy_window is not None:
            windows.append(self._energy_window)
        return windows

    def on_module_closed(self, module_name: str):
        self._open_windows.pop(module_name, None)
        self._set_module_active(module_name, False)
        self.add_log(f"Закрытие мода {module_name}")

    @staticmethod
    def _module_label(name: str, active: bool) -> str:
        # The tile says what the module is, in English; whether it is running
        # is the accent's job (set_active), not the label's.
        return _DISPLAY_NAMES.get(name, name)

    def _set_module_active(self, name: str, active: bool):
        btn = self._module_buttons.get(name)
        if not btn:
            return
        btn.setText(self._module_label(name, active))
        btn.set_active(active)

    def add_log(self, message: str, level: str = "info"):
        self.add_log_segments([(message, theme.TEXT_SECONDARY)], level)

    def add_log_segments(self, segments: list, level: str = "info"):
        # Hold logs until the startup banner is written, so "made by Deatser"
        # always stays the very first line (favorite windows restore early).
        if not self._startup_done:
            self._pending_logs.append((segments, level))
            return
        self.log_panel.add_log_segments(segments, level)

    # ── Settings ─────────────────────────────────────────────────────────────

    def _toggle_settings(self):
        if self._settings_panel is None:
            self._settings_panel = HelperSettingsPanel(self)
        self._settings_panel.toggle()

    # ── Разметка окна ежедневной награды ─────────────────────────────────────
    # После перезапуска игра иногда встречает окном ежедневной награды: пока
    # его не забрать, возвращать моды некуда — они будут кликать по окну,
    # которого не ждут. Чтобы научить помощника его закрывать, нужны две
    # области: по чему понять, что окно вообще на экране, и куда нажать.
    # Разметить их можно только руками, поэтому здесь кнопка, а не константы.
    #
    # Ничего не сохраняется: числа идут в лог, откуда переезжают в код. Это
    # разовая работа, и заводить под неё поле в конфиге незачем.

    def mark_zone(self):
        """Показать рамку над игрой, а вторым нажатием записать, где она встала.

        Одна кнопка на обе области по очереди: первое нажатие показывает
        первую, второе её записывает и так далее по кругу. Отмеченная область
        при следующем заходе открывается там, где её оставили, — поправить
        уже размеченное не значит размечать заново.
        """
        if self._zone_calib is None:
            self._zone_calib = CalibrationOverlay(self.wm, reference=self)

        if self._zone_calib.isVisible():
            # Рамку тянут по живому экрану, а выписать её надо в эталонных
            # координатах: числа отсюда уходят в код, а код считает в них.
            box = self._zone_calib.reference_rect(self._zone_calib.bounds())
            self._zone_calib.clear()
            self._record_zone(box)
            return

        hwnd = self.wm.get_game_hwnd()
        rect = self.wm.window_rect_screen(hwnd) if hwnd else None
        if rect is None:
            self.add_log("Игровое окно не найдено — размечать нечего",
                         level="error")
            return
        origin_x, origin_y, width, height = rect
        marked = self._zones_marked.get(self._zone_slot)
        box = (self._zone_calib.live_rect(marked) if marked is not None
               else QRect(origin_x + (width - _ZONE_W) // 2,
                          origin_y + (height - _ZONE_H) // 2,
                          _ZONE_W, _ZONE_H))
        self._zone_calib.show_at(box)
        self.add_log_segments(
            [(f"Область {self._zone_slot + 1} из {_ZONE_SLOTS} — ",
              theme.ACCENT),
             ("тяните за середину, чтобы подвинуть, за край — чтобы изменить "
              "размер, потом нажмите кнопку ещё раз.", theme.TEXT_SECONDARY)],
            level="plain")

    def _record_zone(self, box: QRect):
        """Запомнить область и выписать её числа — все, какие могут
        понадобиться: и прямоугольник для снимка, и центр для клика."""
        slot = self._zone_slot
        self._zones_marked[slot] = QRect(box)
        centre = box.center()
        self.add_log_segments(
            [(f"Область {slot + 1} — ", theme.ACCENT_GREEN),
             (f"{box.width()}×{box.height()} @ ({box.left()}, {box.top()})",
              theme.TEXT_PRIMARY),
             ("   центр ", theme.TEXT_SECONDARY),
             (f"({centre.x()}, {centre.y()})", theme.TEXT_PRIMARY),
             ("   правый низ ", theme.TEXT_SECONDARY),
             (f"({box.right()}, {box.bottom()})", theme.TEXT_PRIMARY)],
            level="plain")

        self._zone_slot = (slot + 1) % _ZONE_SLOTS
        if len(self._zones_marked) < _ZONE_SLOTS:
            return
        # Обе размечены — одной строкой, чтобы можно было скопировать целиком.
        summary = "   ".join(
            f"{i + 1}: ({r.left()},{r.top()},{r.width()},{r.height()}) "
            f"центр ({r.center().x()},{r.center().y()})"
            for i, r in sorted(self._zones_marked.items()))
        self.add_log_segments(
            [("Обе области — ", theme.ACCENT_GREEN),
             (summary, theme.TEXT_PRIMARY)], level="plain")

    def zone_button_text(self) -> str:
        """Подпись кнопки под текущий шаг — её просит панель настроек."""
        if self._zone_calib is not None and self._zone_calib.isVisible():
            return f"📐  Записать область {self._zone_slot + 1}"
        return f"📐  Отметить область {self._zone_slot + 1} из {_ZONE_SLOTS}"

    # ── Promo codes ──────────────────────────────────────────────────────────

    def _toggle_promo_window(self):
        if self._promo_window and self._promo_window.isVisible():
            self._promo_window.close()
            return

        window = PromoWindow(
            config          = self.config.data.promo,
            save_fn         = self.config.save,
            window_manager  = self.wm,
            stats           = self.stats,
            overlay         = self,
        )
        window.closed.connect(self._on_promo_window_closed)
        if self.wm.get_game_hwnd():
            self.wm.attach_child(int(window.winId()))
        window.show()
        self._links.connect_windows(self, window, theme.ACCENT_CYAN)
        self._promo_window = window
        self.add_log("Открыты промокоды")

    def _on_promo_window_closed(self):
        self._promo_window = None

    def set_promo_watch_enabled(self, enabled: bool, announce: bool = False):
        """Starts/stops PromoAutoLoop — the once-a-minute autonomous
        check-and-activate cycle behind "Запустить автоматический детект
        промокодов". Called both from PromoWindow's own button (announce=True,
        the user just turned it on) and once at startup, from whatever
        config.data.promo.detect_enabled was last left at (announce=False —
        resuming, not a fresh "on" the user should be told about)."""
        running = self._promo_auto is not None
        if enabled == running:
            return
        if enabled:
            loop = PromoAutoLoop(get_hwnd=self.wm.get_game_hwnd)
            loop.starting.connect(self._on_promo_starting)
            loop.submitted.connect(self._on_promo_submitted)
            loop.confirmed.connect(self._on_promo_confirmed)
            loop.failed.connect(self._on_promo_failed)
            loop.unknown_error.connect(self._on_promo_unknown_error)
            loop.error.connect(self._on_promo_error)
            loop.recovered.connect(self._on_promo_recovered)
            self._promo_auto = loop
            loop.start()
            if announce:
                self.add_log(
                    "[Промокоды] Активирован автодетект промокодов - Успешно",
                    level="plain")
        else:
            self._promo_auto.stop_loop()
            self._promo_auto.wait()
            self._promo_auto = None

    # These mirror PromoWindow's own handlers (same signal shapes, since
    # both PromoActivateFlow and PromoAutoLoop share promo_activate.run_entry)
    # — logged here too, in the helper's main log, not only the promo
    # window's own one, since this loop runs whether or not that window
    # is even open. Expired/already-activated entries are not logged here
    # at all — that noise belongs only to the "Вывести данные" button's own
    # one-off listing, not to a check running every minute in the
    # background; PromoAutoLoop simply skips them without a signal.

    def _on_promo_starting(self, entry):
        self.add_log(
            f"[Промокоды] Обнаружен новый промокод {entry.code} - Активируем...",
            level="plain")

    def _on_promo_submitted(self, entry):
        title = entry.title or "без названия"
        self.add_log_segments(
            [("[Промокоды] ", theme.TEXT_SECONDARY),
             ("Активирован", theme.ACCENT_GREEN),
             (f" промокод {entry.code} — {title}", theme.TEXT_SECONDARY)],
            level="plain")

    def _on_promo_confirmed(self, entry):
        self.stats.record_promo_activation()

    def _on_promo_failed(self, entry):
        self.add_log(f"[Промокоды] Не удалось активировать промокод {entry.code}",
                     level="error")

    def _on_promo_unknown_error(self, entry):
        self.add_log(
            "[Промокоды] Произошла неизвестная ошибка при активации промокода.",
            level="plain")

    def _on_promo_error(self, message: str):
        self.add_log(f"[Промокоды] {message}", level="error")

    def _on_promo_recovered(self):
        self.add_log("[Промокоды] База промокодов снова доступна", level="plain")

    # ── Зависание игры ───────────────────────────────────────────────────────
    # PauseWatch видит окно «Пауза», дальше всё разыгрывается здесь: какой мод
    # работал — тот и гасим, ОК жмёт PauseRecovery, а как только вернулись
    # «Места», гашеные моды включаются обратно и сами идут по своим кнопкам.
    # Лог пишется в оба окна — в общий и в окно самого мода: с точки зрения
    # мода это его забег оборвался, а не что-то абстрактное в помощнике.

    def _running_module_windows(self) -> list[QWidget]:
        return [w for w in self._open_windows.values()
                if getattr(w, "module_is_running", None) and w.module_is_running()]

    def _pause_log(self, message: str, level: str = "info",
                   windows: list[QWidget] | None = None):
        self.add_log(f"[Пауза] {message}", level=level)
        for window in (self._running_module_windows() if windows is None
                       else windows):
            if hasattr(window, "module_log"):
                window.module_log(f"[Пауза] {message}", level=level)

    def _on_break_detected(self, score: float):
        """Технический перерыв — перезапускаем игру.

        Перезапуск перерыва не отменяет: игра поднимется и покажет ту же
        заставку. Поэтому подряд не долбим — если прошлый перезапуск был по
        этой же причине и меньше BREAK_RETRY_S назад, просто ждём. Следующий
        повод придёт сам: PauseWatch повторяет сигнал раз в пять минут, пока
        заставка висит.
        """
        self._break_seen_at = time.monotonic()
        self.add_log(f"[Пауза] Технический перерыв — заставка на экране "
                     f"({score:.0%}), игре нужен перезапуск",
                     level="error")

        if self._since_break_restart() < BREAK_RETRY_S:
            self.add_log("[Пауза] Перерыв продолжается — ждём и пробуем позже",
                         level="plain")
            return
        self.request_restart(REASON_BREAK)

    def _since_break_restart(self) -> float:
        """Сколько секунд прошло с перезапуска из-за перерыва. Огромное
        число, если такого перезапуска не было."""
        if self._restart_info is None or self._restart_info.reason != REASON_BREAK:
            return float("inf")
        return max(0.0, time.time() - self._restart_info.at)

    # ── Перезапуск игры вместе с помощником ──────────────────────────────────

    def note_restart(self, info):
        """Записка от прошлого запуска: почему мы перезапустились и кого
        включить обратно. Кладётся сюда из main.py."""
        self._restart_info = info

    def request_restart(self, reason: str, modules: list[str] | None = None):
        """Закрыть игру, поднять её заново и вернуться вместе с модами.

        Своими силами это не сделать: наши окна — дочерние по отношению к
        окну игры, и закрытие игры уничтожает их все. Поэтому работу делает
        отдельный процесс (app/core/restarter.py), а помощник закрывается
        сразу за ним и возвращается уже им запущенный.
        """
        if self._restarting:
            return
        self._restarting = True

        # Работавшие моды плюс те, кого позвали явно: мод, попросивший
        # перезапуск из-за зависшей игры, хочет вернуться независимо от того,
        # успел ли его бот числиться запущенным в эту секунду.
        wanted  = list(modules or [])
        running = [w.module_name for w in self._running_module_windows()]
        modules = wanted + [name for name in running if name not in wanted]
        if not modules and self._restart_info is not None:
            # Повторный заход: моды до сюда не дожили — включить их не успели,
            # игра так и не загрузилась. Список берём из прошлой записки,
            # иначе он потеряется на первом же неудачном перезапуске.
            modules = list(self._restart_info.modules)
        for window in self._running_module_windows():
            window.module_log("[Пауза] Перезапускаем игру", level="error")
        if modules:
            self.add_log("[Пауза] После перезапуска включу обратно: "
                         + ", ".join(modules), level="plain")
        write_restart(reason, modules)
        self._save_logs()

        try:
            subprocess.Popen([sys.executable, "-m", "app.core.restarter",
                              str(os.getpid())], cwd=str(_PROJECT_ROOT))
        except Exception as exc:
            self._restarting = False
            self.add_log(f"[Пауза] Перезапуск не запустился: {exc}",
                         level="error")
            return

        self.add_log("[Пауза] Закрываем помощник — вернёмся сами",
                     level="plain")
        self.close()

    # ── Логи через перезапуск ────────────────────────────────────────────────
    # Перезапуск уносит окна вместе с их логами, а читать по возвращении надо
    # именно то, что было до него: причину, последние забеги, что делал мод.
    # Сохраняется готовый HTML — с цветами и отметками времени, как было на
    # экране, — и вставляется наверх панели, над строками новой сессии.

    def _save_logs(self):
        panels = {HELPER_LOG: self.log_panel.toHtml()}
        for name, window in self._open_windows.items():
            log = getattr(window, "_log", None)
            if log is not None and hasattr(log, "toHtml"):
                panels[name] = log.toHtml()
        write_logs(panels)

    def restore_logs(self):
        """Вставить сохранённый лог в главное окно. Окна модов забирают свой
        сами, когда открываются, — см. _restore_window_log."""
        self._saved_logs = take_logs()
        html = self._saved_logs.pop(HELPER_LOG, "")
        if html:
            self.log_panel.prepend_html(html)
            self.add_log("[Пауза] Выше — лог до перезапуска", level="plain")

    def _restore_window_log(self, window):
        html = self._saved_logs.pop(getattr(window, "module_name", ""), "")
        log = getattr(window, "_log", None)
        if html and log is not None and hasattr(log, "prepend_html"):
            log.prepend_html(html)
            log.add_log("[Пауза] Выше — лог до перезапуска", level="plain")

    def resume_after_restart(self):
        """Дождаться, пока игра догрузится, и включить моды, которые работали
        до перезапуска.

        Ждём не по часам, а по картинке: окно игры появляется задолго до
        того, как в неё можно жать, и единственный честный признак «всё
        загрузилось» — кнопка «Места» на экране. Ждать её можно долго,
        перезапуск бывает небыстрым; но если за десять минут она так и не
        появилась, значит поднялось что-то не то, и перезапуск повторяется.
        """
        info = self._restart_info
        if info is None:
            return
        names = list(info.modules)
        self.add_log("[Пауза] Ждём загрузки игры — «Места» на экране",
                     level="plain")

        watch = GameReadyWatch(get_hwnd=self.wm.get_game_hwnd)
        watch.ready.connect(lambda _score, n=names: self._on_game_ready(n))
        watch.timed_out.connect(self._on_game_not_ready)
        watch.error.connect(
            lambda msg: self.add_log(f"[Пауза] {msg}", level="error"))
        # Ссылка снимается по finished, а не в обработчике сигнала: там поток
        # ещё внутри run(), и снос последней ссылки на живой QThread роняет
        # приложение целиком. Ровно то же правило, что у PauseRecovery.
        watch.finished.connect(self._clear_ready_watch)
        self._ready_watch = watch
        watch.start()

    def _clear_ready_watch(self):
        self._ready_watch = None

    def _on_game_ready(self, names: list[str]):
        self.add_log("[Пауза] Игра загрузилась", level="success")
        if not names:
            return
        self._resume_modules(names)
        # Включить мало — надо убедиться, что он доехал до игры.
        QTimer.singleShot(PLAYING_CHECK_MS, lambda: self._verify_playing(names))

    def _on_game_not_ready(self):
        self.add_log(f"[Пауза] Игра не загрузилась за "
                     f"{int(PLACES_WAIT_S // 60)} мин — перезапускаем ещё раз",
                     level="error")
        reason = self._restart_info.reason if self._restart_info else REASON_MANUAL
        self.request_restart(reason)

    def _resume_modules(self, names: list[str]):
        by_name = {m.name: m for m in MODULES}
        for name in names:
            module_cls = by_name.get(name)
            if module_cls is None:
                continue
            window = self._open_windows.get(name)
            if window is None:
                # Именно «нет окна», а не «оно невидимо»: _toggle_module —
                # переключатель, и на уже открытом моде он его закроет.
                self._toggle_module(module_cls)
                window = self._open_windows.get(name)
            if window is not None and hasattr(window, "module_start"):
                window.module_start()
                self.add_log(f"[Пауза] Мод {name} включён после перезапуска",
                             level="plain")

    # ── Доехал ли мод до игры ────────────────────────────────────────────────

    def _verify_playing(self, names: list[str]):
        """Через PLAYING_CHECK_MS после включения — мод действительно играет?

        «Включён» и «играет» — разные вещи: мод может застрять на входе в
        игру, а его собственный детект залипшей кнопки сработает не всегда
        (кнопки, которую он не нашёл, он и не нажимал). Если за отведённое
        время признаков игры нет — перезапускаемся ещё раз.
        """
        stuck = []
        for name in names:
            window = self._open_windows.get(name)
            if window is None or not hasattr(window, "module_is_playing"):
                continue
            if window.module_is_playing():
                self.add_log(f"[Пауза] Мод {name} играет — перезапуск удался",
                             level="success")
            else:
                stuck.append(name)
        if not stuck:
            return
        self.add_log(f"[Пауза] Мод {', '.join(stuck)} так и не начал играть "
                     f"за {PLAYING_CHECK_MS // 60_000} мин — перезапускаем",
                     level="error")
        self.request_restart(REASON_STUCK, stuck)

    def _on_game_afk(self, ratio: float):
        """Картинка не изменилась за три минуты — игра афк.

        Ни меню паузы, ни заставки: экран просто застыл, и жать в него
        бессмысленно. Кликами это не лечится, поэтому сразу перезапуск.
        Кого включить обратно, request_restart соберёт сам — работавшие моды
        (тот же Ava Dancers) вернутся после «Мест», как после залипшей
        кнопки.
        """
        if self._restarting or self._pause_recovery is not None:
            return   # уже разбираемся с этим же зависанием
        if time.monotonic() - self._break_seen_at < BREAK_REPEAT_S:
            # Заставка техперерыва тоже неподвижна. Она лечится не нами, и
            # у неё свой счётчик попыток — не мешаем.
            return
        self._freeze_watch.pause_checks()
        self._pause_log(
            f"Игра афк — картинка не менялась "
            f"{int(FREEZE_INTERVAL_S // 60)} мин ({ratio:.0%} кадра совпало), "
            f"перезапускаем", level="error")
        self.request_restart(REASON_AFK)

    def _on_pause_detected(self, score: float):
        if self._pause_recovery is not None:
            return   # уже разбираемся с этим же зависанием
        self._pause_watch.pause_checks()
        self._freeze_watch.pause_checks()

        stopped = self._running_module_windows()
        self._pause_log(f"Игра зависла — на экране меню паузы ({score:.0%})",
                        level="error", windows=stopped)

        self._paused_modules = []
        for window in stopped:
            if window.module_stop():
                self._paused_modules.append(window.module_name)
                self._pause_log(f"Выключаю мод {window.module_name}",
                                windows=[window])

        recovery = PauseRecovery(get_hwnd=self.wm.get_game_hwnd)
        recovery.ok_clicked.connect(self._on_pause_ok_clicked)
        recovery.menu_cleared.connect(self._on_pause_menu_cleared)
        recovery.restarted.connect(self._on_game_restarted)
        recovery.failed.connect(self._on_pause_failed)
        recovery.error.connect(
            lambda msg: self.add_log(f"[Пауза] {msg}", level="error"))
        self._pause_recovery = recovery
        recovery.start()

    def _paused_module_windows(self) -> list[QWidget]:
        return [self._open_windows[name] for name in self._paused_modules
                if name in self._open_windows]

    def _on_pause_ok_clicked(self, attempt: int):
        tail = "" if attempt == 1 else f" (попытка {attempt})"
        self._pause_log(f"Нажимаю ОК в меню паузы{tail}",
                        windows=self._paused_module_windows())

    def _on_pause_menu_cleared(self):
        self._pause_log("Меню паузы закрыто — ждём перезапуска игры",
                        windows=self._paused_module_windows())

    def _on_game_restarted(self):
        windows = self._paused_module_windows()
        self._pause_log("Игра успешно перезапущена", level="success",
                        windows=windows)
        self._end_pause_recovery()
        for window in windows:
            if window.module_start():
                self._pause_log(f"Включаю мод обратно: {window.module_name}",
                                windows=[window])
        self._paused_modules = []

    def _on_pause_failed(self, message: str):
        """Кликами не вылечилось — остаётся перезапуск.

        Моды на этот момент уже выключены (_on_pause_detected), поэтому
        список для записки берётся из _paused_modules, а не из работающих.
        """
        windows = self._paused_module_windows()
        self._pause_log(message, level="error", windows=windows)
        self._end_pause_recovery()
        if self._restart_info is None:
            self._restart_info = RestartInfo(reason=REASON_PAUSE,
                                             modules=list(self._paused_modules))
        else:
            self._restart_info.modules = list(self._paused_modules)
        self._paused_modules = []
        self.request_restart(REASON_PAUSE)

    def _end_pause_recovery(self):
        """Ссылка на поток снимается не здесь, а по его собственному finished
        (см. _clear_pause_recovery): он в этот момент ещё внутри run(), и
        уронить его сборщиком мусора — верный способ уронить и приложение."""
        if self._pause_recovery is not None:
            self._pause_recovery.stop_flow()
            self._pause_recovery.finished.connect(self._clear_pause_recovery)
        self._pause_watch.resume_checks()
        self._freeze_watch.resume_checks()

    def _clear_pause_recovery(self):
        self._pause_recovery = None

    # ── Окно ежедневного подарка ─────────────────────────────────────────────

    def _reward_log(self, message: str, level: str = "info",
                    windows: list[QWidget] | None = None):
        self.add_log(f"[Подарок] {message}", level=level)
        for window in (self._running_module_windows() if windows is None
                       else windows):
            if hasattr(window, "module_log"):
                window.module_log(f"[Подарок] {message}", level=level)

    def _reward_module_windows(self) -> list[QWidget]:
        return [self._open_windows[name] for name in self._reward_modules
                if name in self._open_windows]

    def _on_reward_seen(self, score: float):
        """Подарок на экране: гасим моды, забираем, возвращаем.

        Моды гасятся до первого клика, а не после. Пока окно висит, они всё
        равно жмут в пустоту, а два наших клика вперемешку с их собственными —
        верный способ нажать не туда.

        Пока разбираемся с зависанием, сюда не лезем: там своя очередь кликов
        и свой перезапуск, и два таких разбирательства разом переспорят друг
        друга.
        """
        if (self._restarting or self._pause_recovery is not None
                or self._reward_collect is not None):
            return
        self._reward_watch.pause_checks()
        self._pause_watch.pause_checks()
        self._freeze_watch.pause_checks()

        stopped = self._running_module_windows()
        self._reward_log(f"Окно ежедневного подарка на экране ({score:.0%}) — "
                         f"забираю", windows=stopped)
        self._reward_modules = []
        for window in stopped:
            if window.module_stop():
                self._reward_modules.append(window.module_name)
                self._reward_log(f"Выключаю мод {window.module_name}",
                                 windows=[window])

        collect = DailyRewardCollect(get_hwnd=self.wm.get_game_hwnd)
        collect.collected.connect(self._on_reward_collected)
        collect.stuck.connect(self._on_reward_stuck)
        collect.error.connect(
            lambda msg: self.add_log(f"[Подарок] {msg}", level="error"))
        # Разбор итога висит на finished, а не на самих сигналах: сборщик
        # может кончиться и ошибкой, а вернуть моды и разбудить вотчи надо в
        # любом случае — иначе один сбой оставит помощник выключенным навсегда.
        collect.finished.connect(self._after_reward)
        self._reward_outcome = None
        self._reward_collect = collect
        collect.start()

    def _on_reward_collected(self, score: float):
        self._reward_outcome = "collected"
        self._reward_log(f"Подарок забран, окно закрылось ({score:.0%})",
                         level="success", windows=self._reward_module_windows())

    def _on_reward_stuck(self, score: float):
        self._reward_outcome = "stuck"
        self._reward_log(f"Окно подарка не закрылось ({score:.0%}) — "
                         f"перезапускаем игру", level="error",
                         windows=self._reward_module_windows())

    def _after_reward(self):
        """Сборщик отработал — что бы с ним ни случилось.

        Ссылка на поток снимается именно здесь, по его собственному finished:
        run() к этому моменту уже вернулся, и уронить поток сборщиком мусора
        нельзя (та же причина, что у _clear_pause_recovery).
        """
        outcome, self._reward_outcome = self._reward_outcome, None
        self._reward_collect = None

        if outcome == "stuck":
            # Моды уже выключены, так что список берётся из своего, а не из
            # работающих — как и на неудавшемся разборе зависания.
            modules, self._reward_modules = self._reward_modules, []
            self.request_restart(REASON_REWARD, modules=modules)
            return

        for window in self._reward_module_windows():
            if window.module_start():
                self._reward_log(f"Включаю мод обратно: {window.module_name}",
                                 windows=[window])
        self._reward_modules = []
        self._reward_watch.resume_checks()
        self._pause_watch.resume_checks()
        self._freeze_watch.resume_checks()

    def _stop_pause_watch(self):
        if self._ready_watch is not None:
            self._ready_watch.stop_watch()
            self._ready_watch.wait(2000)
            self._ready_watch = None
        if self._pause_recovery is not None:
            self._pause_recovery.stop_flow()
            self._pause_recovery.wait(2000)
            self._pause_recovery = None
        if self._reward_collect is not None:
            self._reward_collect.stop_flow()
            self._reward_collect.wait(2000)
            self._reward_collect = None
        self._pause_watch.stop_watch()
        self._pause_watch.wait(2000)
        self._freeze_watch.stop_watch()
        self._freeze_watch.wait(2000)
        self._reward_watch.stop_watch()
        self._reward_watch.wait(2000)

    # ── Energy ───────────────────────────────────────────────────────────────

    def _toggle_energy(self):
        if self._energy_window and self._energy_window.isVisible():
            self._energy_window.close()
            return

        window = EnergyWindow(
            config          = self.config.data.energy,
            save_fn         = self.config.save,
            window_manager  = self.wm,
            stats           = self.stats,
            overlay         = self,
        )
        window.closed.connect(self._on_energy_closed)
        # Attach before the first show, same rule as every other window here
        if self.wm.get_game_hwnd():
            self.wm.attach_child(int(window.winId()))
        window.show()
        self._links.connect_windows(self, window, theme.EN_YELLOW)
        self._energy_window = window
        self.add_log("Открыто окно энергии")

    def _on_energy_closed(self):
        self._energy_window = None

    # ── Statistics ───────────────────────────────────────────────────────────

    def _toggle_stats(self):
        if self._stats_window and self._stats_window.isVisible():
            self._stats_window.close()
            return

        window = StatsWindow(
            config          = self.config.data.stats_window,
            save_fn         = self.config.save,
            stats           = self.stats,
            window_manager  = self.wm,
            overlay         = self,
        )
        window.closed.connect(self._on_stats_closed)
        # Attach before the first show, same rule as the module windows —
        # re-parenting an already-shown window kills its deferred repaints.
        if self.wm.get_game_hwnd():
            self.wm.attach_child(int(window.winId()))
        window.show()
        self._links.connect_windows(self, window, theme.ACCENT_CYAN)
        self._stats_window = window
        self.add_log("Открыта статистика")

    def _on_stats_closed(self):
        self._stats_window = None

    # ── Node wires ───────────────────────────────────────────────────────────

    def on_window_closing(self, window: QWidget):
        """A window has begun switching off — reel its wire back in."""
        self._links.disconnect_window(window)

    def restore_favorite_windows(self):
        for module_cls in MODULES:
            config_sect = getattr(self.config.data, module_cls.config_key, None)
            if config_sect and getattr(config_sect, "favorite", False):
                self._toggle_module(module_cls)
        # Not modules, but starred the same way and restored the same way
        if getattr(self.config.data.stats_window, "favorite", False):
            self._toggle_stats()
        if getattr(self.config.data.energy, "favorite", False):
            self._toggle_energy()

    # ── Drag ────────────────────────────────────────────────────────────────
    # The title and the handle keep their own bindings; these two let the
    # whole background do the same job. See BackgroundDragMixin.

    def _drag_begin(self, event):
        self._drag_press(event)

    def _drag_to(self, event):
        self._drag_move(event)

    # Only the mouse's travel is used, and the window's own starting point is
    # read in the space it is actually positioned in — see ModuleWindow for
    # why measuring the grab against Qt's geometry made the window shake.

    def _window_origin(self) -> QPoint:
        if self.wm.get_game_hwnd():
            origin = self.wm.window_origin(int(self.winId()))
            if origin is not None:
                return QPoint(*origin)
        return self.pos()

    def _drag_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()
            self._drag_from   = self._window_origin()

    def _drag_move(self, event):
        if not (event.buttons() & Qt.LeftButton):
            return
        target = self._drag_from + (event.globalPosition().toPoint()
                                    - self._drag_origin)
        if self.wm.get_game_hwnd():
            self.wm.move_window(int(self.winId()), target.x(), target.y(),
                                self.width(), self.height())
        else:
            self.move(target.x(), target.y())
        (self.config.data.overlay.x,
         self.config.data.overlay.y) = self.to_reference_offset(target.x(),
                                                                target.y())
        self._save_later.start()   # not once per mouse event — see the module window

    # ── Startup sequence ────────────────────────────────────────────────────

    def refresh_game_status(self):
        """Green while the game window is alive, red otherwise."""
        alive = self.wm.is_game_alive()
        if alive == self._game_ok:
            return
        self._game_ok = alive
        if alive:
            self._status_dot.set_running()
        else:
            self._status_dot.set_stopped()
        self._status_dot.setToolTip(
            "Игровое окно найдено" if alive else "Игровое окно не найдено"
        )

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_game_status()
        if not self._startup_shown:
            self._startup_shown = True
            QTimer.singleShot(200, self._startup_sequence)

    def _startup_sequence(self):
        from app.ui.widgets.log_panel import CLAUDE_ORANGE

        def step2():
            # Blank spacer line between the signature and the real log
            self.log_panel.append("")
            self.log_panel.animate_log(
                segments=[
                    ("Запуск Avataria Helper — ", theme.TEXT_SECONDARY),
                    ("Успешно", theme.ACCENT_GREEN),
                ],
                delay_ms=120,
                on_done=self._flush_pending_logs,
            )

        self.log_panel.animate_log(
            segments=[("made by Deatser", CLAUDE_ORANGE)],
            include_ts=False,
            on_done=step2,
        )

    def _flush_pending_logs(self):
        self._startup_done = True
        pending, self._pending_logs = self._pending_logs, []
        for segments, level in pending:
            self.log_panel.add_log_segments(segments, level)

    # ── Close ────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if getattr(self, "_crt_started", False):
            self._panel.stop_background()
            QApplication.quit()   # the animation has played; really go now
            event.accept()
            return

        # На перезапуске спрашивать нечего: уходим не насовсем, и ждать
        # ответа некому — перезапускатель уже считает секунды.
        if not self._restarting and \
                not self.config.data.overlay.skip_close_confirm and \
                not self._confirm_close():
            event.ignore()
            return

        # Every window switches off at once, all on the same clock, so the
        # helper goes dark like one screen rather than several.
        for w in list(self._open_windows.values()):
            if w.isVisible():
                w.close()
        if self._stats_window:
            self._stats_window.close()
        if self._energy_window:
            self._energy_window.close()

        self._links.clear()   # nothing left to connect to
        if self._zone_calib is not None:
            self._zone_calib.clear()   # живёт в своём окне и само не закроется
        self.set_promo_watch_enabled(False)
        self._stop_pause_watch()
        self.crt_close_started()
        event.ignore()

    def _confirm_close(self) -> bool:
        dialog = NtConfirmDialog(
            title         = "Закрыть помощник",
            message       = "Вы уверены, что хотите закрыть помощник "
                            "и все включённые моды?",
            confirm_text  = "Закрыть",
            remember_text = "Не спрашивать снова",
            parent        = self,
        )
        if self.wm.get_game_hwnd():
            # Same rule as module windows: attach before the first show, or the
            # dialog's hover and checkbox repaints never reach the screen.
            self.wm.attach_child(int(dialog.winId()))
            self._center_dialog_in_game(dialog)
            # And again once the modal loop runs — Qt re-applies its own
            # screen-space geometry over ours when it shows the window.
            QTimer.singleShot(0, lambda: self._center_dialog_in_game(dialog))

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False

        if dialog.remembered:
            self.config.data.overlay.skip_close_confirm = True
            self.config.save()
        return True

    def _center_dialog_in_game(self, dialog):
        """Attached windows use game-client coordinates, not screen ones."""
        ov = self.config.data.overlay
        self.wm.move_window(
            int(dialog.winId()),
            ov.x + (ov.width  - dialog.width())  // 2,
            ov.y + (ov.height - dialog.height()) // 2,
            dialog.width(), dialog.height(),
        )
