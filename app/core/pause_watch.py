# app/core/pause_watch.py
"""Игра иногда зависает — поверх неё встаёт окно «Пауза», и любой мод,
который в этот момент работал, продолжает жать кнопки в пустоту.

Здесь две половины этого: PauseWatch раз в пять секунд смотрит, не появилось
ли это окно (работает всегда, независимо от того, какой мод включён), а
PauseRecovery разбирает последствия — жмёт ОК, пока меню не исчезнет, и ждёт
кнопку «Места», по которой видно, что игра перезапустилась. Кто из модов был
включён и кого возвращать к работе — дело Overlay, не этих потоков.
"""
from __future__ import annotations

import threading
import time

import cv2
from PySide6.QtCore import QThread, Signal

from app.core.capture import grab_window, release_window_capture
from app.core.input_sender import click_at
from app.core.launcher import PLACES_TEMPLATE
from app.core.template_match import best_match, load_template, primary_monitor_region

PAUSE_TEMPLATE  = "PAUSE_MENU.png"
PAUSE_OK_TEMPLATE = "PAUSE_MENU_OK.png"

# Заставка технического перерыва. Не целиком: «technical break.png» — снимок
# всего экрана (2534×1340), он совпал бы только при точно таком же размере
# окна, а искать его каждые пять секунд означает гонять корреляцию по трём
# мегапикселям. Вырезан кусок с надписью «С любовью, команда Аватарии» — он
# на этой заставке и больше нигде, и ищется он в сотни раз дешевле.
BREAK_TEMPLATE = "technical_break_text.png"

# Ниже, чем у обычных кнопок (0.93): окно паузы рисуется поверх того, что
# игра успела застыть на экране, а сама кнопка ОК — маленький кроп, который
# от масштаба окна плывёт заметнее крупных баннеров.
MATCH_THRESHOLD = 0.70

CHECK_INTERVAL = 5.0    # раз в пять секунд — зависание никуда не денется

# Техперерыв длится часами. Сообщать о нём каждые пять секунд бессмысленно,
# один раз за появление — мало: перезапуск игры перерыва не отменяет, и
# помощнику нужен повод попробовать снова. Раз в пять минут, пока висит.
BREAK_REPEAT_S = 300.0

OK_TRIES     = 10       # столько раз жмём ОК, прежде чем сдаться
OK_SETTLE_S  = 1.5      # сколько даём одному клику на то, чтобы закрыть меню
PLACES_WAIT_S = 300.0   # сколько ждём перезапуска игры — перезапуск бывает
                        # долгим, но пять минут это уже не «долго», а «не
                        # поднялась»
PLACES_POLL_S = 1.0


def _grab_gray(hwnd: int, region: dict):
    return cv2.cvtColor(grab_window(hwnd, region), cv2.COLOR_BGR2GRAY)


class PauseWatch(QThread):
    """Раз в CHECK_INTERVAL ищет окно «Пауза» и заставку техперерыва. Молчит,
    пока их нет.

    Ничего не нажимает: увидел — сказал. Дальше Overlay решает, кого гасить
    и когда запускать PauseRecovery, а сам вотч на это время ставится на
    паузу (pause_checks), чтобы не сообщать об одном зависании дважды.

    Про техперерыв говорится один раз за появление: он висит часами, и вотч
    молчит, пока заставка не пропадёт и не появится снова.
    """

    pause_seen = Signal(float)   # match score
    break_seen = Signal(float)   # match score заставки техперерыва
    error      = Signal(str)

    def __init__(self, get_hwnd):
        super().__init__()
        self._get_hwnd   = get_hwnd
        self._stop_event = threading.Event()
        self._paused     = threading.Event()
        self._break_told = 0.0   # monotonic-время последнего сообщения о перерыве

    def stop_watch(self):
        self._stop_event.set()

    def pause_checks(self):
        self._paused.set()

    def resume_checks(self):
        self._paused.clear()

    def run(self):
        self._stop_event.clear()
        pause = load_template(PAUSE_TEMPLATE)
        if pause is None:
            self.error.emit(f"Не найден шаблон: {PAUSE_TEMPLATE}")
            return
        # Заставку перерыва можно и не найти на диске — это не повод бросать
        # слежение за зависаниями, ради которого вотч и заводился.
        brk = load_template(BREAK_TEMPLATE)
        if brk is None:
            self.error.emit(f"Не найден шаблон: {BREAK_TEMPLATE}")
        region = primary_monitor_region()

        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(CHECK_INTERVAL)
                if self._stop_event.is_set() or self._paused.is_set():
                    continue
                hwnd = self._get_hwnd()
                if not hwnd:
                    continue
                try:
                    gray = _grab_gray(hwnd, region)
                    score, _ = best_match(gray, pause)
                    break_score = (best_match(gray, brk)[0]
                                   if brk is not None else 0.0)
                except Exception as exc:
                    self.error.emit(str(exc))
                    continue

                if break_score >= MATCH_THRESHOLD:
                    if time.monotonic() - self._break_told >= BREAK_REPEAT_S:
                        self._break_told = time.monotonic()
                        self.break_seen.emit(break_score)
                else:
                    self._break_told = 0.0   # ушла — о следующей скажем сразу

                if score >= MATCH_THRESHOLD:
                    self.pause_seen.emit(score)
        finally:
            release_window_capture()


class GameReadyWatch(QThread):
    """Ждёт, пока игра догрузится, — то есть пока на экране не появятся
    «Места».

    Окно игры появляется задолго до того, как в неё можно жать: сначала
    лаунчер, потом загрузка самой игры. Единственный надёжный признак «всё,
    загрузилось» — кнопка «Места»; мод, запущенный раньше неё, идёт искать
    кнопки на ещё пустом экране.
    """

    ready     = Signal(float)   # match score
    timed_out = Signal()
    error     = Signal(str)

    def __init__(self, get_hwnd, wait_s: float = PLACES_WAIT_S):
        super().__init__()
        self._get_hwnd   = get_hwnd
        self._wait_s     = wait_s
        self._stop_event = threading.Event()

    def stop_watch(self):
        self._stop_event.set()

    def run(self):
        self._stop_event.clear()
        template = load_template(PLACES_TEMPLATE)
        if template is None:
            self.error.emit(f"Не найден шаблон: {PLACES_TEMPLATE}")
            self.timed_out.emit()
            return
        region   = primary_monitor_region()
        deadline = time.monotonic() + self._wait_s

        try:
            while time.monotonic() < deadline and not self._stop_event.is_set():
                hwnd = self._get_hwnd()
                if hwnd:
                    try:
                        score, _ = best_match(_grab_gray(hwnd, region), template)
                    except Exception as exc:
                        self.error.emit(str(exc))
                    else:
                        if score >= MATCH_THRESHOLD:
                            self.ready.emit(score)
                            return
                self._stop_event.wait(PLACES_POLL_S)
        finally:
            release_window_capture()

        if not self._stop_event.is_set():
            self.timed_out.emit()


class PauseRecovery(QThread):
    """Жмёт ОК, пока окно паузы не уйдёт, потом ждёт «Места».

    Клик один за раз, а не очередью: меню закрывается с первого нажатия,
    когда игра вообще отвечает, а лишние клики по уже пропавшей кнопке
    уходят в игру под ней.
    """

    ok_clicked   = Signal(int)    # какая это по счёту попытка (с 1)
    menu_cleared = Signal()
    restarted    = Signal()
    failed       = Signal(str)
    error        = Signal(str)

    def __init__(self, get_hwnd):
        super().__init__()
        self._get_hwnd   = get_hwnd
        self._stop_event = threading.Event()

    def stop_flow(self):
        self._stop_event.set()

    def run(self):
        self._stop_event.clear()
        try:
            self._execute()
        finally:
            release_window_capture()

    def _execute(self):
        pause  = load_template(PAUSE_TEMPLATE)
        ok     = load_template(PAUSE_OK_TEMPLATE)
        places = load_template(PLACES_TEMPLATE)
        for name, template in ((PAUSE_TEMPLATE, pause),
                               (PAUSE_OK_TEMPLATE, ok),
                               (PLACES_TEMPLATE, places)):
            if template is None:
                self.failed.emit(f"Не найден шаблон: {name}")
                return

        if not self._press_ok(pause, ok):
            return
        self.menu_cleared.emit()
        self._await_places(places)

    def _press_ok(self, pause, ok) -> bool:
        region = primary_monitor_region()
        oh, ow = ok.shape[:2]

        for attempt in range(1, OK_TRIES + 1):
            if self._stop_event.is_set():
                return False
            hwnd = self._get_hwnd()
            if not hwnd:
                self.failed.emit("Игровое окно не найдено")
                return False
            try:
                gray = _grab_gray(hwnd, region)
            except Exception as exc:
                self.error.emit(str(exc))
                self._stop_event.wait(OK_SETTLE_S)
                continue

            pause_score, _ = best_match(gray, pause)
            if pause_score < MATCH_THRESHOLD:
                return True   # меню уже ушло — жать нечего

            ok_score, (x, y) = best_match(gray, ok)
            if ok_score >= MATCH_THRESHOLD:
                click_at(hwnd,
                         region.get("left", 0) + x + ow // 2,
                         region.get("top", 0)  + y + oh // 2)
                self.ok_clicked.emit(attempt)
            self._stop_event.wait(OK_SETTLE_S)

        self.failed.emit(f"Меню паузы не закрылось за {OK_TRIES} нажатий ОК")
        return False

    def _await_places(self, places):
        """Кнопка «Места» на экране — единственное доказательство, что игра
        не просто закрыла окно паузы, а действительно поднялась заново."""
        region   = primary_monitor_region()
        deadline = time.monotonic() + PLACES_WAIT_S

        while time.monotonic() < deadline and not self._stop_event.is_set():
            hwnd = self._get_hwnd()
            if hwnd:
                try:
                    score, _ = best_match(_grab_gray(hwnd, region), places)
                except Exception as exc:
                    self.error.emit(str(exc))
                else:
                    if score >= MATCH_THRESHOLD:
                        self.restarted.emit()
                        return
            self._stop_event.wait(PLACES_POLL_S)

        if not self._stop_event.is_set():
            self.failed.emit(
                f"Игра не перезапустилась за {int(PLACES_WAIT_S)} с — "
                f"«Места» так и не появились")
