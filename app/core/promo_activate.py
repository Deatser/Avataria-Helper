# app/core/promo_activate.py
"""Проигрывает игровой экран промокодов для кодов из базы (vkapi_parser).

Порядок шагов, по одному коду:

    promo1 — кнопка «?» в интерфейсе игры        (клик)
    promo2 — иконка промокодов                   (клик)
    promo3 — заголовок «Промокод»                (только проверка, что экран открылся)
    promo4 / promo4_1 — пустое поле ввода        (клик — фокус)
    ввод кода
    promo5 — «активировать»                      (клик)
    promo6_COLLECT «забрать» ИЛИ promo6_OK «OK»  (клик по тому, что выпало)

Шестой шаг — это ответ игры, и приходит ровно одна из двух кнопок:
«забрать» (код принят, награда выдаётся) или «OK» (код не принят — не
найден, уже использован, просрочен). Жмём ту, что появилась, и по ней же
понимаем исход: отдельных шаблонов экрана результата нет и не нужно.

После шестого шага экран остаётся открытым, поэтому следующий код
начинается сразу с promo4 — promo1..promo3 повторяются только если поле
пропало (экран закрылся сам).

promo4 и promo4_1 — два вида *пустого* поля (с плейсхолдером «Введите
промокод» и просто пустой рамкой), и на этом держится вся защита от
прошлого бага с двойным/грязным вводом: печатаем только когда совпал один
из них (значит, в поле пусто), и считаем ввод состоявшимся ровно тогда,
когда перестали совпадать оба. Ничего не дочищаем Backspace-ами: их перебор
за пределами пустого поля игра читает как «назад» (подтверждено вживую
2026-08-05).
"""
from __future__ import annotations
import threading
import time

import cv2
from PySide6.QtCore import QThread, Signal

from app.core import activated_promo_log
from app.core.capture import grab_window
from app.core.input_sender import click_at, type_text
from app.core.promo_watch import PromoEntry, fetch_available
from app.core.template_match import best_match, load_template, game_region

SEARCH_INTERVAL = 1.0
AUTO_INTERVAL   = 60.0
# Разносит строки лога по одной за раз — иначе пачка пропусков (ничего не
# кликалось) валится в лог одним мгновением.
ENTRY_GAP_S = 2.0
# Держится после каждого клика, до следующего опроса экрана. Без паузы
# следующий опрос успевает застать доклик-кадр и выстрелить вторым кликом
# почти в ту же точку — игра читает это как двойной клик и закрывает то,
# что только что открылось.
CLICK_SETTLE_S = 0.8
# После ввода текста: даём странице отрисовать введённое, прежде чем
# проверять, пропал ли плейсхолдер.
TYPE_SETTLE_S = 1.0

STEP_OPEN_1 = "promo1.png"
STEP_OPEN_2 = "promo2.png"
STEP_SCREEN = "promo3.png"   # подтверждает, что мы на экране ввода
# Пустое поле ввода приходит в двух видах — с плейсхолдером «Введите
# промокод» и просто пустой рамкой, — поэтому шаг ищет оба. Оба означают
# ровно одно: в поле ничего нет.
STEP_FIELD   = "promo4.png"
STEP_FIELD_2 = "promo4_1.png"
FIELD_TEMPLATES = [STEP_FIELD, STEP_FIELD_2]
STEP_SUBMIT = "promo5.png"   # «активировать»
# Шестым шагом приходит одно из двух окон, и нажать надо то, которое
# выпало: «забрать» — код принят и награда выдаётся, «OK» — код игра не
# приняла (не найден, уже использован, просрочен). Разные кнопки — это
# заодно и единственный честный ответ на вопрос, прошёл код или нет,
# поэтому отдельных шаблонов экрана результата больше нет.
STEP_CLAIM  = "promo6_COLLECT.png"
STEP_REJECT = "promo6_OK.png"

MATCH_THRESHOLD = 0.90
# Поле ищется чуть мягче: плейсхолдер бледный, и слишком строгий порог
# оборачивается «поле не нашлось» на живом экране.
FIELD_THRESHOLD = 0.85

FIELD_WAIT_S  = 15.0   # ждём поле после открытия экрана / после шестого шага
SUBMIT_WAIT_S = 15.0   # «активировать» — часть того же экрана, появляется сразу
RESULT_WAIT_S = 12.0   # ответ игры: «забрать» либо «OK»
TYPE_ATTEMPTS = 3      # повторный ввод разрешён только пока поле пусто


def is_pending(entry: PromoEntry) -> bool:
    """Стоит пробовать автоматически: не просрочен, не активирован и не
    отвергнут игрой. Отказ учитывается только здесь, в фоновом цикле:
    вручную «активировать» такой код по-прежнему можно, а вот заходить на
    него каждую минуту самому — незачем."""
    return (entry.status != "expired"
            and not activated_promo_log.is_activated(entry.code)
            and not activated_promo_log.is_rejected(entry.code))


def _grab_gray(hwnd: int, region: dict):
    return cv2.cvtColor(grab_window(hwnd, region), cv2.COLOR_BGR2GRAY)


def _locate(hwnd: int, filename: str, threshold: float):
    """(x, y) центра шаблона на экране, или None. None и когда самого
    файла шаблона нет — это разбирается уровнем выше."""
    template = load_template(filename)
    if template is None:
        return None
    region = game_region()
    gray = _grab_gray(hwnd, region)
    score, (x, y) = best_match(gray, template)
    if score < threshold:
        return None
    h, w = template.shape[:2]
    return x + w // 2, y + h // 2


def _locate_any(hwnd: int, filenames: list[str], threshold: float):
    """Первый совпавший из нескольких шаблонов: (имя, (x, y)) или None.
    Порядок в списке — приоритет, если в кадре совпало сразу несколько."""
    for name in filenames:
        point = _locate(hwnd, name, threshold)
        if point is not None:
            return name, point
    return None


def _wait_for(hwnd: int, filename: str, stop_event: threading.Event,
              click: bool, on_error, timeout: float | None = None,
              threshold: float = MATCH_THRESHOLD) -> bool:
    """Опрашивает экран раз в секунду, пока шаблон не появится.

    timeout=None — ждать бесконечно: так открываются promo1..promo3, где
    «ещё не появилось» неотличимо от «не тот экран», и ложный таймаут
    отменил бы активацию, стоящую в очереди за медленной загрузкой.
    Шаги внутри самого экрана ввода ограничены по времени: там «не
    появилось» — это уже ответ (код не приняли).

    False, если сработал stop_event, вышел таймаут или файла шаблона нет
    (на последнем вызывается on_error — это проблема ассетов, а не экрана).
    """
    if load_template(filename) is None:
        on_error(f"Не найден шаблон: {filename}")
        return False

    deadline = None if timeout is None else time.monotonic() + timeout
    while not stop_event.is_set():
        point = _locate(hwnd, filename, threshold)
        if point is not None:
            if click:
                click_at(hwnd, *point)
                stop_event.wait(CLICK_SETTLE_S)
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return False
        stop_event.wait(SEARCH_INTERVAL)
    return False


def _wait_for_any(hwnd: int, filenames: list[str], stop_event: threading.Event,
                  on_error, timeout: float, click: bool = True,
                  threshold: float = MATCH_THRESHOLD) -> str | None:
    """Ждёт первый из нескольких шаблонов и возвращает его имя (кликнув по
    нему, если click). None — таймаут, стоп или ни одного файла шаблона на
    диске: пропажа одного из вариантов шагу не мешает, а вот отсутствие
    всех — это уже проблема ассетов.
    """
    present = [name for name in filenames if load_template(name) is not None]
    if not present:
        on_error(f"Не найдены шаблоны: {', '.join(filenames)}")
        return None

    deadline = time.monotonic() + timeout
    while not stop_event.is_set():
        found = _locate_any(hwnd, present, threshold)
        if found is not None:
            name, point = found
            if click:
                click_at(hwnd, *point)
                stop_event.wait(CLICK_SETTLE_S)
            return name
        if time.monotonic() >= deadline:
            return None
        stop_event.wait(SEARCH_INTERVAL)
    return None


class PromoScreen:
    """Один сеанс работы с игровым экраном промокодов.

    Держит знание о том, открыт экран или нет: первый код открывает его
    через promo1..promo3, каждый следующий начинает сразу с promo4, а если
    поле не вернулось после «забрать» — экран открывается заново.
    """

    def __init__(self, hwnd: int, stop_event: threading.Event, on_error,
                 on_step=None):
        self._hwnd = hwnd
        self._stop = stop_event
        self._on_error = on_error
        self._on_step = on_step or (lambda _label: None)

    def _step(self, label: str):
        self._on_step(label)

    def _field(self):
        """(имя шаблона, точка) пустого поля — любого из двух его видов."""
        return _locate_any(self._hwnd, FIELD_TEMPLATES, FIELD_THRESHOLD)

    def ensure_screen(self) -> bool:
        """Экран ввода открыт и поле готово принять код."""
        if self._field() is not None:
            return True

        self._step("Ищу кнопку открытия меню промокодов (promo1)...")
        if not _wait_for(self._hwnd, STEP_OPEN_1, self._stop, True, self._on_error):
            return False

        self._step("Открываю экран ввода кода (promo2)...")
        if not _wait_for(self._hwnd, STEP_OPEN_2, self._stop, True, self._on_error):
            return False

        self._step("Проверяю, что попали на экран ввода (promo3)...")
        if not _wait_for(self._hwnd, STEP_SCREEN, self._stop, False, self._on_error):
            return False

        self._step("Жду поле ввода (promo4 / promo4_1)...")
        return _wait_for_any(self._hwnd, FIELD_TEMPLATES, self._stop,
                             self._on_error, timeout=FIELD_WAIT_S,
                             click=False, threshold=FIELD_THRESHOLD) is not None

    def _type_code(self, code: str) -> bool:
        """Кликает по полю и печатает код — ровно один раз на попытку.

        Повтор разрешён только пока поле опознаётся как пустое (promo4 или
        promo4_1): значит, предыдущий ввод не дошёл вообще и печатать не на
        что наслаивать. Как только оба варианта пустого поля перестали
        совпадать — текст в поле, и второй раз мы не печатаем ни при каких
        условиях, даже если дальше что-то пойдёт не так.
        """
        for _ in range(TYPE_ATTEMPTS):
            found = self._field()
            if found is None:
                # Поле больше не читается как пустое, а код мы ещё не
                # печатали: в нём что-то чужое (или экран уехал). Печатать
                # поверх нельзя — ровно так и получались лишние символы.
                return False

            _name, point = found
            click_at(self._hwnd, *point)
            self._stop.wait(CLICK_SETTLE_S)
            if self._stop.is_set():
                return False

            type_text(self._hwnd, code)
            self._stop.wait(TYPE_SETTLE_S)
            if self._stop.is_set():
                return False

            if self._field() is None:
                return True   # поле больше не пустое — текст на месте
        return False

    def submit(self, entry: PromoEntry, on_submitted, on_confirmed,
               on_failed) -> bool:
        """Один код целиком. False — код не прошёл, поток остановлен или
        не хватает шаблона (об этом уже сообщено через on_error)."""
        if not self.ensure_screen():
            return False
        if self._stop.is_set():
            return False

        self._step(f"Ввожу код {entry.code} (promo4)...")
        if not self._type_code(entry.code):
            self._on_error(f"Не удалось ввести код {entry.code} в поле")
            return False

        self._step("Нажимаю «активировать» (promo5)...")
        if not _wait_for(self._hwnd, STEP_SUBMIT, self._stop, True,
                         self._on_error, timeout=SUBMIT_WAIT_S):
            return False

        self._step("Жду ответ игры: «забрать» либо «OK»...")
        answer = _wait_for_any(self._hwnd, [STEP_CLAIM, STEP_REJECT],
                               self._stop, self._on_error,
                               timeout=RESULT_WAIT_S)
        if answer is None:
            return False

        # Журнал пишется в обоих случаях. «OK» — это отказ самой игры (код
        # не найден / уже использован / просрочен), и повторные попытки раз
        # в минуту ничего не изменят, только будут гонять экран по кругу.
        accepted = answer == STEP_CLAIM

        # Пишем обе развязки, но разными записями. Успех делает код
        # использованным (в окне он станет жёлтым). Отказ — нет: код не
        # активирован, и показывать его как активированный неправильно.
        # Запись всё равно нужна, чтобы автодетект не заходил на этот код
        # по кругу каждую минуту — см. is_rejected.
        activated_promo_log.record(entry.code, entry.title, ok=accepted)

        if accepted:
            self._step("Награда забрана.")
            on_submitted(entry)
            on_confirmed(entry)
        else:
            self._step("Игра не приняла код — закрыл окно кнопкой «OK».")
            on_failed(entry)
        return True


class PromoActivateFlow(QThread):
    """Ручной запуск — «Нажмите чтобы активировать все доступные
    промокоды». Получает все выведенные записи в том же порядке, в каком
    они показаны; на каждую приходит своё «Активируем...» и ровно один
    итог (submitted / expired / already_activated / unknown_error). Срок и
    журнал активированных проверяются здесь же, по записи, а не фильтруются
    заранее."""

    starting          = Signal(object)
    submitted         = Signal(object)
    expired           = Signal(object)
    already_activated = Signal(object)
    unknown_error     = Signal(object)
    confirmed         = Signal(object)
    failed            = Signal(object)
    error             = Signal(str)

    def __init__(self, game_hwnd: int, entries: list[PromoEntry]):
        super().__init__()
        self._hwnd = game_hwnd
        self._entries = entries
        self._stop_event = threading.Event()

    def stop_flow(self):
        self._stop_event.set()

    def run(self):
        self._stop_event.clear()
        screen = PromoScreen(self._hwnd, self._stop_event, self.error.emit)

        for i, entry in enumerate(self._entries):
            if self._stop_event.is_set():
                break
            if i > 0:
                self._stop_event.wait(ENTRY_GAP_S)
                if self._stop_event.is_set():
                    break

            self.starting.emit(entry)

            if entry.status == "expired":
                self.expired.emit(entry)
                continue
            if activated_promo_log.is_activated(entry.code):
                self.already_activated.emit(entry)
                continue

            try:
                completed = screen.submit(entry, self.submitted.emit,
                                          self.confirmed.emit, self.failed.emit)
            except Exception as exc:
                self.error.emit(str(exc))
                completed = False

            if not completed and not self._stop_event.is_set():
                self.unknown_error.emit(entry)


class PromoAutoLoop(QThread):
    """Движок кнопки «Запустить автодетект промокодов»: раз в AUTO_INTERVAL
    перечитывает базу, отбрасывает всё, что отсекает is_pending(), и
    прогоняет тот же игровой сценарий для оставшегося."""

    starting      = Signal(object)
    submitted     = Signal(object)
    confirmed     = Signal(object)
    failed        = Signal(object)
    unknown_error = Signal(object)
    error         = Signal(str)   # база недоступна — одна строка на простой, не на цикл
    recovered     = Signal()      # чтение снова прошло
    # Сигнала «пропущено» нет: просроченные и уже активированные тут
    # пропускаются молча — этот шум уместен только в разовом выводе кнопки.

    def __init__(self, get_hwnd):
        super().__init__()
        self._get_hwnd = get_hwnd
        self._stop_event = threading.Event()
        # True с первой неудачной загрузки и до первой удачной — именно это
        # держит мёртвую сеть в пределах одной строки лога вместо строки
        # каждую минуту. Сам цикл из-за этого не останавливается.
        self._unavailable = False

    def stop_loop(self):
        self._stop_event.set()

    def run(self):
        self._stop_event.clear()
        while not self._stop_event.is_set():
            self._cycle()
            self._stop_event.wait(AUTO_INTERVAL)

    def _cycle(self):
        hwnd = self._get_hwnd()
        if not hwnd:
            return
        try:
            entries = fetch_available()
        except Exception as exc:
            if not self._unavailable:
                self._unavailable = True
                self.error.emit(str(exc))
            return

        if self._unavailable:
            self._unavailable = False
            self.recovered.emit()

        screen = PromoScreen(hwnd, self._stop_event, self.error.emit)
        for entry in entries:
            if self._stop_event.is_set():
                return
            if not is_pending(entry):
                continue

            self.starting.emit(entry)
            try:
                completed = screen.submit(entry, self.submitted.emit,
                                          self.confirmed.emit, self.failed.emit)
            except Exception as exc:
                self.error.emit(str(exc))
                completed = False

            if not completed and not self._stop_event.is_set():
                self.unknown_error.emit(entry)
