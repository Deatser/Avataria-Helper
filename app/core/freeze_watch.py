# app/core/freeze_watch.py
"""Игра иногда просто застывает: окна «Пауза» нет, заставки перерыва нет,
кнопки на месте — а картинка не меняется, и мод жмёт в мёртвый экран.

Ловится это единственным честным способом: снимок всего окна игры раз в три
минуты и сравнение с предыдущим. Живая игра за три минуты меняется вся —
хотя бы на пару процентов пикселей; застывшая совпадает с собой почти
пиксель в пиксель.

Здесь только наблюдение: увидел — сказал. Что делать дальше (гасить моды,
перезапускать игру) решает Overlay, как и с меню паузы.
"""
from __future__ import annotations

import threading

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal

from app.core.capture import grab_window, release_window_capture
from app.core.template_match import game_region

# Три минуты — столько же, сколько моду даётся на «дойти до игры» после
# перезапуска. Реже незачем, чаще — начнут совпадать соседние кадры внутри
# одной длинной анимации (экран загрузки, результаты забега).
CHECK_INTERVAL = 180.0

# Сравниваем не полноразмерные кадры, а уменьшенные до этой ширины: разница
# между «играем» и «застыли» видна и на превью, а гонять два с половиной
# мегапикселя раз в три минуты незачем. INTER_AREA заодно усредняет шум
# захвата, из-за которого одинаковые кадры отличаются на единицу яркости.
SCALE_WIDTH = 320

# Насколько пиксель должен отличаться, чтобы считаться изменившимся. 0..255;
# ниже этого — дрожание кодека и сглаживания, а не движение на экране.
PIXEL_TOL = 10

# Доля совпавших пикселей, начиная с которой картинка считается той же.
# Строго: у застывшей игры совпадает практически всё, а живой экран за три
# минуты не удерживается в пределах даже пары процентов.
SAME_RATIO = 0.99


def downscale(frame: np.ndarray) -> np.ndarray:
    """Кадр в серое и в SCALE_WIDTH по ширине."""
    if frame.ndim == 3:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = frame.shape[:2]
    if w <= SCALE_WIDTH or w == 0 or h == 0:
        return frame
    height = max(1, round(h * SCALE_WIDTH / w))
    return cv2.resize(frame, (SCALE_WIDTH, height), interpolation=cv2.INTER_AREA)


def same_ratio(before: np.ndarray, after: np.ndarray,
               tol: int = PIXEL_TOL) -> float:
    """Доля пикселей, которые за это время не изменились (0..1).

    Кадры разного размера сравнивать нечего — окно игры поменяли, и это само
    по себе значит, что что-то происходит.
    """
    if before.shape != after.shape or before.size == 0:
        return 0.0
    diff = cv2.absdiff(before, after)
    return float(np.count_nonzero(diff <= tol)) / diff.size


class FreezeWatch(QThread):
    """Раз в CHECK_INTERVAL снимает окно игры и сравнивает с прошлым кадром.

    Молчит, пока картинка живёт. Совпали два подряд — сообщает один раз и
    забывает кадр: следующий разговор начнётся с новой пары снимков, а не с
    того же самого зависания.
    """

    frozen = Signal(float)   # доля совпавших пикселей
    error  = Signal(str)

    def __init__(self, get_hwnd, interval_s: float = CHECK_INTERVAL):
        super().__init__()
        self._get_hwnd   = get_hwnd
        self._interval   = interval_s
        self._stop_event = threading.Event()
        self._paused     = threading.Event()
        self._prev: np.ndarray | None = None

    def stop_watch(self):
        self._stop_event.set()

    def pause_checks(self):
        self._paused.set()

    def resume_checks(self):
        # Кадр до паузы сравнивать не с чем: пока мы стояли, игру закрывали,
        # перезапускали и жали ОК.
        self._prev = None
        self._paused.clear()

    def run(self):
        self._stop_event.clear()
        self._prev = None
        region = game_region()

        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(self._interval)
                if self._stop_event.is_set():
                    break
                if self._paused.is_set():
                    self._prev = None
                    continue
                hwnd = self._get_hwnd()
                if not hwnd:
                    self._prev = None   # игры на экране нет — сравнивать нечего
                    continue
                try:
                    frame = downscale(grab_window(hwnd, region))
                except Exception as exc:
                    self.error.emit(str(exc))
                    self._prev = None
                    continue

                prev, self._prev = self._prev, frame
                if prev is None:
                    continue
                ratio = same_ratio(prev, frame)
                if ratio >= SAME_RATIO:
                    self._prev = None
                    self.frozen.emit(ratio)
        finally:
            release_window_capture()
