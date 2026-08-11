"""Switching between PrintWindow and Windows Graphics Capture.

The switch has to be safe in the one way that matters: whatever happens to
the fast backend, a capture still comes back. A detector that raises instead
of returning a picture takes the whole round with it.
"""
import numpy as np
import pytest

from app.core import capture


@pytest.fixture(autouse=True)
def _leave_it_off():
    yield
    capture.set_wgc_enabled(False)


class _FakeSession:
    origin = (0, 0)

    def __init__(self, image=None, raises=None):
        self.image = image if image is not None else np.zeros((4, 4, 3), np.uint8)
        self.raises = raises
        self.stopped = False

    def grab(self, region=None):
        if self.raises:
            raise self.raises
        return self.image

    def stop(self):
        self.stopped = True


def test_off_by_default():
    capture.set_wgc_enabled(False)
    assert capture.wgc_enabled() is False


def test_asking_for_it_without_the_library_leaves_it_off(monkeypatch):
    """The setting has to report back what actually took, so a toggle that
    silently did nothing cannot be mistaken for one that worked."""
    from app.core import wgc_capture

    monkeypatch.setattr(wgc_capture, "available", lambda: False)
    assert capture.set_wgc_enabled(True) is False
    assert capture.wgc_enabled() is False


def test_when_on_the_fast_backend_serves_the_frame(monkeypatch):
    from app.core import wgc_capture

    picture = np.full((6, 6, 3), 7, np.uint8)
    monkeypatch.setattr(wgc_capture, "available", lambda: True)
    monkeypatch.setattr(capture, "_wgc_session", lambda hwnd: _FakeSession(picture))
    monkeypatch.setattr(capture, "_print_window",
                        lambda hwnd: pytest.fail("PrintWindow should not run"))
    capture.set_wgc_enabled(True)

    assert np.array_equal(capture.grab_window(123), picture)


def test_a_failing_session_falls_back_to_printwindow(monkeypatch):
    """Minimised, closed, moved — PrintWindow can still answer, and the round
    carries on."""
    from app.core import wgc_capture

    fallback = np.full((8, 8, 3), 3, np.uint8)
    dead = _FakeSession(raises=RuntimeError("нет кадров"))
    monkeypatch.setattr(wgc_capture, "available", lambda: True)
    monkeypatch.setattr(capture, "_wgc_session", lambda hwnd: dead)
    monkeypatch.setattr(capture, "_print_window", lambda hwnd: (fallback, 0, 0))
    capture.set_wgc_enabled(True)

    assert np.array_equal(capture.grab_window(123), fallback)
    assert dead.stopped, "a session that failed must not be inherited by the next call"


def test_a_session_that_never_starts_falls_back(monkeypatch):
    from app.core import wgc_capture

    fallback = np.full((5, 5, 3), 9, np.uint8)
    monkeypatch.setattr(wgc_capture, "available", lambda: True)
    monkeypatch.setattr(capture, "_wgc_session", lambda hwnd: None)
    monkeypatch.setattr(capture, "_print_window", lambda hwnd: (fallback, 0, 0))
    capture.set_wgc_enabled(True)

    assert np.array_equal(capture.grab_window(123), fallback)


def test_turning_it_off_stops_the_sessions(monkeypatch):
    from app.core import wgc_capture

    monkeypatch.setattr(wgc_capture, "available", lambda: True)
    capture.set_wgc_enabled(True)
    session = _FakeSession()
    capture._wgc_sessions[42] = session

    capture.set_wgc_enabled(False)

    assert session.stopped
    assert capture._wgc_sessions == {}


# ── where a full-window capture starts on screen ────────────────────────────

def test_origin_is_the_window_rect_under_printwindow(monkeypatch):
    capture.set_wgc_enabled(False)
    monkeypatch.setattr(capture.win32gui, "GetWindowRect",
                        lambda hwnd: (-8, -8, 2568, 1400))
    assert capture.window_frame_origin(7) == (-8, -8)


def test_origin_is_the_frames_own_under_wgc(monkeypatch):
    """The two backends disagree by the width of the invisible resize
    border, and anything reading a position out of a full-window capture has
    to be told which one framed it. Getting this wrong put the playfield 8px
    out — see the note on window_frame_origin."""
    from app.core import wgc_capture

    monkeypatch.setattr(wgc_capture, "available", lambda: True)
    monkeypatch.setattr(capture.win32gui, "GetWindowRect",
                        lambda hwnd: (-8, -8, 2568, 1400))
    capture.set_wgc_enabled(True)
    capture._wgc_sessions[7] = _FakeSession()

    assert capture.window_frame_origin(7) == (0, 0)


def test_origin_falls_back_when_no_session_is_running(monkeypatch):
    from app.core import wgc_capture

    monkeypatch.setattr(wgc_capture, "available", lambda: True)
    monkeypatch.setattr(capture.win32gui, "GetWindowRect",
                        lambda hwnd: (-8, -8, 2568, 1400))
    capture.set_wgc_enabled(True)
    assert capture.window_frame_origin(999) == (-8, -8)
