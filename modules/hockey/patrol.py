# modules/hockey/patrol.py
"""Вратарь, едущий с известной скоростью между двумя бортами.

Ничего не предсказывает и ни на что не опирается, кроме арифметики: скорость
уже измерена (см. red_watch.Lap), борта известны из разметки, значит положение
в любой момент — это просто «сколько проехал от старта», сложенное туда-обратно
внутри отрезка.

Отдельным модулем, а не парой строк в окне, по двум причинам. Во-первых, это
чистая функция времени: `at(t)` не помнит, когда её звали в прошлый раз, и
поэтому рамка едет ровно, как бы ни дёргался таймер — положение считается от
настоящих часов, а не накапливается по шагам. Во-вторых, из этого же класса
потом вырастет предсказание для броска: вопрос «где он будет через 1.5 с» — это
тот же `at(t)`, только с t в будущем.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Patrol:
    """Заезд туда-обратно с постоянной скоростью.

    `start_x` — где вратарь был в момент `start_at`; `rightward` — куда он в
    этот момент ехал. `low`/`high` — отрезок, по которому ездит **та самая
    точка**, что задана в `start_x`: борта размечены по краям вратаря, так что
    для головы отрезок ýже, чем расстояние между бортами (см.
    rink.head_bounds).
    """
    start_x: float
    start_at: float
    speed: float          # px/с, всегда положительная
    low: float
    high: float
    rightward: bool = True

    @property
    def width(self) -> float:
        return self.high - self.low

    @property
    def period(self) -> float:
        """Сколько занимает полный круг — туда и обратно."""
        return 2 * self.width / self.speed if self.speed > 0 else 0.0

    def at(self, t: float) -> float:
        """Где он в момент `t`.

        Путь разворачивается в прямую и складывается обратно внутрь отрезка:
        проехал больше его длины — значит уже развернулся у дальнего борта и
        едет назад. Так же, как отражается луч, и без единого условия на
        «а сейчас он в какую сторону».
        """
        if self.width <= 0:
            return self.low
        travelled = self.speed * max(0.0, t - self.start_at)
        # Сколько он к моменту старта уже прошёл по своему направлению.
        done = (self.start_x - self.low if self.rightward
                else self.high - self.start_x)
        folded = (done + travelled) % (2 * self.width)
        along = folded if folded <= self.width else 2 * self.width - folded
        return self.low + along if self.rightward else self.high - along
