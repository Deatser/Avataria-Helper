# modules/ava_dancers/window.py
from __future__ import annotations
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer

from app.ui.module_window import ModuleWindow
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.vw_panel import VwPanel
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.log_panel import LogPanel
from app.ui import theme
from modules.ava_dancers.bot import AvaBot, split_tiles, detect_tile, TILE_REGION

_DEFAULT_W = 420
_DEFAULT_H = 330
_MIN_W     = 300
_MIN_H     = 280

_KEY_LABELS = ["A", "S", "W", "D"]


class AvaDancersWindow(ModuleWindow):
    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, window_manager, parent_overlay=None):
        super().__init__("Ava Dancers", config, save_fn, parent_overlay)
        self._wm  = window_manager
        self._bot: AvaBot | None = None
        w = getattr(config, "width",  _DEFAULT_W)
        h = getattr(config, "height", _DEFAULT_H)
        self.resize(max(w, _MIN_W), max(h, _MIN_H))
        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build_ui()
        self.restore_position()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        self._panel = VwPanel(self)
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING, theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        # ── Header ───────────────────────────────────────────────────────────
        header = QHBoxLayout()
        self._status_dot = NtStatusDot()
        title = QLabel("AVA DANCERS")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M))
        title.setStyleSheet(f"color:{theme.VW_CYAN}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self.start_drag
        title.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)

        self._fav_btn = NtButton("★" if self.config.favorite else "☆",
                                 accent=theme.VW_MAGENTA)
        self._fav_btn.setFixedSize(24, 24)
        self._fav_btn.clicked.connect(self._toggle_favorite)

        close_btn = NtButton("×", accent=theme.VW_MAGENTA)
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)

        header.addWidget(self._status_dot)
        header.addSpacing(6)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._fav_btn)
        header.addWidget(close_btn)
        layout.addLayout(header)

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.VW_BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(4)

        # ── Controls ─────────────────────────────────────────────────────────
        self._start_btn = NtButton("▶  СТАРТ", accent=theme.VW_CYAN)
        self._start_btn.clicked.connect(self._toggle_bot)
        layout.addWidget(self._start_btn)

        test_btn = NtButton("◎  ТЕСТ ДЕТЕКЦИИ", accent=theme.VW_PURPLE)
        test_btn.clicked.connect(self._test_detection)
        layout.addWidget(test_btn)

        # ── Tile status row ───────────────────────────────────────────────────
        tiles_row = QHBoxLayout()
        self._tile_dots: list[NtStatusDot] = []
        for label in _KEY_LABELS:
            col = QVBoxLayout()
            dot = NtStatusDot()
            lbl = QLabel(label)
            lbl.setFont(theme.get_display_font(theme.FONT_SIZE_S))
            lbl.setStyleSheet(f"color:{theme.VW_PURPLE}; background:transparent;")
            lbl.setAlignment(Qt.AlignCenter)
            col.addWidget(dot, 0, Qt.AlignCenter)
            col.addWidget(lbl, 0, Qt.AlignCenter)
            self._tile_dots.append(dot)
            tiles_row.addLayout(col)
        layout.addLayout(tiles_row)

        # ── Log (stretchy) ───────────────────────────────────────────────────
        self._log = LogPanel()
        self._log.setMinimumHeight(60)
        layout.addWidget(self._log, stretch=1)

        # ── Drag handle ──────────────────────────────────────────────────────
        drag = NtDragHandle(dot_color=theme.VW_BORDER)
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    # ── showEvent: force repaint to fix visual freeze on first show ──────────

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(30, self.repaint)

    # ── Bot control ──────────────────────────────────────────────────────────

    def _toggle_bot(self):
        if self._bot and self._bot.isRunning():
            self._stop_bot()
        else:
            self._start_bot()

    def _start_bot(self):
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("Игровое окно не найдено", level="error")
            return

        self._bot = AvaBot(hwnd, self.config.min_active)
        self._bot.key_pressed.connect(lambda k: self._log.add_log(f"▶ {k.upper()}"))
        self._bot.tile_detected.connect(self._on_tile_detected)
        self._bot.error.connect(lambda e: self._log.add_log(e, level="error"))
        self._bot.start()

        self._start_btn.set_active(True)
        self._start_btn.setText("■  СТОП")
        self._status_dot.set_running()
        self._log.add_log("Бот запущен", level="success")
        if self.parent_overlay:
            self.parent_overlay.add_log("Ava Dancers: бот запущен")

    def _stop_bot(self):
        if self._bot:
            self._bot.stop_bot()
            if not self._bot.wait(600):
                self._bot.terminate()
            self._bot = None

        self._start_btn.set_active(False)
        self._start_btn.setText("▶  СТАРТ")
        self._status_dot.set_offline()
        for dot in self._tile_dots:
            dot.set_offline()
        self._log.add_log("Бот остановлен")
        if self.parent_overlay:
            self.parent_overlay.add_log("Ava Dancers: бот остановлен")

    def _on_tile_detected(self, tile_id: int, key: str):
        if 0 <= tile_id < 4:
            dot = self._tile_dots[tile_id]
            dot.set_running()
            QTimer.singleShot(180, dot.set_offline)

    # ── Test detection ────────────────────────────────────────────────────────

    def _test_detection(self):
        from app.core.capture import ScreenCapture
        try:
            img   = ScreenCapture.get().grab(TILE_REGION)
            tiles = split_tiles(img)
            for i, tile in enumerate(tiles):
                white, active = detect_tile(tile, self.config.min_active)
                state = "АКТИВНА" if active else "пусто"
                self._log.add_log(f"{_KEY_LABELS[i]}: {white}px — {state}",
                                  "success" if active else "info")
        except Exception as e:
            self._log.add_log(str(e), level="error")

    # ── Favorite ─────────────────────────────────────────────────────────────

    def _toggle_favorite(self):
        self.config.favorite = not self.config.favorite
        self._fav_btn.setText("★" if self.config.favorite else "☆")
        self.save_fn()

    # ── Close ────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._bot and self._bot.isRunning():
            self._bot.stop_bot()
            if not self._bot.wait(600):
                self._bot.terminate()
        super().closeEvent(event)
