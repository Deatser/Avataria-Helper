# app/core/daily_reward.py
"""Окно ежедневного подарка — забрать и убрать с дороги.

Игра время от времени встречает окном «Ежедневный подарок»: чаще всего сразу
после перезапуска, но и посреди работы тоже — на смене суток. Пока оно висит,
любой включённый мод жмёт кнопки в модальное окно и не понимает, почему ничего
не происходит.

Устроено в две половины, чтобы клики не начались раньше, чем Overlay успеет
погасить моды. DailyRewardWatch раз в минуту смотрит, не появилось ли окно, и
только сообщает; DailyRewardCollect — забирает. Кого гасить и кого возвращать,
решает Overlay.

Забирается в два клика по местам, размеченным руками 2026-08-16 кнопкой
«Отметить область» в настройках помощника. Ни искать эти кнопки, ни угадывать
их по шаблону не нужно: окно модальное и всегда встаёт на одно место.
"""
from __future__ import annotations

import threading
import time

import cv2
from PySide6.QtCore import QThread, Signal

from app.core.capture import grab_window, release_window_capture
from app.core.input_sender import click_at
from app.core.template_match import (best_match, load_template,
                                     game_region)

REWARD_TEMPLATE = "daily reward.png"

# Кнопка «Ежедневный подарок» — крупная надпись на плашке, к масштабу окна
# терпимая. Порог ниже, чем у обычных кнопок (0.93), но заметно выше, чем у
# меню паузы (0.70): промахнуться тут дороже, чем не заметить — не заметили,
# посмотрим через минуту, а ложно сработали и нажали в пустоту.
MATCH_THRESHOLD = 0.80

# Раз в минуту. Подарок никуда не денется, а чаще смотреть незачем: каждая
# проверка — это снимок всего монитора и корреляция по нему.
CHECK_INTERVAL = 60.0

# А первый раз — почти сразу. Чаще всего это окно встречает игру после
# перезапуска, и ждать целую минуту, прежде чем вернуть моды, значит терять
# минуту ровно там, где помощник и так только что потерял несколько.
FIRST_CHECK_S = 10.0

# Куда нажимать, в экранных координатах. Центры двух областей, размеченных
# руками: сначала сама плашка подарка, потом крестик окна, которое после неё
# открывается.
CLAIM_POINT = (1260, 741)
CLOSE_POINT = (1573, 506)

# Сколько давать игре на отрисовку между кликами и перед проверкой. Первое
# окно с наградой открывается не мгновенно, а закрывается с анимацией.
CLAIM_SETTLE_S = 1.5
CLOSE_SETTLE_S = 1.5


def _grab_gray(hwnd: int, region: dict):
    return cv2.cvtColor(grab_window(hwnd, region), cv2.COLOR_BGR2GRAY)


class DailyRewardWatch(QThread):
    """Раз в CHECK_INTERVAL ищет окно подарка. Молчит, пока его нет.

    Ничего не нажимает: увидел — сказал. На время разбирательства Overlay
    ставит вотч на паузу (pause_checks), чтобы не сообщать об одном и том же
    окне каждую минуту.
    """

    seen  = Signal(float)   # match score
    error = Signal(str)

    def __init__(self, get_hwnd):
        super().__init__()
        self._get_hwnd   = get_hwnd
        self._stop_event = threading.Event()
        self._paused     = threading.Event()

    def stop_watch(self):
        self._stop_event.set()

    def pause_checks(self):
        self._paused.set()

    def resume_checks(self):
        self._paused.clear()

    def run(self):
        self._stop_event.clear()
        template = load_template(REWARD_TEMPLATE)
        if template is None:
            self.error.emit(f"Не найден шаблон: {REWARD_TEMPLATE}")
            return
        region = game_region()

        try:
            wait_s = FIRST_CHECK_S
            while not self._stop_event.is_set():
                self._stop_event.wait(wait_s)
                wait_s = CHECK_INTERVAL
                if self._stop_event.is_set() or self._paused.is_set():
                    continue
                hwnd = self._get_hwnd()
                if not hwnd:
                    continue
                try:
                    score, _ = best_match(_grab_gray(hwnd, region), template)
                except Exception as exc:
                    self.error.emit(str(exc))
                    continue
                if score >= MATCH_THRESHOLD:
                    self.seen.emit(score)
        finally:
            release_window_capture()


class DailyRewardCollect(QThread):
    """Два клика и проверка: ушло окно или нет.

    Без повторов. Если после клика по подарку и по крестику окно всё ещё на
    экране — значит там не то, что мы думали, и второй заход по тем же местам
    ничего не изменит; такое лечится перезапуском, и это уже дело Overlay.
    """

    collected = Signal(float)   # окно ушло; score того, что осталось
    stuck     = Signal(float)   # окно на месте; score
    error     = Signal(str)

    def __init__(self, get_hwnd):
        super().__init__()
        self._get_hwnd = get_hwnd
        self._stop_event = threading.Event()

    def stop_flow(self):
        self._stop_event.set()

    def run(self):
        self._stop_event.clear()
        template = load_template(REWARD_TEMPLATE)
        if template is None:
            self.error.emit(f"Не найден шаблон: {REWARD_TEMPLATE}")
            return
        region = game_region()

        try:
            hwnd = self._get_hwnd()
            if not hwnd:
                self.error.emit("Окно игры пропало — подарок не забрать")
                return

            click_at(hwnd, *CLAIM_POINT)
            if self._stop_event.wait(CLAIM_SETTLE_S):
                return
            click_at(hwnd, *CLOSE_POINT)
            if self._stop_event.wait(CLOSE_SETTLE_S):
                return

            hwnd = self._get_hwnd()
            if not hwnd:
                self.error.emit("Окно игры пропало — не проверить, ушёл ли подарок")
                return
            try:
                score, _ = best_match(_grab_gray(hwnd, region), template)
            except Exception as exc:
                self.error.emit(str(exc))
                return
            if score >= MATCH_THRESHOLD:
                self.stuck.emit(score)
            else:
                self.collected.emit(score)
        finally:
            release_window_capture()
