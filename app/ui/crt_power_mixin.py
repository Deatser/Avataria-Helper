# app/ui/crt_power_mixin.py
"""Give a window the CRT switch-on when it appears and switch-off when it goes."""
from __future__ import annotations

from app.ui.widgets.crt_shutdown import CrtPowerOn, CrtShutdown


class CrtPowerMixin:
    """Both halves of the tube effect, on the window's own lifecycle.

    Closing happens twice: the first close is turned down and starts the
    animation, and when that ends it calls close() again for real. Subclasses
    put their teardown in _teardown() rather than closeEvent so it runs on the
    real pass only — stopping threads mid-animation would make it stutter, and
    doing it twice would be worse.

    Opening is simpler: the first showEvent plays the switch-on over a
    snapshot and hands the window back when it is done.
    """

    def _init_crt_power(self):
        self._crt_started = False
        self._crt_opened  = False

    # ── Opening ──────────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_crt_opened", False):
            self._crt_opened = True
            CrtPowerOn(self).start(ready=self._crt_open_ready)

    def _crt_open_ready(self) -> bool:
        """Whether the window is worth photographing yet.

        Overridden where the content needs a moment to exist — a video
        backdrop has no first frame at the instant the window is shown, and
        unfolding the placeholder behind it into view is precisely the stale
        picture the animation should not be revealing.
        """
        return True

    # ── Closing ──────────────────────────────────────────────────────────────

    def crt_close_started(self) -> bool:
        """True when this close should be deferred to the animation."""
        if getattr(self, "_crt_started", False):
            return False
        self._crt_started = True
        effect = CrtShutdown(self)
        effect.finished.connect(self.close)
        effect.start()
        # Announced at the start, not at the end: anything drawn between this
        # window and another — a node wire — has to retract while the window
        # is switching off, not after it has already gone.
        self._crt_closing()
        return True

    def _crt_closing(self):
        """Called once, the moment the switch-off begins."""

    def _teardown(self):
        """Release whatever the window owns. Runs once, on the real close."""
