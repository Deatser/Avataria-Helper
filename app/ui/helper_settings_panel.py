# app/ui/helper_settings_panel.py
"""The main helper window's own settings sheet — anchored to Overlay the
same way every module's own "Настройки" sheet anchors to its own window,
except these three toggles are not any single window's own: video is a
value every window with a backdrop carries its own copy of (kept in step
here, see _toggle_video), the connector wires and the close-confirmation
dialog both live on OverlayConfig already.
"""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout

from app.ui import theme
from app.ui.sheet_panel import SheetPanel
from app.ui.widgets.neon_section import NeonSection
from app.ui.widgets.nt_switch import NtSwitch

_W = 360
_H = 290

# Every config section that carries its own video_background field — see
# the comment on OverlayConfig.video_background for why there is one copy
# per section rather than a single shared flag.
_VIDEO_SECTIONS = ("overlay", "ava_dancers", "snowboard", "hockey", "stats_window")


class HelperSettingsPanel(SheetPanel):
    def __init__(self, overlay):
        self._overlay = overlay
        super().__init__(overlay, "НАСТРОЙКИ", (_W, _H), accent=theme.ACCENT)

    # ── Body ─────────────────────────────────────────────────────────────────

    def _build_body(self, layout: QVBoxLayout):
        cfg = self._overlay.config.data.overlay
        section = NeonSection("Хелпер", theme.ACCENT)
        body = section.body()

        video_switch = NtSwitch("Видеофон во всех окнах", accent=theme.ACCENT)
        video_switch.set_checked_silently(getattr(cfg, "video_background", True))
        video_switch.toggled.connect(self._toggle_video)
        body.addWidget(video_switch)

        links_switch = NtSwitch("Линии между окнами", accent=theme.ACCENT)
        links_switch.set_checked_silently(getattr(cfg, "show_links", True))
        links_switch.toggled.connect(self._toggle_links)
        body.addWidget(links_switch)

        confirm_switch = NtSwitch("Подтверждать закрытие помощника",
                                  accent=theme.ACCENT)
        confirm_switch.set_checked_silently(
            not getattr(cfg, "skip_close_confirm", False))
        confirm_switch.toggled.connect(self._toggle_confirm)
        body.addWidget(confirm_switch)

        layout.addWidget(section)

    # ── Video, every window at once ─────────────────────────────────────────

    def _toggle_video(self, enabled: bool):
        data = self._overlay.config.data
        for name in _VIDEO_SECTIONS:
            section = getattr(data, name, None)
            if section is not None and hasattr(section, "video_background"):
                section.video_background = enabled
        self._overlay.config.save()
        self._refresh_backdrops()
        self._overlay.add_log_segments(
            [("Видеофон — ", theme.TEXT_SECONDARY),
             ("включён" if enabled else "выключен",
              theme.ACCENT_GREEN if enabled else theme.ACCENT_AMBER),
             (" везде", theme.TEXT_SECONDARY)])

    def _refresh_backdrops(self):
        """Every currently-open window that has a backdrop of its own
        re-reads the config value that _toggle_video just wrote — nothing
        with a video/photo panel is left showing the old choice."""
        for window in self._overlay.own_windows():
            apply_backdrop = getattr(window, "_apply_backdrop", None)
            if callable(apply_backdrop):
                apply_backdrop()

    # ── Connector wires ──────────────────────────────────────────────────────

    def _toggle_links(self, enabled: bool):
        self._overlay.config.data.overlay.show_links = enabled
        self._overlay.config.save()
        self._overlay._links.set_enabled(enabled)
        self._overlay.add_log_segments(
            [("Линии между окнами — ", theme.TEXT_SECONDARY),
             ("включены" if enabled else "выключены",
              theme.ACCENT_GREEN if enabled else theme.ACCENT_AMBER)],
            level="plain")   # a setting's new value, not an action's outcome

    # ── Close confirmation ───────────────────────────────────────────────────

    def _toggle_confirm(self, enabled: bool):
        self._overlay.config.data.overlay.skip_close_confirm = not enabled
        self._overlay.config.save()
        self._overlay.add_log_segments(
            [("Подтверждение закрытия — ", theme.TEXT_SECONDARY),
             ("включено" if enabled else "выключено",
              theme.ACCENT_GREEN if enabled else theme.ACCENT_AMBER)],
            level="plain")   # a setting's new value, not an action's outcome
