# tests/ui/test_crt_shutdown.py
import time

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from app.ui.crt_power_mixin import CrtPowerMixin
from app.ui.widgets.crt_shutdown import (DURATION_MS, POWER_ON_MS,
                                          CrtPowerOn, CrtShutdown)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()


class _Window(CrtPowerMixin, QWidget):
    def __init__(self):
        super().__init__()
        self.resize(240, 160)
        self._init_crt_power()
        self.torn_down = 0
        self.child = QLabel("content", self)

    def _teardown(self):
        self.torn_down += 1

    def closeEvent(self, event):
        if self.crt_close_started():
            event.ignore()
            return
        self._teardown()
        event.accept()


def test_the_first_close_is_deferred_and_the_second_goes_through(app):
    window = _Window()
    window.show()

    window.close()
    assert window.isVisible()        # still on screen, playing the animation
    assert window.torn_down == 0     # and holding on to whatever it owns

    _pump(app, DURATION_MS / 1000 + 0.4)

    assert window.torn_down == 1
    assert not window.isVisible()


def test_the_live_widgets_give_way_to_the_snapshot(app):
    window = _Window()
    window.show()
    effect = CrtShutdown(window)

    effect.start()

    assert not window.child.isVisible()   # or it would show through the band
    assert effect.isVisible()
    window.close()


def test_every_phase_paints_without_error(app):
    """Walks the whole run — a bad rect or colour would raise here."""
    window = _Window()
    window.show()
    effect = CrtShutdown(window)
    effect.start()

    for value in (0.0, 0.2, 0.49, 0.5, 0.7, 0.81, 0.82, 0.95, 1.0):
        effect._on_tick(value)
        effect.repaint()

    window.close()


def test_the_effect_never_eats_the_mouse(app):
    """It covers the window, so it must stay transparent to clicks."""
    from PySide6.QtCore import Qt

    window = _Window()
    effect = CrtShutdown(window)

    assert effect.testAttribute(Qt.WA_TransparentForMouseEvents)


# ── Switching on ────────────────────────────────────────────────────────────

def test_showing_a_window_plays_the_switch_on(app):
    window = _Window()

    window.show()

    effects = window.findChildren(CrtPowerOn)
    assert len(effects) == 1
    assert not window.child.isVisible()   # the snapshot is what is on screen
    _pump(app, POWER_ON_MS / 1000 + 0.3)

    assert window.child.isVisible()       # and the real widgets come back
    window.close()


def test_the_switch_on_runs_once_per_window(app):
    window = _Window()
    window.show()
    _pump(app, POWER_ON_MS / 1000 + 0.3)

    window.hide()
    window.show()

    assert window.findChildren(CrtPowerOn) == []
    window.close()


def test_widgets_that_were_hidden_stay_hidden(app):
    """A window has children it deliberately keeps out of sight — the
    settings sheet among them — and switching on must not reveal them."""
    window = _Window()
    secret = QLabel("hidden", window)
    secret.hide()
    window.show()
    _pump(app, POWER_ON_MS / 1000 + 0.3)

    assert window.child.isVisible()
    assert not secret.isVisible()
    window.close()


def test_the_switch_on_waits_for_content_that_is_not_ready(app):
    """The Ava Dancers case: its video backdrop has no frame for the first
    moments, and photographing then captures the fallback scene instead."""
    window = _Window()
    window.show()
    _pump(app, POWER_ON_MS / 1000 + 0.3)   # let the automatic one finish

    effect = CrtPowerOn(window)
    ready = [False]
    effect.start(ready=lambda: ready[0])

    assert effect._anim.state() != effect._anim.State.Running
    ready[0] = True
    _pump(app, 0.2)

    assert effect._anim.state() == effect._anim.State.Running
    window.close()


def test_the_wait_gives_up_rather_than_leaving_the_window_dark(app):
    from app.ui.widgets import crt_shutdown

    window = _Window()
    window.show()
    _pump(app, POWER_ON_MS / 1000 + 0.3)

    effect = CrtPowerOn(window)
    effect.start(ready=lambda: False)      # never ready
    effect._deadline = 0.0                 # as if the cap had passed
    _pump(app, 0.2)

    assert effect._anim.state() == effect._anim.State.Running
    window.close()


def test_the_switch_on_starts_from_the_dot_end(app):
    window = _Window()
    effect = CrtPowerOn(window)

    assert effect._t == 1.0        # last frame of the switch-off
    assert effect._anim.startValue() == 1.0
    assert effect._anim.endValue() == 0.0
    assert effect._anim.duration() == POWER_ON_MS


def test_a_second_close_during_the_animation_does_not_restart_it(app):
    window = _Window()
    window.show()

    assert window.crt_close_started() is True
    assert window.crt_close_started() is False   # already running

    _pump(app, DURATION_MS / 1000 + 0.4)
    window.close()
