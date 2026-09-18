# app/core/launcher.py
"""Поднять Аватарию, если её нет на экране.

Способов запустить игру несколько; здесь реализован один — через приложение
Tortuga Game Club. Отсюда и разделение: `open_game` знает только сценарий
«найти лаунчер → нажать Играть → при необходимости войти через VK ID →
дождаться окна игры», а какой лаунчер и какие у него кнопки — константы
сверху. Второй способ, когда до него дойдёт, ляжет рядом такой же функцией.

Работает синхронно и до появления Qt: вызывается из main.py, когда окно
Аватарии не нашлось, и логи пишет в терминал — ни лаунчер, ни страница VK ID
не наши окна, и модовых логов рядом с ними просто нет.
"""
from __future__ import annotations

import ctypes
import time

import cv2
import numpy as np
import win32con
import win32gui

from app.core.capture import grab_window, window_frame_origin
from app.core.input_sender import click_at
from app.core.template_match import best_match, load_template, game_region
from app.core.term_log import tlog

LAUNCHER_TITLE = "Tortuga Game Club"
GAME_TITLE     = "Аватария"

# «Места» — признак того, что игра догрузилась и в неё можно жать.
PLACES_TEMPLATE = "button_places.png"

LAUNCH_TEMPLATE = "launch.png"   # кнопка ИГРАТЬ внутри лаунчера
VKID_TEMPLATE   = "vkid.png"     # «Войти с VK ID» — там же, вместо ИГРАТЬ
PERMIT_TEMPLATE = "Permit.png"   # «Разрешить» на странице VK ID в браузере

# Кнопки лаунчера и браузера рисуются системным сглаживанием, которое от
# масштаба экрана плывёт заметнее игровой графики, — поэтому planka ниже
# игровых 0.93, но выше 0.70, на котором в кнопку начинает превращаться
# любой прямоугольник похожего цвета.
MATCH_THRESHOLD = 0.85

ATTEMPT_WAIT_S = 30.0   # сколько ждём игру после одного нажатия ИГРАТЬ
TOTAL_WAIT_S   = 600.0  # …и сколько всего, прежде чем сдаться совсем
LOADED_WAIT_S  = 300.0  # сколько ждём, что игра догрузится до «Мест»
PERMIT_WAIT_S  = 60.0   # сколько ждём страницу VK ID с кнопкой «Разрешить»
GONE_WAIT_S    = 20.0   # сколько ждём, что страница закроется после клика
POLL_S         = 1.0

# Лаунчер после закрытия страницы входа ещё пересобирает своё окно, и
# нажатие в эту секунду уходит в никуда — кнопка нарисована, а обработчика
# за ней ещё нет (2026-08-12).
SETTLE_S = 2.0

MAXIMIZE_WAIT_S = 5.0   # сколько ждём, что окно действительно развернулось

# Сколько ждать после смены фокуса, прежде чем считать её состоявшейся —
# и столько же после клика, прежде чем отдавать фокус обратно.
FOCUS_SETTLE_S  = 0.4
MIN_WINDOW_SIDE = 200   # окна меньше этого — служебные, страницы входа в них нет

_user32   = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

# После разворачивания окно ещё некоторое время меняет размер: сначала его
# двигает Windows, потом под новый размер перекладывается сама страница игры.
# Всё, что считает координаты от клиентской области — а это позиции окна
# помощника и всех окон из автозагрузки, — обязано дождаться конца этой
# возни, иначе разложится по маленькому экрану и слипнется в углу.
SIZE_STABLE_S = 1.0     # столько размер должен не меняться, чтобы считаться
SIZE_WAIT_S   = 15.0    # …и столько всего его ждём
SIZE_POLL_S   = 0.2


def find_window(title_contains: str) -> int | None:
    """Первое видимое окно, в заголовке которого есть эта строка."""
    found: list[int] = []

    def callback(hwnd: int, _):
        if win32gui.IsWindowVisible(hwnd) and \
                title_contains in win32gui.GetWindowText(hwnd):
            found.append(hwnd)

    win32gui.EnumWindows(callback, None)
    return found[0] if found else None


def _gray(image: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def _find_in_window(hwnd: int, template) -> tuple[float, int, int] | None:
    """Совпадение внутри окна hwnd, в экранных координатах его центра.

    Берётся снимок самого окна, а не экрана: лаунчер к этому моменту вполне
    может быть закрыт браузером, а PrintWindow рисует его в любом случае.
    """
    try:
        gray = _gray(grab_window(hwnd))
    except Exception:
        return None
    score, (x, y) = best_match(gray, template)
    ox, oy = window_frame_origin(hwnd)
    h, w = template.shape[:2]
    return score, ox + x + w // 2, oy + y + h // 2


def _visible_windows() -> list[int]:
    """Видимые окна с заголовком и разумным размером — кандидаты на поиск.

    Свёрнутые пропускаются: PrintWindow с них снимает мусор, да и клика они
    всё равно не примут.
    """
    found: list[int] = []

    def callback(hwnd: int, _):
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return
        if not win32gui.GetWindowText(hwnd):
            return
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except win32gui.error:
            return
        if right - left >= MIN_WINDOW_SIDE and bottom - top >= MIN_WINDOW_SIDE:
            found.append(hwnd)

    win32gui.EnumWindows(callback, None)
    return found


def _find_in_any_window(template) -> tuple[float, int, int, int] | None:
    """Лучшее совпадение среди всех видимых окон: (score, hwnd, x, y).

    Страница VK ID открывается в браузере пользователя, и какой он и как
    называет свои окна — не наше дело. Искать по снимку экрана было бы
    проще, но тогда найденное нельзя нажать: клик уйдёт в то окно, что
    лежит сверху, а нужное может быть и под ним.
    """
    best = None
    for hwnd in _visible_windows():
        hit = _find_in_window(hwnd, template)
        if hit is None:
            continue
        if best is None or hit[0] > best[0]:
            best = (hit[0], hwnd, hit[1], hit[2])
    return best


def bring_to_front(hwnd: int) -> bool:
    """Вывести окно на передний план, обойдя защиту от кражи фокуса.

    SetForegroundWindow сам по себе Windows отклоняет: право на фокус есть
    только у процесса, который в фокусе сейчас. Стандартный обход — на
    мгновение приклеить свой поток ввода к потоку активного окна, тогда
    система считает нас тем же самым приложением.
    """
    if not hwnd:
        return False
    try:
        current = _user32.GetForegroundWindow()
        their_thread = _user32.GetWindowThreadProcessId(current, None)
        our_thread   = _kernel32.GetCurrentThreadId()
        _user32.AttachThreadInput(our_thread, their_thread, True)
        _user32.SetForegroundWindow(hwnd)
        _user32.BringWindowToTop(hwnd)
        _user32.AttachThreadInput(our_thread, their_thread, False)
    except Exception:
        return False
    time.sleep(FOCUS_SETTLE_S)
    return _user32.GetForegroundWindow() == hwnd


def click_in_window(hwnd: int, x: int, y: int) -> bool:
    """Клик в окно с кратким выводом его на передний план.

    Моды жмут игру фоново, посланными сообщениями, и игре этого хватает.
    Лаунчеру и браузеру — нет: их Chromium не превращает посланный клик в
    событие страницы, пока окно не в фокусе по-настоящему (проверено
    2026-08-12: без фокуса кнопка ИГРАТЬ не нажимается вовсе). Поэтому здесь
    окно на секунду поднимается, принимает клик и уступает фокус обратно —
    происходит это только при запуске игры, не во время работы модов.
    """
    if not hwnd:
        return False
    previous = _user32.GetForegroundWindow()
    bring_to_front(hwnd)
    ok = click_at(hwnd, x, y)
    time.sleep(FOCUS_SETTLE_S)
    if previous and previous != hwnd:
        bring_to_front(previous)
    return ok


def open_game(log=tlog) -> bool:
    """True — окно Аватарии на экране (нашлось само или мы его подняли).

    Одно нажатие ИГРАТЬ ничего не гарантирует: лаунчер может вместо игры
    попросить войти заново, а может просто не отреагировать. Поэтому цикл —
    жмём, ждём, и пока игры нет, жмём снова; требование войти проверяется в
    том же ожидании каждую секунду, а не один раз в конце.

    Ничего не делает молча: каждый шаг — строка в терминал, потому что
    смотреть на него, кроме терминала, в этот момент негде.
    """
    game = find_window(GAME_TITLE)
    if game:
        # Игра уже на экране — но не факт, что развёрнута. Окна помощника
        # раскладываются по её клиентской области, поэтому привести её в
        # порядок надо и здесь, а не только после собственного запуска.
        _prepare_game(game, log)
        return True

    launcher = find_window(LAUNCHER_TITLE)
    if not launcher:
        log(f"{LAUNCHER_TITLE} not found — запустить игру нечем")
        return False
    log(f"{LAUNCHER_TITLE} found")

    launch = load_template(LAUNCH_TEMPLATE)
    vkid   = load_template(VKID_TEMPLATE)
    permit = load_template(PERMIT_TEMPLATE)
    for name, template in ((LAUNCH_TEMPLATE, launch), (VKID_TEMPLATE, vkid),
                           (PERMIT_TEMPLATE, permit)):
        if template is None:
            log(f"Не найден шаблон: {name}")
            return False

    give_up = time.monotonic() + TOTAL_WAIT_S
    message = "Жмём кнопку играть"

    while time.monotonic() < give_up:
        if not _press(launcher, launch, message, log):
            return False

        outcome = _await_game_or_vkid(launcher, vkid,
                                      min(ATTEMPT_WAIT_S, give_up - time.monotonic()))
        if outcome == "game":
            # Окно только что появилось и ещё раскладывается — F11 в эту
            # секунду уходит в никуда, как и нажатие в лаунчер.
            time.sleep(SETTLE_S)
            started = find_window(GAME_TITLE)
            if started:
                _prepare_game(started, log)
                # Только на этом пути: окно игры сразу после запуска ещё
                # пересоздаётся, и прицепляться к нему рано — см. await_loaded.
                await_loaded(log)
                # И вперёд: клики по лаунчеру возвращали фокус туда, откуда
                # его взяли (терминал), а окна помощника — дочерние к окну
                # игры и уходят за спину вместе с ней.
                bring_to_front(find_window(GAME_TITLE))
            # Про найденное окно говорит main.py, когда действительно за него
            # ухватится — своя строка здесь означала бы то же самое секундой
            # раньше и печаталась дважды подряд.
            return True

        if outcome == "vkid":
            log("Обнаружено требование войти в аккаунт, переходим в VK ID")
            hit = _find_in_window(launcher, vkid)
            if hit is not None:
                click_in_window(launcher, hit[1], hit[2])
            if not _login_via_vkid(permit, log):
                return False
            message = "Повторно жмём кнопку играть"
        else:
            message = "Игра не поднялась — жмём кнопку играть ещё раз"

        # Лаунчер только что перерисовался — дать ему прийти в себя, иначе
        # нажатие уйдёт в кнопку, за которой ещё нет обработчика.
        time.sleep(SETTLE_S)

    log(f"{GAME_TITLE} не появилась за {int(TOTAL_WAIT_S)} с")
    return False


def is_maximized(hwnd: int) -> bool:
    """Развёрнуто ли окно. GetWindowPlacement, а не размеры на экране:
    развёрнутое окно не занимает монитор целиком (под ним панель задач), а
    окно, растянутое руками во весь экран, — занимает, и по одним координатам
    эти два состояния не различить."""
    try:
        return win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED
    except win32gui.error:
        return False


def ensure_maximized(hwnd: int, log=tlog) -> bool:
    """Развернуть окно игры — ровно как кнопкой «развернуть» в его заголовке.

    Не F11: тот уводит окно в собственный полный экран браузерного движка,
    без рамки, без кнопки закрытия и поверх панели задач. Нужно обычное
    развёрнутое окно, поэтому спрашивается системное состояние (IsZoomed) и
    ставится системное же (SW_MAXIMIZE).
    """
    if is_maximized(hwnd):
        return True

    log("Разворачиваем окно игры")
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    except win32gui.error as exc:
        log(f"Развернуть окно игры не удалось: {exc}")
        return False

    deadline = time.monotonic() + MAXIMIZE_WAIT_S
    while time.monotonic() < deadline:
        if is_maximized(hwnd):
            return True
        time.sleep(0.2)
    log("Окно игры не развернулось")
    return False


def _prepare_game(hwnd: int, log) -> None:
    """Привести окно игры в вид, по которому можно раскладывать наши окна:
    развернуть и дождаться, пока размер устоится. Дальше — main.py."""
    ensure_maximized(hwnd, log)
    size = await_stable_size(hwnd, log)
    if size:
        log(f"Окно игры готово — {size[0]}×{size[1]}")


def await_loaded(log=tlog) -> bool:
    """Дождаться, пока игра догрузится — до кнопки «Места» на экране.

    Ждём этого не ради красоты: окна помощника прицепляются к окну игры как
    дочерние, а только что запущенная игра своё окно ещё меняет. Прицепиться
    к промежуточному — значит уйти вместе с ним, и помощник останется без
    единого видимого окна (2026-08-12).

    Только на пути «мы сами её запустили»: у игры, которая и так была
    открыта, «Места» могут быть не на экране — она может стоять внутри
    мини-игры, и ждать их там нечего.
    """
    template = load_template(PLACES_TEMPLATE)
    if template is None:
        log(f"Не найден шаблон: {PLACES_TEMPLATE}")
        return False

    deadline = time.monotonic() + LOADED_WAIT_S
    while time.monotonic() < deadline:
        hwnd = find_window(GAME_TITLE)
        if hwnd:
            try:
                score, _ = best_match(_gray(grab_window(hwnd)), template)
            except Exception:
                score = 0.0
            if score >= MATCH_THRESHOLD:
                log("Игра загрузилась")
                return True
        time.sleep(POLL_S)

    log(f"Игра не догрузилась за {int(LOADED_WAIT_S)} с — идём дальше как есть")
    return False


def await_stable_size(hwnd: int, log=tlog) -> tuple[int, int] | None:
    """Дождаться, пока клиентская область окна перестанет меняться.

    Возвращает её размер — или None, если окно за отведённое время так и не
    успокоилось (тогда звать всё равно можно, просто без гарантии).
    """
    deadline = time.monotonic() + SIZE_WAIT_S
    last: tuple[int, int] | None = None
    steady_since = time.monotonic()

    while time.monotonic() < deadline:
        try:
            _, _, right, bottom = win32gui.GetClientRect(hwnd)
        except win32gui.error:
            return None
        size = (right, bottom)
        if size != last:
            last, steady_since = size, time.monotonic()
        elif time.monotonic() - steady_since >= SIZE_STABLE_S:
            return size
        time.sleep(SIZE_POLL_S)

    log("Окно игры всё ещё меняет размер — открываем окна как есть")
    return last


def _press(hwnd: int, template, message: str | None, log) -> bool:
    """Найти кнопку в окне и нажать. Ждёт её появления — лаунчер после
    закрытия страницы входа перерисовывается не мгновенно."""
    deadline = time.monotonic() + PERMIT_WAIT_S
    while time.monotonic() < deadline:
        hit = _find_in_window(hwnd, template)
        if hit is not None and hit[0] >= MATCH_THRESHOLD:
            if message:
                log(message)
            click_in_window(hwnd, hit[1], hit[2])
            return True
        time.sleep(POLL_S)
    log("Кнопка «Играть» в лаунчере не найдена")
    return False


def _await_game_or_vkid(launcher: int, vkid, seconds: float) -> str:
    """Что случилось раньше: "game", "vkid" или "timeout".

    Обе проверки идут в одном цикле раз в секунду. Раньше требование войти
    смотрелось один раз, после того как истечёт ожидание игры — отсюда и
    брались те десять секунд, что кнопка VK ID просто висела на экране.
    """
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if find_window(GAME_TITLE):
            return "game"
        hit = _find_in_window(launcher, vkid)
        if hit is not None and hit[0] >= MATCH_THRESHOLD:
            return "vkid"
        time.sleep(POLL_S)
    return "timeout"


def _login_via_vkid(permit, log) -> bool:
    """Дождаться страницы VK ID, нажать «Разрешить», дождаться, что она ушла."""
    deadline = time.monotonic() + PERMIT_WAIT_S
    while time.monotonic() < deadline:
        hit = _find_in_any_window(permit)
        if hit is not None and hit[0] >= MATCH_THRESHOLD:
            log("Обнаружена кнопка входа, входим в аккаунт")
            _score, hwnd, x, y = hit
            click_in_window(hwnd, x, y)
            break
        time.sleep(POLL_S)
    else:
        log("Страница VK ID не появилась — вход не выполнен")
        return False

    # Пропала кнопка — значит страница закрылась и лаунчер снова наш.
    gone = time.monotonic() + GONE_WAIT_S
    while time.monotonic() < gone:
        hit = _find_in_any_window(permit)
        if hit is None or hit[0] < MATCH_THRESHOLD:
            return True
        time.sleep(POLL_S)
    log("Страница VK ID не закрылась после входа")
    return False
