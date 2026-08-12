# app/core/term_log.py
"""Строка в терминал со временем по Москве.

Логи помощника живут в его собственных окнах и время печатают сами; но
запуск игры происходит до того, как появилось хоть одно окно, и видно его
только в терминале. Раз уж это единственный экран — пусть на нём будет
понятно, что когда произошло.

Время московское, а не системное: игра живёт по нему, и все остальные
отметки в статистике и дневном логе тоже.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))


def stamp() -> str:
    return datetime.now(MSK).strftime("[%H:%M:%S]")


def tlog(message: str):
    print(f"{stamp()} {message}", flush=True)
