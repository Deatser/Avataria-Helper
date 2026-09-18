# app/core/game_geometry.py
"""Где сейчас лежит картинка игры и во сколько раз она отличается от эталона.

Всё в этом репозитории снято при одном-единственном размере игры —
развёрнутом на весь экран: вырезанные шаблоны, забитые руками прямоугольники
областей, точки кликов, положения и размеры окон помощника в config.json.
Стоит свернуть игру в окно поменьше, и каждое из этих чисел показывает не
туда: шаблон больше не совпадает, область уезжает за край, клик приходит
мимо, окна помощника остаются висеть там, где игры уже нет.

Здесь живёт единственный перевод между двумя системами координат:

  «эталон» — экранные координаты, какими они были при калибровке. Все числа
             в коде и в config.json записаны в них и остаются как есть;
  «сейчас» — экранные координаты той же точки при нынешнем размере игры.

Картинка игры **измеряется**, а не вычисляется. Клиентская область окна
отрезает заголовок точно, а поля по краям — если игра сохраняет свои
пропорции в окне другой формы — отрезаются по кадру: они одноцветные.
Поэтому один и тот же код одинаково верен и когда игра растягивается на всё
окно, и когда она вписывается в него с полосами: масштаб по X и по Y
считается независимо, и если игра держит пропорции, они просто совпадут.

Без записанного эталона перевод — тождественный: помощник ведёт себя ровно
так, как вёл до появления этого модуля.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
import win32gui

# ── Поиск полос по краям кадра ──────────────────────────────────────────────
# Полоса — это край кадра, на котором нет ничего: строка (или столбец)
# одного цвета. Режем, пока такие идут подряд, и не дальше трети кадра — за
# этой границей уже не поле, а ровно закрашенный кусок самой игры.
_TRIM_LIMIT = 0.35
# Разброс яркости, при котором строка ещё считается одноцветной. Не ноль:
# видеокодек и сглаживание оставляют на чёрном поле шум в пару единиц.
_TRIM_TOL   = 6
# Полосы тоньше этого не бывает, а вот ровная строка на краю самой игры —
# сплошь и рядом. Без этого порога у кадра каждый раз отгрызался случайный
# пиксель, и эталон с текущим кадром переставали сходиться.
_MIN_BAR    = 8


@dataclass(frozen=True)
class Frame:
    """Прямоугольник в экранных координатах."""

    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def centre(self) -> tuple[int, int]:
        return self.left + self.width // 2, self.top + self.height // 2

    @property
    def region(self) -> dict:
        """Тот же прямоугольник в форме, которую принимают все grab-вызовы."""
        return {"left": self.left, "top": self.top,
                "width": self.width, "height": self.height}

    @property
    def valid(self) -> bool:
        return self.width > 0 and self.height > 0

    @classmethod
    def from_region(cls, region: dict) -> Frame:
        return cls(int(region["left"]), int(region["top"]),
                   int(region["width"]), int(region["height"]))


class GameGeometry:
    """Эталонный кадр игры, нынешний — и перевод между ними.

    Потокобезопасен: измеряет и переписывает кадр GUI-поток, а спрашивают
    его детекторы модов, каждый из своего.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._reference: Frame | None = None
        self._current: Frame | None = None
        self._client: Frame | None = None

    # ── Эталон ───────────────────────────────────────────────────────────────

    @property
    def reference(self) -> Frame | None:
        with self._lock:
            return self._reference

    def set_reference(self, frame: Frame | None):
        with self._lock:
            self._reference = frame if (frame and frame.valid) else None

    @property
    def has_reference(self) -> bool:
        return self.reference is not None

    # ── Нынешний кадр ────────────────────────────────────────────────────────

    @property
    def current(self) -> Frame | None:
        with self._lock:
            return self._current

    def set_current(self, frame: Frame | None,
                    client: Frame | None = None) -> bool:
        """Запомнить измеренный кадр. True — он отличается от прошлого.

        Клиентская область запоминается тем же заходом: окна помощника
        живут в её координатах, а масштабируются по картинке, и разница
        между ними — то самое слагаемое, на которое окно отступает от угла.
        Спрашивать её отдельно и позже значит однажды вычесть свежий угол
        из картинки, померенной до того, как игру подвинули, — и получить
        отступ размером с этот сдвиг.
        """
        with self._lock:
            frame = frame if (frame and frame.valid) else None
            self._client = client if (client and client.valid) else frame
            if frame == self._current:
                return False
            self._current = frame
            return True

    @property
    def client(self) -> Frame | None:
        """Клиентская область игры на момент последнего замера."""
        with self._lock:
            return self._client

    @property
    def offset_in_client(self) -> tuple[int, int]:
        """Насколько картинка игры отступает от угла клиентской области.

        Ноль, когда игра занимает окно целиком; ширина поля, когда она
        держит свои пропорции в окне другой формы.
        """
        with self._lock:
            picture, client = self._current, self._client
        if picture is None or client is None:
            return 0, 0
        return picture.left - client.left, picture.top - client.top

    def forget(self):
        """Игра закрылась — про её кадр больше ничего не известно."""
        self.set_current(None)

    # ── Масштаб ──────────────────────────────────────────────────────────────

    @property
    def scale_x(self) -> float:
        with self._lock:
            if self._reference is None or self._current is None:
                return 1.0
            return self._current.width / self._reference.width

    @property
    def scale_y(self) -> float:
        with self._lock:
            if self._reference is None or self._current is None:
                return 1.0
            return self._current.height / self._reference.height

    @property
    def scale(self) -> float:
        """Один масштаб на обе оси — для всего, что нельзя растянуть по
        одной: шрифтов, окон помощника, радиусов кистей."""
        return min(self.scale_x, self.scale_y)

    @property
    def identity(self) -> bool:
        """Переводить нечего — эталон не записан или игра ровно на нём."""
        with self._lock:
            if self._reference is None or self._current is None:
                return True
            return self._reference == self._current

    # ── Перевод точек и прямоугольников ──────────────────────────────────────

    def point(self, x: float, y: float) -> tuple[int, int]:
        """Эталонная экранная точка → та же точка на экране сейчас."""
        with self._lock:
            ref, cur = self._reference, self._current
        if ref is None or cur is None:
            return int(round(x)), int(round(y))
        return (
            int(round(cur.left + (x - ref.left) * cur.width  / ref.width)),
            int(round(cur.top  + (y - ref.top)  * cur.height / ref.height)),
        )

    def to_reference(self, x: float, y: float) -> tuple[int, int]:
        """Обратный перевод: экранная точка сейчас → эталонная.

        Нужен всему, что рождается от живого экрана и попадает в код,
        считающий в эталоне, — перетаскиванию окон прежде всего.
        """
        with self._lock:
            ref, cur = self._reference, self._current
        if ref is None or cur is None or not cur.valid:
            return int(round(x)), int(round(y))
        return (
            int(round(ref.left + (x - cur.left) * ref.width  / cur.width)),
            int(round(ref.top  + (y - cur.top)  * ref.height / cur.height)),
        )

    def frame(self, ref_frame: Frame) -> Frame:
        """Эталонный прямоугольник → нынешний. Углы переводятся оба, а не
        угол плюс масштабированный размер: так прямоугольник не расползается
        на пиксель туда-сюда от округления."""
        left, top     = self.point(ref_frame.left, ref_frame.top)
        right, bottom = self.point(ref_frame.right, ref_frame.bottom)
        return Frame(left, top, max(1, right - left), max(1, bottom - top))

    def region(self, region: dict) -> dict:
        """То же для прямоугольника в форме grab-вызовов."""
        return self.frame(Frame.from_region(region)).region

    def length_x(self, value: float) -> int:
        return max(1, int(round(value * self.scale_x)))

    def length_y(self, value: float) -> int:
        return max(1, int(round(value * self.scale_y)))

    def length(self, value: float) -> int:
        """Длина, которой всё равно, вдоль какой она оси."""
        return max(1, int(round(value * self.scale)))


# Одна геометрия на окно игры, а не на приложение: Аватария и Тропикания —
# разные окна, разных размеров, с разными эталонами. У окна, для которого
# эталон не записан, геометрия тождественная, и мод ведёт себя как раньше.
_geometries: dict[int, GameGeometry] = {}
_registry_lock = threading.Lock()
_primary_hwnd = 0


def set_primary(hwnd: int | None):
    """Чьей геометрией отвечает geometry() без аргумента — окна Аватарии.

    Спрашивают её и те места, где под рукой нет hwnd: область «вся игра»,
    масштаб окон помощника.
    """
    global _primary_hwnd
    _primary_hwnd = int(hwnd or 0)


def primary_hwnd() -> int:
    return _primary_hwnd


def geometry(hwnd: int | None = None) -> GameGeometry:
    key = int(hwnd) if hwnd else _primary_hwnd
    with _registry_lock:
        geom = _geometries.get(key)
        if geom is None:
            geom = _geometries[key] = GameGeometry()
        return geom


# ── Измерение ───────────────────────────────────────────────────────────────

def client_frame(hwnd: int) -> Frame | None:
    """Клиентская область окна в экранных координатах.

    Не GetWindowRect: у окна игры есть заголовок и невидимая рамка, они не
    тянутся вместе с картинкой, и считать масштаб по ним значит ошибаться
    тем сильнее, чем меньше окно.
    """
    if not hwnd:
        return None
    try:
        if win32gui.IsIconic(hwnd):
            # Свёрнутая в панель задач игра: клиентская область у неё либо
            # нулевая, либо уехавшая в -32000, и пересчитать по ней значит
            # разослать всем окнам помощника мусорные координаты. Пусть
            # остаётся известным прошлый размер — разворачивают игру в него же.
            return None
        _l, _t, right, bottom = win32gui.GetClientRect(hwnd)
        left, top = win32gui.ClientToScreen(hwnd, (0, 0))
    except Exception:
        return None
    if right <= 0 or bottom <= 0:
        return None
    return Frame(left, top, right, bottom)


def picture_frame(hwnd: int, image: np.ndarray | None = None,
                  image_origin: tuple[int, int] | None = None) -> Frame | None:
    """Где на экране лежит собственно картинка игры.

    Клиентская область, с которой срезаны одноцветные поля по краям — то,
    что появляется, когда игра сохраняет свои пропорции в окне другой формы.
    Без кадра (image=None) — просто клиентская область: это верный ответ для
    окна, в котором игра занимает всё.
    """
    client = client_frame(hwnd)
    if client is None or image is None or image_origin is None:
        return client

    box = _trim_bars(image, image_origin, client)
    return box if box is not None else client


def _trim_bars(image: np.ndarray, origin: tuple[int, int],
               client: Frame) -> Frame | None:
    """Срезать одноцветные поля по краям клиентской области кадра."""
    if image is None or image.size == 0:
        return None
    gray = image if image.ndim == 2 else image.max(axis=2)

    # Клиентская область в координатах самого кадра.
    x0 = client.left - origin[0]
    y0 = client.top  - origin[1]
    x1, y1 = x0 + client.width, y0 + client.height
    height, width = gray.shape[:2]
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
        # Кадр не покрывает клиентскую область целиком — значит он не от
        # этой рамки: снят до того, как окно подвинули или перерисовали.
        # Раньше такой кадр просто обрезался по краям и промерялся как ни в
        # чём не бывало, а меряется по нему масштаб всей игры: одного
        # такого кадра хватало, чтобы объявить картинкой игры случайный
        # кусок кадра и сволочь все окна помощника в угол. Клиентская
        # область известна точно и без кадра — ею и обойдёмся.
        return None
    if x1 - x0 < 2 * _MIN_BAR or y1 - y0 < 2 * _MIN_BAR:
        return None

    view = gray[y0:y1, x0:x1].astype(np.int16)
    rows, cols = view.shape

    top    = _bar_run(view, rows, forward=True,  axis=0)
    bottom = _bar_run(view, rows, forward=False, axis=0)
    left   = _bar_run(view, cols, forward=True,  axis=1)
    right  = _bar_run(view, cols, forward=False, axis=1)

    if top + bottom >= rows or left + right >= cols:
        return None
    return Frame(client.left + left, client.top + top,
                 client.width - left - right, client.height - top - bottom)


def _bar_run(view: np.ndarray, length: int, forward: bool, axis: int) -> int:
    """Сколько одноцветных линий подряд лежит у этого края.

    Ноль, если их меньше _MIN_BAR: ровная строка у края самой игры — обычное
    дело, и принимать её за поле значит каждый раз отгрызать от кадра
    случайный кусок.
    """
    limit = int(length * _TRIM_LIMIT)
    run = 0
    for step in range(limit):
        index = step if forward else length - 1 - step
        line = view[index, :] if axis == 0 else view[:, index]
        if int(line.max()) - int(line.min()) > _TRIM_TOL:
            break
        run += 1
    return run if run >= _MIN_BAR else 0
