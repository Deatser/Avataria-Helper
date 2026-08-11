# app/core/energy_farm.py
"""Закуп энергии в кафе — проигрывает игровой экран по шаблонам.

Порядок шагов:

    button_menu.png    — «Меню» кафе. Если оно на экране сразу, мы уже
                         на месте и всё начинается отсюда.
    button_places.png  — иконка «места», если меню не нашлось: мы не в кафе
    button_cafe.png    — карточка «Кафе» в списке мест
    button_menu.png    — теперь меню на месте, открываем его
    Button_pie.png / button_cheesecake.png / button_brownie.png — три кнопки покупки

После покупки игра закрывает меню, поэтому цикл чередуется: «Меню» →
кнопка товара → «Меню» → кнопка товара, пока не куплено всё, что
насчитал energy_bar.plan(). Нажатие считается состоявшимся не по факту
клика, а когда кнопка, по которой кликнули, пропала с экрана — это
единственный честный признак, что игра его приняла.
"""
from __future__ import annotations
import threading
import time

import cv2
from PySide6.QtCore import QThread, Signal

from app.core import energy_bar
from app.core.capture import grab_window
from app.core.input_sender import click_at
from app.core.template_match import best_match, load_template, primary_monitor_region

STEP_MENU   = "button_menu.png"
STEP_PLACES = "button_places.png"
STEP_CAFE   = "button_cafe.png"

MATCH_THRESHOLD = 0.85
SEARCH_INTERVAL = 0.4

# Насколько счёт кнопки должен просесть от её же лучшего чтения, чтобы клик
# считался принятым. Правило целиком взято из modules/ava_dancers/click_flow
# — оно там выведено живыми замерами: кнопка после нажатия остаётся
# нарисованной и падает всего на несколько процентов.
CONFIRM_DROP = 0.03
DROP_POLLS   = 2      # …и держится так два опроса подряд, не один кадр

# Кнопка, которая ещё на экране, дожимается — но по своим часам, а не раз в
# опрос: у меню это переключатель, и лишний клик закрыл бы только что
# открытое.
RECLICK_INTERVAL = 0.35

# Один короткий взгляд на экран в самом начале: есть «Меню» — мы в кафе,
# нет — идём через места. Дольше ждать нечего, кнопка либо
# нарисована, либо нас тут нет.
MENU_PROBE_S = 2.0

STEP_TIMEOUT = 30.0   # загрузка локации небыстрая, но и не бесконечная


def _grab_gray(hwnd: int, region: dict):
    return cv2.cvtColor(grab_window(hwnd, region), cv2.COLOR_BGR2GRAY)


def _locate(hwnd: int, filename: str, threshold: float = MATCH_THRESHOLD):
    """(x, y) центра шаблона на экране, или None."""
    template = load_template(filename)
    if template is None:
        return None
    gray = _grab_gray(hwnd, primary_monitor_region())
    score, (x, y) = best_match(gray, template)
    if score < threshold:
        return None
    h, w = template.shape[:2]
    return x + w // 2, y + h // 2


class CafeRun:
    """Один проход по кафе: дойти до меню и купить всё по списку."""

    def __init__(self, hwnd: int, stop_event: threading.Event, on_error):
        self._hwnd = hwnd
        self._stop = stop_event
        self._on_error = on_error

    # ── Один шаг ─────────────────────────────────────────────────────────────

    def click_step(self, filename: str, following: str | None = None) -> bool:
        """Жать кнопку, пока экран видимо не уехал дальше.

        Два признака, что клик приняли, — ровно те же, что у AvaDancers
        (modules/ava_dancers/click_flow.ClickFlow.run_step):

          * счёт самой кнопки просел от её лучшего чтения. Падать ниже
            порога он не обязан: кнопка остаётся нарисованной, просто уже
            не подсвечена;
          * появился *следующий* шаг, которого не было в начале. Это и есть
            случай «Мест»: после нажатия панель мест никуда не девается и
            держит свои 100%, так что единственное доказательство — что
            открылась карточка «Кафе».

        Шаблон следующего шага, уже стоявший на экране в первом опросе,
        доказательством не считается и на весь шаг игнорируется.
        """
        template = load_template(filename)
        if template is None:
            self._on_error(f"Не найден шаблон: {filename}")
            return False
        next_template = None if following is None else load_template(following)

        region     = primary_monitor_region()
        deadline   = time.monotonic() + STEP_TIMEOUT
        h, w       = template.shape[:2]
        peak       = 0.0
        clicked    = False
        last_click = 0.0
        first_poll = True
        next_useless = False   # следующий шаг был на экране ещё до нажатия
        low_polls    = 0

        while not self._stop.is_set():
            if time.monotonic() > deadline:
                self._on_error(f"Не дождался {filename} за "
                               f"{int(STEP_TIMEOUT)} с — лучшее совпадение "
                               f"{peak:.0%}")
                return False
            try:
                gray = _grab_gray(self._hwnd, region)

                if next_template is not None and not next_useless:
                    next_score, _ = best_match(gray, next_template)
                    if first_poll:
                        next_useless = next_score >= MATCH_THRESHOLD
                    elif clicked and next_score >= MATCH_THRESHOLD:
                        return True
                first_poll = False

                score, (x, y) = best_match(gray, template)

                # Проверяется до клика: кнопка может просесть и всё ещё
                # оставаться выше порога, и дожимать её тогда — значит жать
                # экран, который уже ушёл дальше.
                if clicked and score <= peak - CONFIRM_DROP:
                    low_polls += 1
                    if low_polls >= DROP_POLLS:
                        return True
                elif score >= MATCH_THRESHOLD:
                    low_polls = 0
                    peak = max(peak, score)
                    now  = time.monotonic()
                    if now - last_click >= RECLICK_INTERVAL:
                        last_click = now
                        click_at(self._hwnd, x + w // 2, y + h // 2)
                        clicked = True
                else:
                    low_polls = 0
            except Exception as exc:
                self._on_error(str(exc))
                return False

            self._stop.wait(SEARCH_INTERVAL)
        return False

    # ── Шаги ─────────────────────────────────────────────────────────────────

    def at_cafe(self) -> bool:
        """Одна короткая проба: видно ли «Меню» прямо сейчас."""
        deadline = time.monotonic() + MENU_PROBE_S
        while not self._stop.is_set():
            if _locate(self._hwnd, STEP_MENU) is not None:
                return True
            if time.monotonic() >= deadline:
                return False
            self._stop.wait(SEARCH_INTERVAL)
        return False

    def go_to_cafe(self) -> bool:
        """Мы не в кафе: места → кафе, и там уже будет «Меню».

        «Места» подтверждаются появлением «Кафе», а «Кафе» — появлением
        меню: обе панели остаются на экране после нажатия, так что своего
        падения счёта ждать бесполезно.
        """
        return (self.click_step(STEP_PLACES, following=STEP_CAFE)
                and self.click_step(STEP_CAFE, following=STEP_MENU))

    def open_menu(self, treat) -> bool:
        """Меню подтверждается кнопкой товара, который мы идём покупать."""
        return self.click_step(STEP_MENU, following=treat.template)

    def buy(self, treat, next_menu: bool) -> bool:
        """Один товар. Покупка закрывает меню, поэтому следующий шаг —
        снова кнопка «Меню»; для последней покупки её ждать незачем."""
        return self.click_step(treat.template,
                               following=STEP_MENU if next_menu else None)


class EnergyFarmFlow(QThread):
    """Кнопка «В кафе»: доводит до меню и покупает то, что насчитал plan().

    Логов ровно три вида, как и просили: одна строка в начале — «Открываем
    меню» либо «Мы не в кафе, идём в кафе», — и одна в конце, с итогом. Ни
    покупки, ни повторные открытия меню в лог не идут.
    """

    opening_menu  = Signal()        # «Меню» было на экране сразу
    going_to_cafe = Signal()        # …или нет, и мы идём через места
    finished_run  = Signal(object)  # energy_bar.Plan — что купили
    error         = Signal(str)

    def __init__(self, hwnd: int, plan):
        super().__init__()
        self._hwnd = hwnd
        self._plan = plan
        self._stop_event = threading.Event()

    def stop_flow(self):
        self._stop_event.set()

    def run(self):
        self._stop_event.clear()
        treats = self._plan.sequence()
        if not treats:
            self.error.emit("Количество энергии не выбрано")
            return

        run = CafeRun(self._hwnd, self._stop_event, self.error.emit)
        try:
            if not self._reach_menu(run, treats[0]):
                return
            for index, treat in enumerate(treats):
                if self._stop_event.is_set():
                    return
                # Первое меню уже открыто тем, что нас сюда привело; каждое
                # следующее открывается молча — покупка его закрывает.
                if index and not run.open_menu(treat):
                    return
                last = index == len(treats) - 1
                if not run.buy(treat, next_menu=not last):
                    return
        except Exception as exc:
            self.error.emit(str(exc))
            return

        if not self._stop_event.is_set():
            self.finished_run.emit(self._plan)

    def _reach_menu(self, run: CafeRun, first_treat) -> bool:
        """До открытого меню — из кафе или через список мест."""
        if run.at_cafe():
            self.opening_menu.emit()
            return run.open_menu(first_treat)

        if self._stop_event.is_set():
            return False
        self.going_to_cafe.emit()
        if not run.go_to_cafe():
            return False
        self.opening_menu.emit()
        return run.open_menu(first_treat)
