"""Switching between PrintWindow and Windows Graphics Capture.

The switch has to be safe in the one way that matters: whatever happens to
the fast backend, a capture still comes back. A detector that raises instead
of returning a picture takes the whole round with it.
"""
import threading

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
        self.asked_fresh = None

    def grab(self, region=None, fresh=True):
        self.asked_fresh = fresh
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


# ── waiting for a frame, and asking not to ─────────────────────────────────
# Hockey polls the rink, the level strip and its debug snapshots from one
# thread — the GUI thread — where AvaDancers gives each watcher a QThread of
# its own. The novelty marker is per thread, so on one thread every poller
# draws from the same one: a strip reader that consumes a frame takes it away
# from the scan loop and blocks for _FRAME_WAIT_S doing it. Only a caller
# building a time series needs a frame nobody has read.

def _bare_session(picture):
    """A WindowSession with its frame already in hand, built without the
    library or a real window — the waiting is what is under test."""
    from app.core import wgc_capture

    session = wgc_capture.WindowSession.__new__(wgc_capture.WindowSession)
    session.hwnd = 1
    session._origin = (0, 0)
    session._frame = picture
    session._index = 1
    session._cv = threading.Condition()
    session._closed = False
    session._seen = threading.local()
    return session


def test_a_fresh_grab_consumes_the_frame():
    picture = np.full((4, 4, 3), 5, np.uint8)
    session = _bare_session(picture)

    assert np.array_equal(session.grab(), picture)
    assert getattr(session._seen, "index", 0) == 1


def test_a_stale_grab_never_waits():
    """The whole point: a poller that only wants to know what is on screen
    must not sit in the condition variable for half a second."""
    picture = np.full((4, 4, 3), 5, np.uint8)
    session = _bare_session(picture)
    session.grab()          # the only frame there is, now seen
    session._cv.wait = lambda *a, **k: pytest.fail("fresh=False must not wait")

    assert np.array_equal(session.grab(fresh=False), picture)


def test_a_stale_grab_leaves_the_frame_for_whoever_needs_it():
    """And it must not consume it either. The scan loop's timestamps are its
    measurements; a strip reader quietly eating every other frame halves the
    rate the model is fitted from."""
    picture = np.full((4, 4, 3), 5, np.uint8)
    session = _bare_session(picture)

    session.grab(fresh=False)
    session._cv.wait = lambda *a, **k: pytest.fail("the frame was never read")
    assert np.array_equal(session.grab(), picture)


def test_grab_window_passes_freshness_through(monkeypatch):
    from app.core import wgc_capture

    session = _FakeSession(np.full((6, 6, 3), 7, np.uint8))
    monkeypatch.setattr(wgc_capture, "available", lambda: True)
    monkeypatch.setattr(capture, "_wgc_session", lambda hwnd: session)
    capture.set_wgc_enabled(True)

    capture.grab_window(123)
    assert session.asked_fresh is True
    capture.grab_window(123, fresh=False)
    assert session.asked_fresh is False


def test_printwindow_ignores_freshness(monkeypatch):
    """It renders the window on the spot, so every frame it returns is new
    and there is nothing to ask it for."""
    fallback = np.full((5, 5, 3), 9, np.uint8)
    capture.set_wgc_enabled(False)
    monkeypatch.setattr(capture, "_print_window", lambda hwnd: (fallback, 0, 0))

    assert np.array_equal(capture.grab_window(123, fresh=False), fallback)


# ── Где лежал кадр ──────────────────────────────────────────────────────────
# origin запоминался один раз, при заведении сессии, и жил до её конца. А
# разворот игры из полноэкранки двигает окно всегда: кусок под детект после
# него вырезался не оттуда, найденная точка переводилась в экран не туда, и
# картинка игры мерилась по чужому месту — окна помощника слипались в углу.

def test_a_frame_brings_the_place_the_window_was_at(monkeypatch):
    from app.core import wgc_capture

    session = _bare_session(np.zeros((4, 4, 3), np.uint8))
    monkeypatch.setattr(wgc_capture, "frame_origin", lambda hwnd: (700, 400))

    session._store_frame(np.full((4, 4, 3), 9, np.uint8))

    assert session.origin == (700, 400)


def test_the_last_known_place_survives_a_window_that_will_not_answer(
        monkeypatch):
    """Окно закрывается — кадр от этого не становится хуже."""
    from app.core import wgc_capture

    session = _bare_session(np.zeros((4, 4, 3), np.uint8))

    def _gone(hwnd):
        raise OSError("окна больше нет")

    monkeypatch.setattr(wgc_capture, "frame_origin", _gone)
    session._store_frame(np.zeros((4, 4, 3), np.uint8))

    assert session.origin == (0, 0)
