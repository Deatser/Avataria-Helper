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

from app.core.game_geometry import geometry

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


# ── Which capture to use ────────────────────────────────────────────────
# PrintWindow is the default and always works. Windows Graphics Capture is
# the same picture roughly twice as fast (see wgc_capture.py), switched on
# from Ava Dancers' settings; it cannot capture a minimised window, so a
# session that will not start or stops producing frames falls straight back
# rather than taking the bot down with it.
_use_wgc = False
_wgc_lock = Lock()
_wgc_sessions: dict[int, object] = {}
_wgc_broken: set[int] = set()


def set_wgc_enabled(enabled: bool) -> bool:
    """Turn Windows Graphics Capture on or off. Returns what was actually
    set — asking for it without the library installed leaves it off."""
    global _use_wgc
    from app.core import wgc_capture

    enabled = bool(enabled) and wgc_capture.available()
    with _wgc_lock:
        if not enabled:
            _drop_wgc_sessions()
        _use_wgc = enabled
        _wgc_broken.clear()
    return enabled


def wgc_enabled() -> bool:
    return _use_wgc


def _live_frame_origin(hwnd: int) -> tuple[int, int]:
    """Экранные координаты левого верхнего пикселя снятого кадра, как есть.

    The two backends do not agree on this and the difference is not
    cosmetic. GetWindowRect counts in the invisible resize border — on the
    game it reads (-8, -8) — while a WGC frame starts at the window as it is
    actually drawn, (0, 0). Anything converting a position found *inside* a
    full-window capture back to screen coordinates has to ask here, or it
    lands eight pixels out: enough to shift every lane into its neighbour and
    move the hit line up by the same amount.
    """
    if _use_wgc:
        session = _wgc_sessions.get(hwnd)
        if session is not None:
            return session.origin
    left, top, _, _ = win32gui.GetWindowRect(hwnd)
    return left, top


def window_frame_origin(hwnd: int) -> tuple[int, int]:
    """Где лежит левый верхний пиксель того, что вернул grab_window, —
    в эталонных координатах.

    grab_window отдаёт кадр, приведённый к эталонному масштабу (см. его
    собственное описание), поэтому точка, найденная в этом кадре,
    складывается с эталонным началом координат, а не с живым. На игре
    ровно эталонного размера это одно и то же число.
    """
    return geometry(hwnd).to_reference(*_live_frame_origin(hwnd))


def wgc_frames_seen(hwnd: int) -> int | None:
    """Frames the window has produced, or None if WGC is not driving it."""
    session = _wgc_sessions.get(hwnd) if _use_wgc else None
    return session.frames_seen if session is not None else None


def _drop_wgc_sessions():
    for session in _wgc_sessions.values():
        try:
            session.stop()
        except Exception:
            pass
    _wgc_sessions.clear()


def _wgc_session(hwnd: int):
    """The running capture for this window, started on first use.

    None once a window has failed: retrying a session that would not start,
    every poll, would cost far more than the capture it is meant to replace.
    """
    from app.core import wgc_capture

    with _wgc_lock:
        if hwnd in _wgc_broken:
            return None
        session = _wgc_sessions.get(hwnd)
        if session is not None:
            return session
        try:
            session = wgc_capture.WindowSession(hwnd)
            if not session.wait_ready():
                session.stop()
                raise RuntimeError("окно не отдало ни одного кадра")
        except Exception:
            _wgc_broken.add(hwnd)
            return None
        _wgc_sessions[hwnd] = session
        return session


def grab_window(hwnd: int, region: dict | None = None,
                fresh: bool = True) -> np.ndarray:
    """Capture hwnd's own content, wherever it sits in the window stack.

    ScreenCapture.grab reads the screen, which only ever shows whatever is
    drawn on top — alt-tab to something else and it starts describing that
    instead of the game. PrintWindow renders the window itself into an
    off-screen bitmap, unaffected by whatever else is in front of it.

    region, if given, is in **эталонных** экранных координатах — тех самых,
    в которых записаны все области и точки в этом коде. Сюда она приезжает
    как есть, здесь переводится в нынешний размер игры, вырезается из кадра
    и возвращается обратно в эталонном масштабе. Поэтому шаблоны, снятые с
    развёрнутой игры, совпадают и с игрой, ужатой в четверть экрана, а
    найденная точка складывается с region["left"]/["top"] ровно так же, как
    складывалась всегда. На игре эталонного размера здесь не происходит
    ничего: ни перевода, ни лишнего resize.

    region=None по-прежнему значит «всё окно целиком» — тоже приведённое к
    эталону, а где лежит его левый верхний пиксель, отвечает
    window_frame_origin.

    `fresh=False` says this caller is not building a time series and will
    take whatever picture is already in hand — see WindowSession.grab, which
    is the only backend the flag means anything to. PrintWindow draws the
    window on the spot, so everything it returns is new either way.
    """
    image, origin = _grab_frame(hwnd, fresh)
    geom = geometry(hwnd)

    if region is not None:
        live = geom.region(region)
        # max(0, …) — область, заказанная за краем окна: срез с
        # отрицательного индекса молча вернул бы кусок с другой стороны
        # кадра. Обычный путь сюда не заходит: game_region() за пределы
        # игры не выходит.
        rel_left = max(0, live["left"] - origin[0])
        rel_top  = max(0, live["top"]  - origin[1])
        image = image[rel_top:rel_top + live["height"],
                      rel_left:rel_left + live["width"]]

    return _to_reference_scale(image, geom)


def _grab_frame(hwnd: int, fresh: bool) -> tuple[np.ndarray, tuple[int, int]]:
    """Кадр всего окна и экранные координаты его левого верхнего пикселя.

    The GDI calls below transiently fail under load — the same reason
    ScreenCapture.grab retries its own BitBlt — so a failure here gets a
    couple of fresh attempts before it is allowed to propagate.
    """
    if _use_wgc:
        session = _wgc_session(hwnd)
        if session is not None:
            try:
                return session.grab(None, fresh=fresh), session.origin
            except Exception:
                # Minimised, closed, moved — whatever it was, PrintWindow can
                # still answer. Tear the session down so the next call starts
                # a fresh one instead of inheriting a dead one; it is the
                # session that just failed that has to go, whether or not it
                # is still the one on file.
                with _wgc_lock:
                    if _wgc_sessions.get(hwnd) is session:
                        del _wgc_sessions[hwnd]
                try:
                    session.stop()
                except Exception:
                    pass

    last_error: Exception | None = None
    for _ in range(_RETRIES):
        try:
            img, left, top = _print_window(hwnd)
            return img, (left, top)
        except Exception as exc:
            last_error = exc
            _release_thread_cache()   # cached DC/bitmap may be the cause — drop it and retry clean
            time.sleep(_RETRY_DELAY)
    raise last_error


def grab_screen_region(region: dict) -> np.ndarray:
    """Кусок экрана по эталонному прямоугольнику, в эталонном масштабе.

    Тот же уговор, что у grab_window, только поверх ScreenCapture — для
    мест, которые читают экран целиком, а не окно игры.
    """
    geom = geometry()
    return _to_reference_scale(ScreenCapture.get().grab(geom.region(region)),
                               geom)


def grab_window_raw(hwnd: int, fresh: bool = True
                    ) -> tuple[np.ndarray, tuple[int, int]]:
    """Кадр окна без всякого перевода — и где он лежит на экране.

    Ровно один вызывающий: измерение самого кадра игры (game_geometry).
    Спрашивать нынешний масштаб у grab_window значило бы мерить масштаб
    масштабом.
    """
    return _grab_frame(hwnd, fresh)


def _to_reference_scale(image: np.ndarray, geom) -> np.ndarray:
    """Живой кусок кадра → тот же кусок в эталонном масштабе."""
    if geom.identity or image.size == 0:
        return np.ascontiguousarray(image)
    height, width = image.shape[:2]
    out_w = max(1, int(round(width  / geom.scale_x)))
    out_h = max(1, int(round(height / geom.scale_y)))
    if (out_w, out_h) == (width, height):
        return np.ascontiguousarray(image)
    # INTER_AREA — единственная интерполяция, которая при уменьшении
    # усредняет, а не выбрасывает пиксели; вверх она вырождается, поэтому
    # растяжение идёт линейным.
    interpolation = cv2.INTER_AREA if out_w < width else cv2.INTER_LINEAR
    return cv2.resize(image, (out_w, out_h), interpolation=interpolation)


# One PrintWindow target (window DC + memory DC + bitmap) per calling thread,
# reused across polls instead of recreated every call. AvaDancers' detector
# and its watcher threads call grab_window many times a second each; creating
# and tearing down a GDI bitmap on every single call was the single biggest
# cost in that loop. Keyed by thread, not shared: a DC handed to a second
# thread while the first is still using it is a straight race on the same
# GDI object, so each thread gets its own via threading.local instead of a
# lock around a shared one.
_tls = threading.local()


def _thread_cache(hwnd: int, width: int, height: int):
    cache = getattr(_tls, "cache", None)
    if cache is not None and cache[0] == hwnd and cache[-2] == width and cache[-1] == height:
        return cache
    if cache is not None:
        _release_thread_cache()

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    mem_dc = src_dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(src_dc, width, height)
    mem_dc.SelectObject(bitmap)

    cache = (hwnd, hwnd_dc, src_dc, mem_dc, bitmap, width, height)
    _tls.cache = cache
    return cache


def _release_thread_cache():
    """Free this thread's cached DC/bitmap, if any — call before the calling
    thread exits (each AvaDancers worker is a fresh QThread per round) so the
    GDI handles don't outlive it."""
    cache = getattr(_tls, "cache", None)
    if cache is None:
        return
    hwnd, hwnd_dc, src_dc, mem_dc, bitmap, _, _ = cache
    try:
        mem_dc.DeleteDC()
        src_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        win32gui.DeleteObject(bitmap.GetHandle())
    except Exception:
        pass
    _tls.cache = None


def release_window_capture():
    """Public entry point for a worker thread to call right before it exits."""
    _release_thread_cache()


def release_all_capture():
    """Drop everything, both backends — for shutting the app down.

    A WGC session owns a thread and a GPU surface, and unlike the GDI cache
    it is shared between threads rather than owned by one, so it cannot be
    cleaned up by whichever worker happens to exit last.
    """
    _release_thread_cache()
    with _wgc_lock:
        _drop_wgc_sessions()


def _print_window(hwnd: int) -> tuple[np.ndarray, int, int]:
    """One PrintWindow attempt — the raw BGR image plus the window's origin."""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise RuntimeError("Окно игры свёрнуто")

    _, _, _, mem_dc, bitmap, _, _ = _thread_cache(hwnd, width, height)
    ok = ctypes.windll.user32.PrintWindow(
        hwnd, mem_dc.GetSafeHdc(), _PW_RENDERFULLCONTENT)
    if not ok:
        raise RuntimeError("Не удалось отрисовать окно игры")

    info = bitmap.GetInfo()
    bits = bitmap.GetBitmapBits(True)
    img = np.frombuffer(bits, dtype=np.uint8).reshape(
        info["bmHeight"], info["bmWidth"], 4)[:, :, :3]
    img = np.ascontiguousarray(img)

    return img, left, top
