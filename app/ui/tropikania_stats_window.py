# app/ui/tropikania_stats_window.py
"""Tropikania's own Статистика — same construction as the main app's
StatsWindow (module_window base, VwPanel backdrop, period control, glowing
rows and tiles), trimmed to what Tropikania actually tracks: player info
with no promo-code rows, and one farm section instead of the games/
professions ones.
"""
from __future__ import annotations

from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
                               QScrollArea, QWidget, QFrame,
                               QGraphicsDropShadowEffect)
from PySide6.QtGui import QColor
from PySide6.QtCore import Qt, QTimer, Signal

from app.core.paths import TEMPLATES
from app.core.stats import shown
from app.ui import theme
from app.ui.module_window import ModuleWindow
from app.ui.widgets.vw_panel import VwPanel
from app.ui.widgets.neon_section import NeonSection
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.segmented_control import SegmentedControl
from app.ui.widgets.stat_tile import StatTile

# Index into both the period SegmentedControl and _PERIOD_LABELS — same
# three-way split the main app's StatsWindow offers.
_PERIOD_ALL   = 0
_PERIOD_TODAY = 1
_PERIOD_MONTH = 2
_PERIOD_LABELS = ("всё время", "сегодня", "месяц")

_MIN_W = 460
_MIN_H = 420

# Same backdrop file as the main app's own StatsWindow — "такой же фон",
# by explicit request, not a Tropikania-specific asset.
_BACKDROP_STEM  = "AvaStats"
_TEMPLATES      = TEMPLATES
_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def _default_backdrop() -> str:
    for suffix in _STILL_SUFFIXES:
        candidate = _TEMPLATES / f"{_BACKDROP_STEM}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return ""


class TropikaniaStatsWindow(ModuleWindow):
    """Everything the Tropikania helper has counted, in one place."""

    closed = Signal()

    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, stats, window_manager, overlay=None,
                player_stats=None):
        super().__init__("Статистика", config, save_fn, parent_overlay=None)
        self._wm      = window_manager
        self._stats   = stats
        self._overlay = overlay
        # ID/имя/регистрация come from the main app's own StatsManager —
        # same player, one source of truth. Falls back to a fresh one (all
        # placeholders) if this window is ever opened without it.
        if player_stats is None:
            from app.core.stats import StatsManager
            player_stats = StatsManager()
        self._player_stats = player_stats
        self._period  = _PERIOD_ALL
        self._poll_timer: QTimer | None = None
        self.resize(max(getattr(config, "width",  _MIN_W), _MIN_W),
                    max(getattr(config, "height", _MIN_H), _MIN_H))
        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build_ui()
        self.restore_position()
        self._init_collapse(self._panel, window_manager)
        self.refresh()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(1000)

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        self._panel = VwPanel(self)
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
        return self._panel.backdrop_ready

    def _build_scroll_area(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
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

        layout.addWidget(self._section_label("ИГРОК", theme.ACCENT_GREEN))
        self._id_value   = self._add_row(layout, "ID")
        self._name_value = self._add_row(layout, "Имя")
        self._date_value = self._add_row(layout, "Регистрация")
        layout.addSpacing(6)

        layout.addLayout(self._build_controls())
        layout.addSpacing(2)

        layout.addWidget(self._build_farm_section())
        layout.addStretch()

        scroll.setWidget(content)
        return scroll

    def _build_controls(self) -> QVBoxLayout:
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
            accent=theme.ACCENT_GREEN, active=self._period)
        self._period_control.changed.connect(self._on_period_changed)
        col.addWidget(self._period_control)

        return col

    def _refresh_period_label(self):
        self._period_label.setText(f"Статистика за {_PERIOD_LABELS[self._period]}")

    def _on_period_changed(self, idx: int):
        self._period = idx
        self._refresh_period_label()
        self.refresh(animate=True)

    def _build_farm_section(self) -> NeonSection:
        section = NeonSection("Фарм", theme.ACCENT_GREEN)
        body = section.body()

        body.addWidget(self._section_label("ФАРМ ОПЫТА", theme.ACCENT_GREEN))
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(theme.SPACING)
        self._exp_tile = StatTile("опыта получено от фарма черники",
                                  accent=theme.ACCENT_GREEN)
        grid.addWidget(self._exp_tile, 0, 0)
        body.addLayout(grid)
        return section

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        title = QLabel("СТАТИСТИКА")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M))
        title.setStyleSheet(f"color:{theme.ACCENT_SOFT}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self.start_drag
        title.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)

        self._collapse_btn = NtButton("▲", accent=theme.ACCENT_GREEN)
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
        row = QHBoxLayout()
        row.setSpacing(theme.SPACING)
        name = QLabel(f"{caption}:")
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
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(radius)
        effect.setOffset(0, 0)
        effect.setColor(QColor(color or theme.TEXT_PRIMARY))
        widget.setGraphicsEffect(effect)

    # ── Favorite ─────────────────────────────────────────────────────────────

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
        self._stats.reload()
        self._player_stats.reload()
        self.refresh()

    def _poll(self):
        self._stats.reload()
        self._player_stats.reload()
        self.refresh(animate=True)

    def refresh(self, animate: bool = False):
        player = self._player_stats.data.player
        self._set_value(self._id_value,
                        shown(player.player_id, "player_id"))
        self._set_value(self._name_value,
                        shown(player.player_name, "player_name"))
        self._set_value(self._date_value,
                        shown(player.registration_date, "registration_date"))

        if self._period == _PERIOD_TODAY:
            farm = self._stats.daily.today()
        elif self._period == _PERIOD_MONTH:
            farm = self._stats.daily.this_month()
        else:
            farm = self._stats.data.farm

        self._set_tile(self._exp_tile, farm.blueberry_exp, animate)

    def _set_tile(self, tile: StatTile, value: int, animate: bool):
        text = str(value)
        if animate:
            tile.animate_value(text)
        else:
            tile.set_value(text)

    def _set_value(self, label: QLabel, text: str):
        empty  = text.startswith("%") and text.endswith("%")
        colour = theme.ACCENT_AMBER if empty else theme.TEXT_PRIMARY
        label.setText(text)
        label.setStyleSheet(f"color:{colour}; background:transparent;")

    # ── Window plumbing ──────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self.reload()
        QTimer.singleShot(30, self.repaint)

    def _teardown(self):
        # The position/size save from a drag or resize is debounced
        # (ModuleWindow's own _save_later) — closing right after moving the
        # window could otherwise quit before that timer ever fires, silently
        # dropping the final position. Flushing here guarantees whatever's
        # in memory right now reaches tropikania_config.json.
        if self._save_later.isActive():
            self._save_later.stop()
            self.save_fn()
        self._panel.stop_background()
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        self.closed.emit()
