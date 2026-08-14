# app/core/wgc_capture.py
"""Windows Graphics Capture — the same picture as PrintWindow, faster.

PrintWindow asks the window to redraw itself into a GDI bitmap and hands the
result back through system memory. Measured against the game: **44.6ms a
frame**, which is what caps the tile detector at ~22 polls a second. WGC is
what the OS itself uses to record a window: the compositor already has the
window's surface on the GPU, and hands out frames as they are produced.
Measured against the same window: **a frame every 21ms, ~40 a second**.

It keeps the property PrintWindow was chosen for — it captures the *window*,
not the screen, so another window sitting on top (the helper's own overlays
included) does not get into the picture. What it cannot do is capture a
minimised window: there is no surface to hand out. The game has to stay open
on screen, though it may be covered.

Frames arrive on the library's own thread whenever the window redraws, so
this keeps the newest one and serves it to whoever asks. `grab` blocks until
a frame the caller has not already seen turns up, which is what keeps the
detector's loop paced by the game's own redraws instead of spinning on one
picture and reading the same note position over and over.

Coordinates
-----------
A WGC frame covers the window's *extended frame bounds* — the window as it
is actually drawn, without the invisible resize border GetWindowRect counts
in. On the game those differ: GetWindowRect says (-8, -8, 2568, 1400) while
the frame is 2560x1392 at (0, 0). Verified by cropping the same screen
rectangle out of both a WGC frame and a PrintWindow one and comparing pixels
— identical on static content.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

import numpy as np

_DWMWA_EXTENDED_FRAME_BOUNDS = 9

# How long grab() waits for a frame the caller has not seen yet. A redraw
# comes every ~21ms; well past that and the window is not drawing at all
# (minimised, or the game is idle), which is the caller's cue to fall back.
_FRAME_WAIT_S = 0.5

# Giving up on a session that has produced nothing at all — long enough for
# the compositor to hand over the first frame, short enough that a wrong
# setting is obvious rather than a hang.
_START_TIMEOUT_S = 2.0


def available() -> bool:
    """Is the library installed at all?"""
    try:
        import windows_capture  # noqa: F401
    except Exception:
        return False
    return True


def frame_origin(hwnd: int) -> tuple[int, int]:
    """Screen coordinates of a WGC frame's top-left corner.

    The window as drawn, not as GetWindowRect reports it — see this module's
    own note on coordinates.
    """
    rect = wintypes.RECT()
    ok = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd), ctypes.c_uint(_DWMWA_EXTENDED_FRAME_BOUNDS),
        ctypes.byref(rect), ctypes.sizeof(rect))
    if ok != 0:
        raise OSError(f"DwmGetWindowAttribute вернул {ok}")
    return int(rect.left), int(rect.top)


class WindowSession:
    """A running capture of one window, newest frame kept for readers."""

    def __init__(self, hwnd: int):
        from windows_capture import WindowsCapture

        self.hwnd = hwnd
        self._frame: np.ndarray | None = None
        self._index = 0
        self.origin = frame_origin(hwnd)
        self._cv = threading.Condition()
        self._closed = False
        self._seen = threading.local()

        capture = WindowsCapture(window_hwnd=hwnd, cursor_capture=False,
                                 draw_border=False)

        @capture.event
        def on_frame_arrived(frame, control):     # noqa: ANN001
            # The buffer belongs to the compositor and is only valid until
            # this returns, so it has to be copied out. Dropping the alpha
            # channel here rather than per reader keeps every consumer on
            # the plain BGR the rest of the codebase expects.
            picture = np.ascontiguousarray(frame.frame_buffer[:, :, :3])
            with self._cv:
                self._frame = picture
                self._index += 1
                self._cv.notify_all()

        @capture.event
        def on_closed():
            with self._cv:
                self._closed = True
                self._cv.notify_all()

        self._control = capture.start_free_threaded()

    def wait_ready(self, timeout: float = _START_TIMEOUT_S) -> bool:
        deadline = time.monotonic() + timeout
        with self._cv:
            while self._frame is None and not self._closed:
                if not self._cv.wait(max(0.0, deadline - time.monotonic())):
                    return False
        return self._frame is not None

    def grab(self, region: dict | None = None,
             fresh: bool = True) -> np.ndarray:
        """The newest frame this thread has not seen yet, cropped to region.

        Blocking on novelty rather than returning whatever is in hand is the
        whole point: a detector that reads the same picture twice measures a
        note as having stood still, and its speed fit is made of exactly
        those measurements.

        `fresh=False` is for the callers that are not measuring anything —
        a poller reading a counter off the screen, a one-off look to see
        whether the window is there. They get whatever frame is in hand,
        without waiting and without marking it seen.

        That second half matters more than it looks. The marker is per
        *thread*, and a window that drives every poll off one thread — as
        the hockey module does, off the GUI thread — has them all drawing
        from the same marker. A strip reader consuming frames there is not
        merely slow: it takes every other frame away from the loop whose
        timestamps are the measurement.
        """
        with self._cv:
            if fresh:
                last = getattr(self._seen, "index", 0)
                deadline = time.monotonic() + _FRAME_WAIT_S
                while self._index <= last and not self._closed:
                    if not self._cv.wait(max(0.0, deadline - time.monotonic())):
                        break
            if self._frame is None:
                raise RuntimeError("WGC: кадров от окна ещё не было")
            frame = self._frame
            if fresh:
                self._seen.index = self._index
        if region is None:
            return frame

        left, top = self.origin
        y0, x0 = region["top"] - top, region["left"] - left
        crop = frame[y0:y0 + region["height"], x0:x0 + region["width"]]
        return np.ascontiguousarray(crop)

    @property
    def frames_seen(self) -> int:
        """How many frames the window has produced since this started.

        Against the detector's own poll count it answers the one question a
        bare Hz figure cannot: whether the loop is waiting on the game to
        draw, or on itself to finish.
        """
        return self._index

    def stop(self):
        self._closed = True
        try:
            self._control.stop()
        except Exception:
            pass
        with self._cv:
            self._cv.notify_all()
