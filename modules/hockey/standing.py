# modules/hockey/standing.py
"""Кто в ряду не двигается.

Не всякий вратарь ездит: в ряду попадаются такие, что стоят на месте весь
уровень. Их не надо ни ловить у борта, ни мерить период — надо один раз
увидеть, где они, и дальше знать это без единого кадра. Заодно они и есть та
помеха, из-за которой ловля у борта иногда не сходится: стоящий в полосе
никогда из неё не выедет, и разворота там не дождаться.

Способ ровно тот, что просили: смотреть секунду и проверить, поменяло ли
красное пятно координаты. Считается это не по одному пятну, а тальей по
позициям — иначе ряд, где один стоит, а второй мимо проезжает, не разобрать:
проезжающий даёт по кадру в каждой точке, стоящий — все кадры в одной.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Сколько смотрим, прежде чем отвечать.
WINDOW_S = 1.0

# Насколько может гулять центр пятна, чтобы это всё ещё было «на месте».
# Едущий вратарь идёт 220…340 px/с, то есть проходит эти восемь пикселей за
# пару кадров — спутать его со стоящим не выйдет.
TOL_PX = 8

# В какой доле кадров пятно должно быть на месте. Не во всех: детекция ловит
# не каждый кадр, и одного пропуска достаточно, чтобы «все» не выполнилось
# никогда.
MIN_SHARE = 0.7

# И сколько кадров вообще должно набраться, прежде чем доля что-то значит.
# Доля от трёх кадров — это не доля.
MIN_FRAMES = 8


@dataclass(frozen=True)
class Stander:
    """Вратарь, который никуда не едет."""
    x: float        # центр пятна по экрану
    seen: float     # в какой доле кадров он там был


class StandWatch:
    """Один ряд, за которым смотрят секунду.

    Кормится центрами красных пятен — по списку на кадр; что считать пятном,
    решает red_watch.red_blobs.
    """

    def __init__(self, window_s: float = WINDOW_S):
        self.window_s = float(window_s)
        self._frames: list[tuple[float, tuple[float, ...]]] = []
        self._first: float | None = None
        self._last: float = 0.0

    def feed(self, centres, at: float):
        if self._first is None:
            self._first = at
        self._last = at
        self._frames.append((at, tuple(float(x) for x in centres)))

    @property
    def frames(self) -> int:
        return len(self._frames)

    @property
    def watched(self) -> float:
        """Сколько секунд смотрим. От первого кадра, а не от нажатия: пока
        игра не отдала ни одного, смотреть было не на что."""
        if self._first is None:
            return 0.0
        return max(0.0, self._last - self._first)

    def ready(self) -> bool:
        return self.watched >= self.window_s and self.frames >= MIN_FRAMES

    def standers(self) -> list[Stander]:
        """Позиции, занятые почти во всех кадрах.

        Позиции разбиваются на корзины шириной TOL_PX и считается, в скольких
        кадрах корзина была занята. Стоящий даёт корзину, занятую всегда;
        проезжающий — по кадру в каждой из двух десятков корзин подряд.

        Соседние занятые корзины схлопываются: вратарь, стоящий на границе
        двух, попадает то в одну, то в другую и сам по себе ни одну не
        набирает.
        """
        if not self._frames:
            return []
        tally: dict[int, int] = {}
        for _at, centres in self._frames:
            for key in {int(round(x / TOL_PX)) for x in centres}:
                tally[key] = tally.get(key, 0) + 1

        needed = MIN_SHARE * self.frames
        busy = sorted(key for key, seen in tally.items() if seen >= needed)
        found, run = [], []
        for key in busy + [None]:
            if run and (key is None or key != run[-1] + 1):
                best = max(run, key=lambda b: tally[b])
                found.append(Stander(x=self._settle(best * float(TOL_PX)),
                                     seen=tally[best] / self.frames))
                run = []
            if key is not None:
                run.append(key)
        return found

    def _settle(self, about: float) -> float:
        """Корзина говорит, где он примерно; сами отметки — где точно."""
        near = [x for _at, centres in self._frames for x in centres
                if abs(x - about) <= TOL_PX]
        return float(np.median(near)) if near else about
