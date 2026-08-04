# app/ui/stats_window.py
from __future__ import annotations
from pathlib import Path

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
                               QScrollArea, QWidget, QFrame,
                               QGraphicsDropShadowEffect)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QTimer, Signal

from app.core.duration import from_seconds
from app.core.stats import shown
from app.ui import theme
from app.ui.module_window import ModuleWindow
from app.ui.widgets.vw_panel import VwPanel, VIDEO_SUFFIXES
from app.ui.widgets.neon_section import NeonSection
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.segmented_control import SegmentedControl
from app.ui.widgets.stat_tile import StatTile

# Index into both the period SegmentedControl and _PERIOD_LABELS.
_PERIOD_ALL   = 0
_PERIOD_TODAY = 1
_PERIOD_MONTH = 2
_PERIOD_LABELS = ("всё время", "сегодня", "месяц")

# Set by hand (2026-08-04) to the size the window was actually left at —
# smaller than this and the period/view controls, tiles and scroll area
# start crowding each other rather than just scrolling past the edge.
_MIN_W = 519
_MIN_H = 657

_COUNTDOWN_MS = 1000

# Same auto-pick rule every module's own backdrop uses — see
# modules.ava_dancers.window._default_backdrop.
_BACKDROP_STEM  = "AvaStats"
_PROJECT_ROOT   = Path(__file__).resolve().parents[2]
_TEMPLATES      = _PROJECT_ROOT / "templates"
_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def _default_backdrop(video: bool = True) -> str:
    """First existing templates/AvaStats.* file."""
    moving = VIDEO_SUFFIXES + (".gif",)
    order  = moving + _STILL_SUFFIXES if video else _STILL_SUFFIXES + moving
    for suffix in order:
        candidate = _TEMPLATES / f"{_BACKDROP_STEM}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return ""


def _countdown_text(remaining_seconds: int) -> str:
    """The live H:MM:SS still left, worked out from wall-clock time — so
    it reads correctly even after the mod was closed for a while, not
    whatever was last written to disk — or "Доступна!" once it hits 0."""
    if remaining_seconds > 0:
        return from_seconds(remaining_seconds)
    return "Доступна!"


class StatsWindow(ModuleWindow):
    """Everything the helper has counted, in one place.

    A module window in every mechanical sense — dragged, resized and
    remembered the same way — but it owns no bot and no screen watching, so
    it is not in the module registry and the overlay opens it directly.
    """

    closed = Signal()

    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, stats, window_manager, overlay=None):
        # Not passed as parent_overlay: the base class would report this as a
        # module closing, and it is not one. The overlay listens to `closed`
        # instead, and `overlay` here is only somewhere to write a log line.
        super().__init__("Статистика", config, save_fn, parent_overlay=None)
        self._wm      = window_manager
        self._stats   = stats
        self._overlay = overlay
        self._period  = _PERIOD_ALL   # which SegmentedControl option is lit
        self._poll_timer: QTimer | None = None
        self.resize(max(getattr(config, "width",  _MIN_W), _MIN_W),
                    max(getattr(config, "height", _MIN_H), _MIN_H))
        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build_ui()
        self.restore_position()
        self._init_collapse(self._panel, window_manager)
        self.refresh()

        # One timer for everything that has to move on its own while this
        # window sits open: the countdown reads real wall-clock time, and
        # stats.json can change under us too — another module recording a
        # run, or a hand edit — so both get checked on the same second-by-
        # second beat. See _poll.
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(_COUNTDOWN_MS)

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        self._panel = VwPanel(self)
        # This window is tall and narrow next to a 16:9 backdrop, so it
        # crops down to a thin vertical slice of it — biased right, since
        # centred left the visually busier right half of the source
        # offscreen.
        self._panel.focus_x = 0.55
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        outer = QVBoxLayout(self._panel)
        outer.setContentsMargins(theme.PADDING, theme.PADDING,
                                 theme.PADDING, theme.PADDING)
        outer.setSpacing(theme.SPACING)

        outer.addLayout(self._build_header())
        outer.addWidget(self._hairline())
        outer.addSpacing(2)

        outer.addWidget(self._build_scroll_area(), stretch=1)

        # ── Backdrop: templates/AvaStats.* by default, video first ───────────
        self._panel.background_failed.connect(self._on_background_failed)
        self._apply_backdrop(fade=False)

        drag = NtDragHandle()
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        outer.addWidget(drag)

    # ── Backdrop ─────────────────────────────────────────────────────────────

    def _apply_backdrop(self, fade: bool = True):
        video    = getattr(self.config, "video_background", True)
        backdrop = getattr(self.config, "background", "") or _default_backdrop(video)
        if backdrop and not self._panel.set_background(backdrop, fade=fade):
            self._on_background_failed(f"Фон не загружен: {backdrop}")

    def _on_background_failed(self, message: str):
        if self._overlay:
            self._overlay.add_log(message, level="error")

    def _crt_open_ready(self) -> bool:
        """Hold the switch-on until the video backdrop has a frame to show."""
        return self._panel.backdrop_ready

    def _build_scroll_area(self) -> QScrollArea:
        """Everything below the header, scrollable — squeezing the window
        down used to compress the fixed gaps between sections instead of
        just hiding the overflow; a real minimum height (_MIN_H) plus a
        scrollbar for whatever does not fit is what a resizable panel
        with this much content actually needs."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        # Transparent all the way down: VwPanel paints its own backdrop
        # (video, photo or the drawn scene) behind everything, and a plain
        # QScrollArea would otherwise sit an opaque rectangle on top of it.
        scroll.setStyleSheet(f"""
            QScrollArea {{ background: transparent; border: none; }}
            QScrollArea > QWidget > QWidget {{ background: transparent; }}
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                border: none;
                margin: 2px 0px 2px {theme.PADDING}px;
            }}
            QScrollBar::handle:vertical {{
                background: {theme.BORDER_BRIGHT};
                border-radius: 3px;
                min-height: 24px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{ height: 0px; }}
        """)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACING)

        layout.addWidget(self._section_label("ИГРОК", theme.ACCENT))
        self._id_value   = self._add_row(layout, "ID")
        self._name_value = self._add_row(layout, "Имя")
        self._date_value = self._add_row(layout, "Регистрация")
        layout.addSpacing(6)

        layout.addLayout(self._build_controls())
        layout.addSpacing(2)

        layout.addWidget(self._build_games_section())
        layout.addSpacing(8)
        layout.addWidget(self._build_professions_section())
        layout.addStretch()

        scroll.setWidget(content)
        return scroll

    def _build_controls(self) -> QVBoxLayout:
        """Period — all-time / today / month — a segmented row with
        exactly one option lit; changes what refresh() reads."""
        col = QVBoxLayout()
        col.setSpacing(6)

        self._period_label = QLabel()
        self._period_label.setFont(theme.get_display_font(theme.FONT_SIZE_S))
        self._period_label.setStyleSheet(
            f"color:{theme.TEXT_PRIMARY}; background:transparent;")
        self._glow(self._period_label)
        self._refresh_period_label()
        col.addWidget(self._period_label)

        self._period_control = SegmentedControl(
            ["Всё время", "Сегодня", "Месяц"],
            accent=theme.ACCENT, active=self._period)
        self._period_control.changed.connect(self._on_period_changed)
        col.addWidget(self._period_control)

        return col

    def _refresh_period_label(self):
        self._period_label.setText(f"Статистика за {_PERIOD_LABELS[self._period]}")

    def _on_period_changed(self, idx: int):
        self._period = idx
        self._refresh_period_label()
        self.refresh(animate=True)

    def _build_games_section(self) -> NeonSection:
        section = NeonSection("Игры", theme.ACCENT_CYAN)
        body = section.body()

        body.addWidget(self._section_label("AVA DANCERS", theme.VW_MAGENTA))
        body.addLayout(self._build_tiles())
        body.addSpacing(6)

        body.addWidget(self._section_label("ХОККЕЙ", theme.HK_ICE))
        body.addLayout(self._build_hockey_tiles())
        body.addSpacing(6)

        body.addWidget(self._section_label("СНОУБОРД", theme.SB_STEEL))
        body.addLayout(self._build_snowboard_tiles())
        return section

    def _build_professions_section(self) -> NeonSection:
        section = NeonSection("Профессии", theme.ACCENT_GREEN)
        body = section.body()

        body.addWidget(self._section_label("УБОРЩИК", theme.JN_AMBER))
        body.addLayout(self._build_janitor_tiles())
        body.addSpacing(6)

        body.addWidget(self._section_label("САДОВНИК", theme.GD_OLIVE))
        body.addLayout(self._build_gardener_tiles())
        return section

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        title = QLabel("СТАТИСТИКА")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M))
        title.setStyleSheet(f"color:{theme.ACCENT_SOFT}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self.start_drag
        title.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)

        self._collapse_btn = NtButton("▲", accent=theme.ACCENT)
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        self._fav_btn = NtButton("★" if self.favorite else "☆",
                                 accent=theme.ACCENT_AMBER)
        self._fav_btn.setFixedSize(24, 24)
        self._fav_btn.clicked.connect(self._toggle_favorite)

        close_btn = NtButton("×", accent=theme.ACCENT_RED)
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._fav_btn)
        header.addWidget(self._collapse_btn)
        header.addWidget(close_btn)
        return header

    def _build_tiles(self) -> QGridLayout:
        grid, self._games_tile, self._gold_tile, self._silver_tile = \
            self._build_run_tiles(theme.ACCENT)
        return grid

    def _build_snowboard_tiles(self) -> QGridLayout:
        grid, self._sb_games_tile, self._sb_gold_tile, self._sb_silver_tile = \
            self._build_run_tiles(theme.SB_STEEL)
        return grid

    def _build_hockey_tiles(self) -> QGridLayout:
        grid, self._hk_games_tile, self._hk_gold_tile, self._hk_silver_tile = \
            self._build_run_tiles(theme.HK_ICE)
        return grid

    def _build_run_tiles(self, games_accent: str):
        """The three-wide games/gold/silver row every "farming run" module
        gets — same shape, only the "игр сыграно" tile's own accent
        changes between modules; gold and silver always read in their own
        colour no matter whose row they are in."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)   # QGridLayout's own default
                                              # margins were stacking on top
                                              # of the label above it
        grid.setSpacing(theme.SPACING)
        games_tile  = StatTile("игр сыграно", accent=games_accent)
        gold_tile   = StatTile("золота",  accent=theme.ACCENT_AMBER)
        silver_tile = StatTile("серебра", accent=theme.ACCENT_STEEL)
        for column, tile in enumerate((games_tile, gold_tile, silver_tile)):
            grid.addWidget(tile, 0, column)
        return grid, games_tile, gold_tile, silver_tile

    def _build_gardener_tiles(self) -> QGridLayout:
        """Same three-wide row Ava Dancers gets, but only one number
        belongs in a square of its own — the rest is a single wide tile,
        since a countdown reads better with room to breathe than squeezed
        into a square next to its neighbours."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.SPACING)
        self._cleanup_tile = StatTile("уборок сделано", accent=theme.GD_OLIVE)
        self._next_tile    = StatTile("Новая смена", accent=theme.GD_OLIVE)
        grid.addWidget(self._cleanup_tile, 0, 0)
        grid.addWidget(self._next_tile, 0, 1, 1, 2)
        # Without this, an unspanned column and a pair a span shares do not
        # reliably end up in the 1:2 ratio their column counts imply — Qt
        # sizes each from its own widgets' hints first and only falls back
        # to stretch for what is left over, which read backwards here.
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        return grid

    def _build_janitor_tiles(self) -> QGridLayout:
        """Same layout as Садовник's own row — one square tile plus one
        wide one, for the same reason: its own countdown, independent of
        Садовник's."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.SPACING)
        self._janitor_cleanup_tile = StatTile("уборок сделано",
                                              accent=theme.JN_AMBER)
        self._janitor_next_tile    = StatTile("Новая смена",
                                              accent=theme.JN_AMBER)
        grid.addWidget(self._janitor_cleanup_tile, 0, 0)
        grid.addWidget(self._janitor_next_tile, 0, 1, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        return grid

    # ── Small parts ──────────────────────────────────────────────────────────

    def _hairline(self) -> QLabel:
        line = QLabel()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{theme.BORDER_DIM};")
        return line

    def _section_label(self, text: str, accent: str) -> QLabel:
        label = QLabel(f"◈  {text}")
        label.setFont(theme.get_display_font(theme.FONT_SIZE_S))
        label.setStyleSheet(f"color:{accent}; background:transparent;")
        return label

    def _add_row(self, layout: QVBoxLayout, caption: str) -> QLabel:
        """Caption on the left, value on the right — the same bright,
        glowing white both, not a dim label next to a lit-up value."""
        row = QHBoxLayout()
        row.setSpacing(theme.SPACING)
        name = QLabel(caption)
        name.setFont(theme.get_mono_font(theme.FONT_SIZE_S, bold=True))
        name.setStyleSheet(f"color:{theme.TEXT_PRIMARY}; background:transparent;")
        self._glow(name)
        value = QLabel("—")
        value.setFont(theme.get_mono_font(theme.FONT_SIZE_S, bold=True))
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._glow(value)
        row.addWidget(name)
        row.addStretch()
        row.addWidget(value)
        layout.addLayout(row)
        return value

    def _glow(self, widget: QLabel, color: str = None, radius: float = 12.0):
        """A soft halo the label's own colour, not a plain drop shadow —
        what actually reads as "glowing" rather than just "bright"."""
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(radius)
        effect.setOffset(0, 0)
        effect.setColor(QColor(color or theme.TEXT_PRIMARY))
        widget.setGraphicsEffect(effect)

    # ── Favorite ─────────────────────────────────────────────────────────────
    # Same star as the module windows carry, and the same meaning: the
    # overlay reopens it on the next launch. See Overlay.restore_favourites.

    @property
    def favorite(self) -> bool:
        return bool(getattr(self.config, "favorite", False))

    def _toggle_favorite(self):
        self.config.favorite = not self.favorite
        self.save_fn()
        self._fav_btn.setText("★" if self.favorite else "☆")

        if self.favorite:
            verb, tail, colour = ("добавлено", " в автозагрузку при старте",
                                  theme.ACCENT_GREEN)
        else:
            verb, tail, colour = ("удалено", " из автозагрузки при старте",
                                  theme.ACCENT_AMBER)
        if self._overlay:
            self._overlay.add_log_segments([
                ("Окно Статистика ", theme.TEXT_SECONDARY),
                (verb, colour),
                (tail, theme.TEXT_SECONDARY),
            ])

    # ── Data ─────────────────────────────────────────────────────────────────

    def reload(self):
        """Re-read stats.json, then redraw — the file can be edited by hand."""
        self._stats.reload()
        self.refresh()

    def _poll(self):
        """Runs once a second while the window is open — see __init__.

        Re-reading stats.json here, not just trusting the in-memory
        StatsManager, is what catches a hand edit made while this window
        sits open, on top of whatever another module already changed in
        memory (which reload() picks up too, just redundantly).
        """
        self._stats.reload()
        self.refresh(animate=True)

    def refresh(self, animate: bool = False):
        """Redraw every tile from whichever source the current period
        picks out.

        animate=True (only from the once-a-second poll, or a period
        switch) runs each tile's own scramble reveal, but only the ones
        whose value actually changed — animate_value() itself no-ops
        otherwise — so a poll tick with nothing new to show causes no
        visible flicker at all.
        """
        player = self._stats.data.player
        self._set_value(self._id_value,
                        shown(player.player_id, "player_id"))
        self._set_value(self._name_value,
                        shown(player.player_name, "player_name"))
        self._set_value(self._date_value,
                        shown(player.registration_date, "registration_date"))

        # Loaded once, not once per module: this_month() sums every kept
        # day's own file from disk, and five separate calls to it would
        # just re-do that same work five times over.
        daily = None
        if self._period == _PERIOD_TODAY:
            daily = self._stats.daily.today()
        elif self._period == _PERIOD_MONTH:
            daily = self._stats.daily.this_month()

        def source(module_key: str):
            owner = daily if daily is not None else self._stats.data
            return getattr(owner, module_key)

        ava       = source("ava_dancers")
        hockey    = source("hockey")
        snowboard = source("snowboard")
        gardener  = source("gardener")
        janitor   = source("janitor")

        self._set_tile(self._games_tile, ava.games_played, animate)
        self._set_tile(self._gold_tile, ava.gold_won, animate)
        self._set_tile(self._silver_tile, ava.silver_won, animate)

        self._set_tile(self._sb_games_tile, snowboard.games_played, animate)
        self._set_tile(self._sb_gold_tile, snowboard.gold_won, animate)
        self._set_tile(self._sb_silver_tile, snowboard.silver_won, animate)

        self._set_tile(self._hk_games_tile, hockey.games_played, animate)
        self._set_tile(self._hk_gold_tile, hockey.gold_won, animate)
        self._set_tile(self._hk_silver_tile, hockey.silver_won, animate)

        self._set_tile(self._cleanup_tile, gardener.shifts_finished, animate)
        self._set_tile(self._janitor_cleanup_tile, janitor.shifts_finished, animate)
        self._update_countdown()

    def _set_tile(self, tile: StatTile, value: int, animate: bool):
        text = str(value)
        if animate:
            tile.animate_value(text)
        else:
            tile.set_value(text)

    def _update_countdown(self):
        # sync_*_countdown, not *_remaining_seconds: this timer is the one
        # place in the whole app that ticks once a second, so it is also
        # the one place that writes the live value back to stats.json —
        # see StatsManager.sync_gardener_countdown / sync_janitor_countdown.
        # One timer, two independent countdowns — Уборщик's own cooldown
        # never touches Садовник's.
        self._next_tile.set_value(
            _countdown_text(self._stats.sync_gardener_countdown()))
        self._janitor_next_tile.set_value(
            _countdown_text(self._stats.sync_janitor_countdown()))

    def _set_value(self, label: QLabel, text: str):
        """Unfilled values are marked, not hidden — that is the point of them."""
        empty  = text.startswith("%") and text.endswith("%")
        colour = theme.ACCENT_AMBER if empty else theme.TEXT_PRIMARY
        label.setText(text)
        label.setStyleSheet(f"color:{colour}; background:transparent;")

    # ── Window plumbing ──────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        # Re-read on every show: the pin button took the refresh button's
        # place, and stats.json can be edited by hand between openings.
        self.reload()
        QTimer.singleShot(30, self.repaint)

    def _teardown(self):
        self._panel.stop_background()
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        self.closed.emit()
