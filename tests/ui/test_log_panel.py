# tests/ui/test_log_panel.py
"""Clearing the log — the split-flap wipe."""
import time

import pytest
from PySide6.QtWidgets import QApplication

from app.ui import theme
from app.ui.widgets import log_panel
from app.ui.widgets.log_panel import LogPanel


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()


def _filled(app, lines=4):
    panel = LogPanel()
    for i in range(lines):
        panel.add_log_segments([(f"строка номер {i}", theme.TEXT_PRIMARY)],
                               level="plain")
    _pump(app, 1.0)          # let the reveal animations finish
    return panel


def test_a_wipe_empties_the_log(app):
    panel = _filled(app)
    assert panel.toPlainText().strip()

    panel.clear_logs()
    _pump(app, 2.5)

    assert panel.toPlainText().strip() == ""
    assert panel._wiping is False


def test_the_text_is_still_there_while_it_flips(app):
    """It should be an animation, not an instant blank."""
    panel = _filled(app)

    panel.clear_logs()
    _pump(app, 0.05)

    assert panel._wiping is True
    assert panel.toPlainText().strip() != ""
    _pump(app, 2.5)
    panel.clear_logs()


def test_an_empty_log_clears_without_a_fuss(app):
    panel = LogPanel()

    panel.clear_logs()

    assert panel.toPlainText().strip() == ""
    assert panel._wiping is False


def test_a_second_press_mid_wipe_finishes_it_at_once(app):
    panel = _filled(app)
    panel.clear_logs()
    _pump(app, 0.05)

    panel.clear_logs()          # impatient

    assert panel.toPlainText().strip() == ""


# ── The animation switch ────────────────────────────────────────────────────


@pytest.fixture
def no_animation():
    log_panel.set_animation_enabled(False)
    yield
    log_panel.set_animation_enabled(True)


def test_a_line_lands_whole_with_the_animation_off(app, no_animation):
    panel = LogPanel()

    panel.add_log_segments([("строка", theme.TEXT_PRIMARY)], level="plain")

    # No pumping: the text is there before the event loop gets a turn, and
    # it is the settled text, not scramble.
    assert "строка" in panel.toPlainText()


def test_clearing_is_instant_with_the_animation_off(app, no_animation):
    panel = LogPanel()
    for i in range(4):
        panel.add_log_segments([(f"строка номер {i}", theme.TEXT_PRIMARY)],
                               level="plain")

    panel.clear_logs()

    assert panel.toPlainText().strip() == ""
    assert panel._wiping is False


def test_on_done_still_fires_with_the_animation_off(app, no_animation):
    panel = LogPanel()
    calls = []

    panel.animate_log([("строка", theme.TEXT_PRIMARY)], include_ts=False,
                      on_done=lambda: calls.append(1))

    assert calls == [1]


# ── The frame builder, driven directly ──────────────────────────────────────

_SEGMENTS = [("абв", theme.TEXT_PRIMARY), ("где", theme.ACCENT_GREEN)]


def test_nothing_has_flipped_at_the_start(app):
    panel = LogPanel()

    frame = panel._flip_frame(_SEGMENTS, gone=0)

    assert "".join(text for text, _c in frame) == "абвгде"
    assert [colour for _t, colour in frame] == [theme.TEXT_PRIMARY,
                                                theme.ACCENT_GREEN]


def test_the_wave_leaves_blanks_behind_it(app):
    panel = LogPanel()

    frame = panel._flip_frame(_SEGMENTS, gone=99)

    assert "".join(text for text, _c in frame).strip() == ""


def test_mid_wave_there_are_settled_flipping_and_blank_parts(app):
    panel = LogPanel()

    # gone=4 is one full _FLIP_SPAN in: the first letter has finished
    # flipping and gone, the middle is still turning, the end is untouched.
    text = "".join(t for t, _c in panel._flip_frame(_SEGMENTS, gone=4))

    assert len(text) == len("абвгде")     # length never changes, it is a board
    assert text[0] == " "                 # the near end has already gone
    assert text[3] not in ("г", " ")      # mid-flip, showing some glyph
    assert text[4:] == "де"               # the far end has not been reached
