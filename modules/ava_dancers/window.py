# modules/ava_dancers/window.py
from __future__ import annotations
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer

from app.ui.module_window import ModuleWindow
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_panel import NtPanel
from app.ui.widgets.nt_status_dot import NtStatusDot
from app.ui.widgets.log_panel import LogPanel
from app.ui import theme
from modules.ava_dancers.bot import AvaBot, split_tiles, detect_tile, TILE_REGION

WIDTH  = 280
HEIGHT = 330

_KEY_LABELS = ["A", "S", "W", "D"]


class AvaDancersWindow(ModuleWindow):

    def __init__(self, config, save_fn, window_manager, parent_overlay=None):
        super().__init__("AvaDancers", config, save_fn, parent_overlay)
        self._wm  = window_manager
        self._bot: AvaBot | None = None
        self.resize(WIDTH, HEIGHT)
        self._build_ui()
        self.restore_position()

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        panel = NtPanel(self)
        panel.setGeometry(0, 0, WIDTH, HEIGHT)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING, theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        # Header
        header = QHBoxLayout()
        self._status_dot = NtStatusDot()
        title = QLabel("AVADANCERS")
        title.setFont(theme.get_mono_font(theme.FONT_SIZE_M, bold=True))
        title.setStyleSheet(f"color:{theme.TEXT_PRIMARY}; background:transparent;")
        self._fav_btn = NtButton("★" if self.config.favorite else "☆")
        self._fav_btn.setFixedSize(24, 24)
        self._fav_btn.clicked.connect(self._toggle_favorite)
        close_btn = NtButton("×")
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
        sep.setStyleSheet(f"background:{theme.BORDER};")
        layout.addWidget(sep)
        layout.addSpacing(4)

        # Start / stop
        self._start_btn = NtButton("▶  START BOT")
        self._start_btn.clicked.connect(self._toggle_bot)
        layout.addWidget(self._start_btn)

        # Test detection
        test_btn = NtButton("◎  TEST DETECTION")
        test_btn.clicked.connect(self._test_detection)
        layout.addWidget(test_btn)

        # Tile status row
        tiles_row = QHBoxLayout()
        self._tile_dots: list[NtStatusDot] = []
        for label in _KEY_LABELS:
            col = QVBoxLayout()
            dot = NtStatusDot()
            lbl = QLabel(label)
            lbl.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
            lbl.setStyleSheet(f"color:{theme.TEXT_DIM}; background:transparent;")
            lbl.setAlignment(Qt.AlignCenter)
            col.addWidget(dot, 0, Qt.AlignCenter)
            col.addWidget(lbl, 0, Qt.AlignCenter)
            self._tile_dots.append(dot)
            tiles_row.addLayout(col)
        layout.addLayout(tiles_row)

        layout.addStretch()

        # Log
        self._log = LogPanel()
        self._log.setFixedHeight(80)
        layout.addWidget(self._log)

        # Drag bar
        drag = QLabel()
        drag.setFixedHeight(4)
        drag.setStyleSheet(f"background:{theme.BORDER_DIM};")
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    # ── Bot control ──────────────────────────────────────────────────────────

    def _toggle_bot(self):
        if self._bot and self._bot.isRunning():
            self._stop_bot()
        else:
            self._start_bot()

    def _start_bot(self):
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            self._log.add_log("game window not found", level="error")
            return

        self._bot = AvaBot(hwnd, self.config.min_active)
        self._bot.key_pressed.connect(lambda k: self._log.add_log(f"pressed {k.upper()}"))
        self._bot.tile_detected.connect(self._on_tile_detected)
        self._bot.error.connect(lambda e: self._log.add_log(e, level="error"))
        self._bot.start()

        self._start_btn.set_active(True)
        self._start_btn.setText("■  STOP BOT")
        self._status_dot.set_running()
        self._log.add_log("bot started", level="success")
        if self.parent_overlay:
            self.parent_overlay.add_log("AvaDancers: bot started")

    def _stop_bot(self):
        if self._bot:
            self._bot.stop_bot()
            self._bot.wait(600)
            self._bot = None

        self._start_btn.set_active(False)
        self._start_btn.setText("▶  START BOT")
        self._status_dot.set_offline()
        for dot in self._tile_dots:
            dot.set_offline()
        self._log.add_log("bot stopped")
        if self.parent_overlay:
            self.parent_overlay.add_log("AvaDancers: bot stopped")

    def _on_tile_detected(self, tile_id: int, key: str):
        if 0 <= tile_id < 4:
            dot = self._tile_dots[tile_id]
            dot.set_running()
            QTimer.singleShot(180, dot.set_offline)

    # ── Test detection ───────────────────────────────────────────────────────

    def _test_detection(self):
        from app.core.capture import ScreenCapture
        try:
            img   = ScreenCapture.get().grab(TILE_REGION)
            tiles = split_tiles(img)
            for i, tile in enumerate(tiles):
                white, active = detect_tile(tile, self.config.min_active)
                state = "ACTIVE" if active else "empty"
                self._log.add_log(f"{_KEY_LABELS[i]}: {white}px — {state}")
        except Exception as e:
            self._log.add_log(str(e), level="error")

    # ── Favorite ─────────────────────────────────────────────────────────────

    def _toggle_favorite(self):
        self.config.favorite = not self.config.favorite
        self._fav_btn.setText("★" if self.config.favorite else "☆")
        self.save_fn()

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._bot and self._bot.isRunning():
            self._bot.stop_bot()
            self._bot.wait(600)
        super().closeEvent(event)
