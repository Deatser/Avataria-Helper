# app/ui/stats_window.py
from __future__ import annotations
from pathlib import Path

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
                               QScrollArea, QWidget, QFrame,
                               QGraphicsDropShadowEffect)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QTimer, Signal

from app.core import energy_bar
from app.core.stats import shown
from app.ui import theme
from app.ui.module_window import ModuleWindow
from app.ui.widgets.vw_panel import VwPanel
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
# Высота пересчитана после того, как из окна уехали два ряда игр и обе
# плитки отсчёта: со старым минимумом окно уже нельзя было подобрать по
# содержимому, под ним оставалась пустая полоса.
_MIN_W = 519
_MIN_H = 470

_COUNTDOWN_MS = 1000

# Same auto-pick rule every module's own backdrop uses — see
# modules.ava_dancers.window._default_backdrop.
_BACKDROP_STEM  = "AvaStats"
_PROJECT_ROOT   = Path(__file__).resolve().parents[2]
_TEMPLATES      = _PROJECT_ROOT / "templates"
_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

# Верхние строки лежат прямо на фоне окна, а фон бывает светлым — видео
# AvaStats светлее собственной заставки, и светлые буквы на нём таяли.
# Поэтому ореол под текстом теперь не «свечение своим цветом», а тёмная
# подложка: буквы остаются светлыми и читаются на любом кадре.
_HALO_COLOR = "#05040a"
_HALO_BLUR  = 18.0


def _default_backdrop() -> str:
    """First existing templates/AvaStats.* file."""
    for suffix in _STILL_SUFFIXES:
        candidate = _TEMPLATES / f"{_BACKDROP_STEM}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return ""


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

        # stats.json меняется под нами — другой мод записал прогон, или
        # файл поправили руками, — а окно должно это показывать, не дожидаясь
        # повторного открытия. Отсюда и посекундный такт. См. _poll.
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

        # ── Backdrop: templates/AvaStats.* by default ────────────────────────
        self._panel.background_failed.connect(self._on_background_failed)
        self._apply_backdrop(fade=False)

        drag = NtDragHandle()
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        outer.addWidget(drag)

    # ── Backdrop ─────────────────────────────────────────────────────────────

    def _apply_backdrop(self, fade: bool = True):
        backdrop = getattr(self.config, "background", "") or _default_backdrop()
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
        self._id_value    = self._add_row(layout, "ID")
        self._name_value  = self._add_row(layout, "Имя")
        self._date_value  = self._add_row(layout, "Регистрация")
        self._promo_value = self._add_row(layout, "Автоматическая активация промокодов")
        self._promo_count_value = self._add_row(layout, "Кол-во активированных промокодов")
        layout.addSpacing(6)

        layout.addLayout(self._build_controls())
        layout.addSpacing(2)

        layout.addWidget(self._build_games_section())
        layout.addSpacing(8)
        layout.addWidget(self._build_professions_section())
        layout.addSpacing(8)
        layout.addWidget(self._build_panel_section())
        layout.addStretch()

        scroll.setWidget(content)
        return scroll

    def _build_controls(self) -> QVBoxLayout:
        """Period — all-time / today / month — a segmented row with
        exactly one option lit; changes what refresh() reads."""
        col = QVBoxLayout()
        col.setSpacing(6)

        self._period_label = QLabel()
        self._period_label.setFont(
            theme.get_display_font(theme.FONT_SIZE_S, bold=True))
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
        """Пока только Ava Dancers: Хоккей и Сноуборд стоят на доске мода
        погашенными (см. overlay._WIP_MODULES), и считать по ним нечего."""
        section = NeonSection("Игры", theme.ACCENT_CYAN)
        body = section.body()

        body.addWidget(self._section_label("AVA DANCERS", theme.VW_MAGENTA))
        body.addLayout(self._build_tiles())
        return section

    def _build_professions_section(self) -> NeonSection:
        """Обе профессии одним блоком — по плитке в ряд, и на каждой ровно
        одно число: сколько смен закрыто. Отсчёт до новой смены занимал тут
        целую строку на всю ширину и про накопленное ничего не говорил."""
        section = NeonSection("Профессии", theme.ACCENT_GREEN)
        body = section.body()

        body.addWidget(self._section_label("СМЕН ЗАВЕРШЕНО",
                                           theme.ACCENT_GREEN))
        body.addLayout(self._build_profession_tiles())
        return section

    def _build_panel_section(self) -> NeonSection:
        """The helper's own windows — for now just Энергия's cafe runs."""
        section = NeonSection("Панель", theme.EN_YELLOW)
        body = section.body()

        body.addWidget(self._section_label("ЭНЕРГИЯ", theme.EN_YELLOW))
        body.addLayout(self._build_energy_tiles())
        body.addSpacing(6)
        body.addLayout(self._build_treat_tiles())
        return section

    def _build_energy_tiles(self) -> QGridLayout:
        """What a cafe run costs and what it brought — three across."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.SPACING)
        self._energy_tile = StatTile("энергии куплено", accent=theme.EN_YELLOW)
        self._energy_gold_tile = StatTile("золота потрачено",
                                          accent=theme.ACCENT_AMBER)
        self._energy_silver_tile = StatTile("серебра потрачено",
                                            accent=theme.ACCENT_STEEL)
        for column, tile in enumerate((self._energy_tile,
                                       self._energy_gold_tile,
                                       self._energy_silver_tile)):
            grid.addWidget(tile, 0, column)
        return grid

    def _build_treat_tiles(self) -> QGridLayout:
        """One tile per sweet on the cafe's menu, each in its own colour."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.SPACING)
        self._pie_tile = StatTile("Пирожок", accent=energy_bar.PIE.color)
        self._cheesecake_tile = StatTile("Чизкейк",
                                         accent=energy_bar.CHEESECAKE.color)
        self._brownie_tile = StatTile("Брауни",
                                      accent=energy_bar.BROWNIE.color)
        for column, tile in enumerate((self._pie_tile, self._cheesecake_tile,
                                       self._brownie_tile)):
            grid.addWidget(tile, 0, column)
        return grid

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        title = QLabel("СТАТИСТИКА")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M, bold=True))
        title.setStyleSheet(f"color:{theme.ACCENT_SOFT}; background:transparent;")
        self._glow(title)
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
        """Ряд Ava Dancers: сыграно / золото / серебро. Золото и серебро
        всегда читаются в своём цвете, счёт игр — в цвете самого мода."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)   # QGridLayout's own default
                                              # margins were stacking on top
                                              # of the label above it
        grid.setSpacing(theme.SPACING)
        self._games_tile  = StatTile("игр сыграно", accent=theme.ACCENT)
        self._gold_tile   = StatTile("золота",  accent=theme.ACCENT_AMBER)
        self._silver_tile = StatTile("серебра", accent=theme.ACCENT_STEEL)
        for column, tile in enumerate((self._games_tile, self._gold_tile,
                                       self._silver_tile)):
            grid.addWidget(tile, 0, column)
        return grid

    def _build_profession_tiles(self) -> QGridLayout:
        """По плитке на профессию, рядом — каждая в своём цвете."""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.SPACING)
        self._janitor_shifts_tile  = StatTile("Уборщик", accent=theme.JN_AMBER)
        self._gardener_shifts_tile = StatTile("Садовник", accent=theme.GD_OLIVE)
        for column, tile in enumerate((self._janitor_shifts_tile,
                                       self._gardener_shifts_tile)):
            grid.addWidget(tile, 0, column)
        return grid

    # ── Small parts ──────────────────────────────────────────────────────────

    def _hairline(self) -> QLabel:
        line = QLabel()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background:{theme.BORDER_DIM};")
        return line

    def _section_label(self, text: str, accent: str) -> QLabel:
        label = QLabel(f"◈  {text}")
        label.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        label.setStyleSheet(f"color:{accent}; background:transparent;")
        self._glow(label)
        return label

    def _add_row(self, layout: QVBoxLayout, caption: str) -> QLabel:
        """Подпись слева, значение справа — обе жирным поверх тёмной
        подложки, но значение чисто белое: строка читается сразу, и
        глазу есть за что зацепиться."""
        row = QHBoxLayout()
        row.setSpacing(theme.SPACING)
        name = QLabel(f"{caption}:")
        name.setFont(theme.get_mono_font(theme.FONT_SIZE_S, bold=True))
        name.setStyleSheet(f"color:{theme.TEXT_PRIMARY}; background:transparent;")
        self._glow(name)
        value = QLabel("—")
        value.setFont(theme.get_mono_font(theme.FONT_SIZE_S, bold=True))
        value.setStyleSheet(
            f"color:{theme.ACCENT_WHITE}; background:transparent;")
        value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._glow(value)
        row.addWidget(name)
        row.addStretch()
        row.addWidget(value)
        layout.addLayout(row)
        return value

    def _glow(self, widget: QLabel, color: str = None, radius: float = None):
        """Ореол под текстом — тёмный, а не «свечение своим цветом».

        Раньше он подсвечивал буквы их же цветом: на тёмном кадре фона это
        читалось как свечение, а на светлом светлое по светлому попросту
        сливалось. Тёмная подложка держит контраст на любом кадре, и
        буквы при этом остаются светлыми — см. _HALO_COLOR."""
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(_HALO_BLUR if radius is None else radius)
        effect.setOffset(0, 0)
        effect.setColor(QColor(color or _HALO_COLOR))
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

        promo_on = bool(self._overlay and getattr(
            self._overlay.config.data.promo, "detect_enabled", False))
        self._promo_value.setText("ВКЛ" if promo_on else "ВЫКЛ")
        self._promo_value.setStyleSheet(
            f"color:{theme.ACCENT_GREEN if promo_on else theme.ACCENT_RED}; "
            f"background:transparent;")

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
        gardener  = source("gardener")
        janitor   = source("janitor")
        promo     = source("promo")
        energy    = source("energy")

        self._set_value(self._promo_count_value, str(promo.activated))

        self._set_tile(self._games_tile, ava.games_played, animate)
        self._set_tile(self._gold_tile, ava.gold_won, animate)
        self._set_tile(self._silver_tile, ava.silver_won, animate)

        self._set_tile(self._janitor_shifts_tile,
                       janitor.shifts_finished, animate)
        self._set_tile(self._gardener_shifts_tile,
                       gardener.shifts_finished, animate)

        self._set_tile(self._energy_tile, energy.energy_bought, animate)
        self._set_tile(self._energy_gold_tile, energy.gold_spent, animate)
        self._set_tile(self._energy_silver_tile, energy.silver_spent, animate)
        self._set_tile(self._pie_tile, energy.pie_bought, animate)
        self._set_tile(self._cheesecake_tile, energy.cheesecake_bought, animate)
        self._set_tile(self._brownie_tile, energy.brownie_bought, animate)

    def _set_tile(self, tile: StatTile, value: int, animate: bool):
        text = str(value)
        if animate:
            tile.animate_value(text)
        else:
            tile.set_value(text)

    def _set_value(self, label: QLabel, text: str):
        """Unfilled values are marked, not hidden — that is the point of them."""
        empty  = text.startswith("%") and text.endswith("%")
        colour = theme.ACCENT_AMBER if empty else theme.ACCENT_WHITE
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
