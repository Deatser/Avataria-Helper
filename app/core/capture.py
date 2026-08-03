# app/core/capture.py
from __future__ import annotations
import ctypes
import threading
import time
from threading import Lock
import mss
import mss.exception
import numpy as np
import cv2
import win32gui
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


def grab_window(hwnd: int, region: dict | None = None) -> np.ndarray:
    """Capture hwnd's own content, wherever it sits in the window stack.

    ScreenCapture.grab reads the screen, which only ever shows whatever is
    drawn on top — alt-tab to something else and it starts describing that
    instead of the game. PrintWindow renders the window itself into an
    off-screen bitmap, unaffected by whatever else is in front of it.

    region, if given, is in the same absolute screen coordinates every
    other region in this codebase uses; it is converted to window-relative
    pixels here using hwnd's own current position.
    """
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

    if region is None:
        return img
    rel_left = region["left"] - left
    rel_top  = region["top"] - top
    return np.ascontiguousarray(
        img[rel_top:rel_top + region["height"],
           rel_left:rel_left + region["width"]])
