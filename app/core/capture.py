# app/core/capture.py
from __future__ import annotations
import ctypes
import os
import threading
import time
from threading import Lock
import mss
import mss.exception
import numpy as np
import cv2
import win32gui
import win32process
import win32ui

_RETRIES     = 3      # BitBlt can fail transiently — a retry usually succeeds
_RETRY_DELAY = 0.015  # seconds between attempts

# Asks a window to render its own content into the given DC, the same way
# it would paint to the screen — DWM fills it in even for a window that is
# fully covered or minimised, which plain BitBlt-based capture cannot do.
_PW_RENDERFULLCONTENT = 0x00000002


class ScreenCapture:
    """Screen grabber with one mss instance per thread.

    An mss object owns GDI device contexts bound to the thread that created
    it; sharing one across the bot thread and the GUI thread makes BitBlt fail
    intermittently. Each thread gets its own, built on first use.
    """

    _instance: ScreenCapture | None = None
    _init_lock = Lock()
    _tls = threading.local()

    @classmethod
    def get(cls) -> ScreenCapture:
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls.__new__(cls)
        return cls._instance

    @classmethod
    def release(cls):
        """Drop this thread's grabber — call before a worker thread exits."""
        sct = getattr(cls._tls, "sct", None)
        if sct is not None:
            try:
                sct.close()
            except Exception:
                pass
            cls._tls.sct = None

    def _sct(self):
        sct = getattr(self._tls, "sct", None)
        if sct is None:
            sct = mss.mss()
            self._tls.sct = sct
        return sct

    def grab(self, region: dict) -> np.ndarray:
        """Capture region and return BGR numpy array.

        mss draws through BitBlt with CAPTUREBLT, which fails transiently while
        the desktop is busy (layered windows redrawing, display mode change,
        locked session). Retry with a fresh device context; if it still fails
        the error propagates so the caller can report it.
        """
        last_error = None
        for _ in range(_RETRIES):
            try:
                raw = self._sct().grab(region)
                break
            except mss.exception.ScreenShotError as exc:
                last_error = exc
                ScreenCapture.release()
                time.sleep(_RETRY_DELAY)
        else:
            raise last_error

        img = np.array(raw)
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


def _own_descendant_rects(hwnd: int) -> list[tuple[int, int, int, int]]:
    """Screen rects of every descendant of hwnd that belongs to this process.

    attach_child/attach_overlay (see window_manager.py) make our own module
    windows real WS_CHILD windows of the game, which is what lets them move
    and minimise with it — but PW_RENDERFULLCONTENT renders a window's whole
    child tree, ours included, so without this a helper panel dragged over
    the game gets baked into the very capture the bot reads back as the game.
    An unrelated app on top is never in this tree at all (it is a sibling in
    the desktop's own stacking order, not a child of hwnd), which is why only
    our own windows ever caused this.
    """
    own_pid = os.getpid()
    rects: list[tuple[int, int, int, int]] = []

    def visit(child_hwnd, _):
        _, pid = win32process.GetWindowThreadProcessId(child_hwnd)
        if pid == own_pid and win32gui.IsWindowVisible(child_hwnd):
            rects.append(win32gui.GetWindowRect(child_hwnd))
        return True

    try:
        win32gui.EnumChildWindows(hwnd, visit, None)
    except win32gui.error:
        pass   # no children right now — nothing to blank out
    return rects


def _blank_own_windows(img: np.ndarray, abs_left: int, abs_top: int, hwnd: int):
    """Paint over any of our own windows caught inside img, in place.

    Tried patching these spots back in from a real screen grab instead of
    blanking them, on the theory that WDA_EXCLUDEFROMCAPTURE (no_capture.py)
    would make that grab skip straight past our own window to the game
    underneath — confirmed live that the flag itself does work on this
    machine (a plain top-level probe window vanished from an mss capture as
    expected), but not for these specific windows: attach_child/attach_overlay
    (window_manager.py) make them real WS_CHILD windows of the game, and DWM
    does not seem to honour the flag at that granularity — the patch just
    painted the mod window's own pixels straight back in.

    Blank is the safe fallback: every detector this codebase has reads "very
    dark" as background/empty (AvaBot's own _IDLE_V, the gardener/janitor
    template matchers all fail closed on a blank crop), so a lane genuinely
    covered by one of our own windows reads as nothing happening there rather
    than as some wrong, confidently-misread colour.

    Coordinates come back from Windows in absolute screen space; img's own
    origin is (abs_left, abs_top), so each rect is shifted by that before it
    can be used to slice img.
    """
    height, width = img.shape[:2]
    for left, top, right, bottom in _own_descendant_rects(hwnd):
        rel_left   = max(0, left - abs_left)
        rel_top    = max(0, top - abs_top)
        rel_right  = min(width, right - abs_left)
        rel_bottom = min(height, bottom - abs_top)
        if rel_right > rel_left and rel_bottom > rel_top:
            img[rel_top:rel_bottom, rel_left:rel_right] = 0


def grab_window(hwnd: int, region: dict | None = None) -> np.ndarray:
    """Capture hwnd's own content, wherever it sits in the window stack.

    ScreenCapture.grab reads the screen, which only ever shows whatever is
    drawn on top — alt-tab to something else and it starts describing that
    instead of the game. PrintWindow renders the window itself into an
    off-screen bitmap, unaffected by whatever else is in front of it. Our own
    windows attached to hwnd get blanked back out afterwards — see
    _blank_own_windows.

    region, if given, is in the same absolute screen coordinates every
    other region in this codebase uses; it is converted to window-relative
    pixels here using hwnd's own current position.

    The GDI calls below transiently fail under load — the same reason
    ScreenCapture.grab retries its own BitBlt — so a failure here gets a
    couple of fresh attempts before it is allowed to propagate.
    """
    last_error: Exception | None = None
    for _ in range(_RETRIES):
        try:
            img, left, top = _print_window(hwnd)
            break
        except Exception as exc:
            last_error = exc
            time.sleep(_RETRY_DELAY)
    else:
        raise last_error

    if region is None:
        crop, abs_left, abs_top = img, left, top
    else:
        rel_left = region["left"] - left
        rel_top  = region["top"] - top
        crop = np.ascontiguousarray(
            img[rel_top:rel_top + region["height"],
               rel_left:rel_left + region["width"]])
        abs_left, abs_top = region["left"], region["top"]

    _blank_own_windows(crop, abs_left, abs_top, hwnd)
    return crop


def _print_window(hwnd: int) -> tuple[np.ndarray, int, int]:
    """One PrintWindow attempt — the raw BGR image plus the window's origin."""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise RuntimeError("Окно игры свёрнуто")

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    mem_dc = src_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(src_dc, width, height)
    mem_dc.SelectObject(bitmap)
    try:
        ok = ctypes.windll.user32.PrintWindow(
            hwnd, mem_dc.GetSafeHdc(), _PW_RENDERFULLCONTENT)
        if not ok:
            raise RuntimeError("Не удалось отрисовать окно игры")

        info = bitmap.GetInfo()
        bits = bitmap.GetBitmapBits(True)
        img = np.frombuffer(bits, dtype=np.uint8).reshape(
            info["bmHeight"], info["bmWidth"], 4)[:, :, :3]
        img = np.ascontiguousarray(img)
    finally:
        mem_dc.DeleteDC()
        src_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        win32gui.DeleteObject(bitmap.GetHandle())

    return img, left, top
