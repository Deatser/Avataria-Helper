# app/ui/promo_window.py
from __future__ import annotations
from html import escape
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout

from app.core.promo_watch import PromoCheck, PromoFetchLatest
from app.ui import theme
from app.ui.module_window import ModuleWindow
from app.ui.widgets.log_actions import build_log_actions
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.nt_button import NtButton
from app.ui.widgets.nt_drag_handle import NtDragHandle
from app.ui.widgets.vw_panel import VwPanel, VIDEO_SUFFIXES

_DEFAULT_W = 380
_DEFAULT_H = 440
_MIN_W     = 320
_MIN_H     = 380

_DETECT_TEXT = "▶  Запустить автоматический детект промокодов"
_FETCH_TEXT  = "🔍  Вывести данные по последним промокодам"

_ACTIVATE_LINE = "Нажмите чтобы активировать все доступные промокоды"

_STATUS_COLOR = {
    "valid":   theme.ACCENT_GREEN,
    "expired": theme.ACCENT_RED,
    "unknown": theme.ACCENT_AMBER,
}

_BACKDROP_STEM  = "AvaPromo"
_PROJECT_ROOT   = Path(__file__).resolve().parents[2]
_TEMPLATES      = _PROJECT_ROOT / "templates"
_STILL_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def _default_backdrop(video: bool = True) -> str:
    """First existing templates/AvaPromo.* file — same rule every other
    window's own backdrop picker uses."""
    moving = VIDEO_SUFFIXES + (".gif",)
    order  = moving + _STILL_SUFFIXES if video else _STILL_SUFFIXES + moving
    for suffix in order:
        candidate = _TEMPLATES / f"{_BACKDROP_STEM}{suffix}"
        if candidate.is_file():
            return str(candidate)
    return ""


def _entry_html(entry) -> str:
    """One "pr_XXX - Title [до ДД.ММ.ГГ]" line, code and date each an
    underlined, coloured `copy:`-anchored span — see LogPanel.mousePressEvent
    for what actually makes them clickable."""
    code_html = (f'<a href="copy:{entry.code}" '
                 f'style="color:{theme.ACCENT_CYAN}; text-decoration:underline;">'
                 f'{escape(entry.code)}</a>')

    title_text = escape(entry.title) if entry.title else "Не удалось определить содержимое"

    if entry.date_display:
        date_text = f"до {entry.date_display}"
    else:
        date_text = "не удалось определить срок активации"
    date_color = _STATUS_COLOR[entry.status]
    date_html  = (f'<span style="color:{date_color}; text-decoration:underline;">'
                  f'{date_text}</span>')

    return (f'<span style="color:{theme.TEXT_SECONDARY};">{code_html} - '
            f'{title_text} [{date_html}]</span>')


class PromoWindow(ModuleWindow):
    """Промокоды — not in the module registry (same reasoning as
    StatsWindow: no bot, no screen watching of its own — PromoWatch runs
    at the Overlay level so it keeps going with this window closed), the
    overlay opens it directly.
    """

    closed = Signal()

    _RESIZE_MIN_W = _MIN_W
    _RESIZE_MIN_H = _MIN_H

    def __init__(self, config, save_fn, window_manager, overlay=None):
        # Not parent_overlay: same reason StatsWindow skips it — the base
        # class would report this as a module closing, and it is not one.
        super().__init__("Промокоды", config, save_fn, parent_overlay=None)
        self._wm      = window_manager
        self._overlay = overlay
        self._fetcher: PromoFetchLatest | None = None
        self._checker: PromoCheck | None = None

        w = getattr(config, "width",  _DEFAULT_W)
        h = getattr(config, "height", _DEFAULT_H)
        self.resize(max(w, _MIN_W), max(h, _MIN_H))
        self.setMinimumSize(_MIN_W, _MIN_H)
        self._build_ui()
        self.restore_position()
        self._init_collapse(self._panel, window_manager)

    # ── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        self._panel = VwPanel(self)
        self._panel.setGeometry(0, 0, self.width(), self.height())
        self._panel.setMouseTracking(True)

        layout = QVBoxLayout(self._panel)
        layout.setContentsMargins(theme.PADDING, theme.PADDING,
                                  theme.PADDING, theme.PADDING)
        layout.setSpacing(theme.SPACING)

        layout.addLayout(self._build_header())

        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.BORDER_DIM};")
        layout.addWidget(sep)
        layout.addSpacing(4)

        warning = QLabel(
            "⚠ Нужен рабочий доступ к Telegram (VPN, если он заблокирован "
            "у провайдера)")
        warning.setWordWrap(True)
        warning.setFont(theme.get_mono_font(theme.FONT_SIZE_S))
        warning.setStyleSheet(f"color:{theme.ACCENT_AMBER}; background:transparent;")
        layout.addWidget(warning)
        layout.addSpacing(2)

        self._detect_btn = NtButton(_DETECT_TEXT, accent=theme.ACCENT_GREEN,
                                    upper=False)
        self._detect_btn.set_active(bool(getattr(self.config, "detect_enabled", False)))
        self._detect_btn.clicked.connect(self._toggle_detect)
        layout.addWidget(self._detect_btn)

        self._fetch_btn = NtButton(_FETCH_TEXT, accent=theme.ACCENT_CYAN,
                                   upper=False)
        self._fetch_btn.clicked.connect(self._fetch_latest)
        layout.addWidget(self._fetch_btn)

        layout.addLayout(self._build_log(), stretch=1)

        self._panel.background_failed.connect(
            lambda msg: self._log.add_log(msg, level="error"))
        self._apply_backdrop(fade=False)

        drag = NtDragHandle()
        drag.mousePressEvent = self.start_drag
        drag.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)
        layout.addWidget(drag)

    def _apply_backdrop(self, fade: bool = True):
        video    = getattr(self.config, "video_background", True)
        backdrop = getattr(self.config, "background", "") or _default_backdrop(video)
        if backdrop and not self._panel.set_background(backdrop, fade=fade):
            self._log.add_log(f"Фон не загружен: {backdrop}", level="error")

    def _crt_open_ready(self) -> bool:
        return self._panel.backdrop_ready

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()

        title = QLabel("ПРОМОКОДЫ")
        title.setFont(theme.get_display_font(theme.FONT_SIZE_M))
        title.setStyleSheet(f"color:{theme.ACCENT_CYAN}; background:transparent;")
        title.setCursor(Qt.SizeAllCursor)
        title.mousePressEvent = self.start_drag
        title.mouseMoveEvent  = lambda e: self.do_drag(e, self._wm)

        self._collapse_btn = NtButton("▲", accent=theme.ACCENT_CYAN)
        self._collapse_btn.setFixedSize(24, 24)
        self._collapse_btn.clicked.connect(self._toggle_collapse)

        close_btn = NtButton("×", accent=theme.ACCENT_RED)
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)

        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._collapse_btn)
        header.addWidget(close_btn)
        return header

    def _build_log(self) -> QVBoxLayout:
        block = QVBoxLayout()
        block.setSpacing(4)

        self._log = LogPanel()
        self._log.setMinimumHeight(160)

        head = QHBoxLayout()
        log_label = QLabel("Промокоды:")
        log_label.setFont(theme.get_display_font(theme.FONT_SIZE_S, bold=True))
        log_label.setStyleSheet(f"color:{theme.TEXT_PRIMARY}; background:transparent;")
        head.addWidget(log_label)
        head.addStretch()
        head.addLayout(build_log_actions(self._log, theme.ACCENT_CYAN))
        block.addLayout(head)

        block.addWidget(self._log, stretch=1)
        self._log.anchor_clicked.connect(self._on_log_anchor)
        return block

    def _on_log_anchor(self, href: str):
        if href == "activate:all":
            # The mechanism itself is a later step — see the button's own
            # log line for what is there today.
            self._log.add_log(
                "Активация всех промокодов — в разработке", level="plain")

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(30, self.repaint)

    # ── Automatic detection toggle ───────────────────────────────────────────

    def _toggle_detect(self):
        if getattr(self.config, "detect_enabled", False):
            self._set_detect(False)
            return

        if self._checker is not None:
            return   # already checking whether Telegram answers
        self._detect_btn.setEnabled(False)
        checker = PromoCheck()
        checker.ok.connect(self._on_detect_check_ok)
        checker.error.connect(self._on_detect_check_error)
        checker.finished.connect(self._on_detect_check_finished)
        self._checker = checker
        checker.start()

    def _on_detect_check_ok(self):
        self._set_detect(True)

    def _on_detect_check_error(self, message: str):
        self._log.add_log(f"Промокоды: {message}", level="error")

    def _on_detect_check_finished(self):
        self._checker = None
        self._detect_btn.setEnabled(True)

    def _set_detect(self, enabled: bool):
        self.config.detect_enabled = enabled
        self.save_fn()
        self._detect_btn.set_active(enabled)
        if self._overlay is not None:
            self._overlay.set_promo_watch_enabled(enabled)
        self._log.add_log(
            "Автодетект промокодов включён" if enabled
            else "Автодетект промокодов выключен",
            level="plain")

    # ── Latest codes ─────────────────────────────────────────────────────────

    def _fetch_latest(self):
        if self._fetcher is not None:
            return   # a fetch is already in flight
        self._fetch_btn.setEnabled(False)
        fetcher = PromoFetchLatest()
        fetcher.fetched.connect(self._on_fetched)
        fetcher.error.connect(self._on_fetch_error)
        fetcher.finished.connect(self._on_fetch_finished)
        self._fetcher = fetcher
        fetcher.start()

    def _on_fetch_finished(self):
        self._fetcher = None
        self._fetch_btn.setEnabled(True)

    def _on_fetch_error(self, message: str):
        self._log.add_log(f"Промокоды: {message}", level="error")

    def _on_fetched(self, entries: list):
        if not entries:
            self._log.add_log("Свежих промокодов не нашлось", level="plain")
            return
        for entry in entries:
            self._log.add_html(_entry_html(entry))
        self._log.blank_line()
        self._log.add_html(
            f'<a href="activate:all" style="color:{theme.ACCENT_SOFT}; '
            f'text-decoration:underline;">{_ACTIVATE_LINE}</a>')

    # ── Window plumbing ──────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self.crt_close_started():
            event.ignore()
            return
        self._teardown()
        self.closed.emit()
        event.accept()

    def _teardown(self):
        if self._fetcher is not None:
            self._fetcher.wait()
        if self._checker is not None:
            self._checker.wait()
