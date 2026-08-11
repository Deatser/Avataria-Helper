# app/core/promo_watch.py
"""Qt-обёртки над vkapi_parser — источник промокодов для окна «Промокоды».

Сам разбор живёт в корневом vkapi_parser.py. Наполняет базу отдельный
сборщик (репозиторий avataria-promo-collector, крутится на GitHub Actions
по расписанию) — здесь только потоки: одноразовая загрузка последних кодов
для кнопки и проба связи перед включением автодетекта. Ни Telegram, ни
токенов — база открыта на чтение.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from vkapi_parser import (LATEST_COUNT, PromoEntry, PromoUnavailable,
                          fetch_available, fetch_entries, fetch_latest,
                          fetch_raw)

__all__ = ["PromoEntry", "PromoUnavailable", "PromoFetchLatest", "PromoCheck",
           "LATEST_COUNT", "fetch_available", "fetch_entries", "fetch_latest"]


class PromoFetchLatest(QThread):
    """Одноразово: последние LATEST_COUNT промокодов из базы, новые первыми
    — для кнопки «Вывести данные по последним промокодам», независимо от
    того, включён автодетект или нет."""

    fetched = Signal(list)   # [PromoEntry, ...]
    error   = Signal(str)

    def __init__(self, count: int = LATEST_COUNT):
        super().__init__()
        self._count = count

    def run(self):
        try:
            entries = fetch_latest(self._count)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.fetched.emit(entries)


class PromoCheck(QThread):
    """Проба связи перед включением автодетекта: кнопка переходит во
    включённое состояние только если база реально ответила за `timeout`."""

    ok    = Signal()
    error = Signal(str)

    def __init__(self, timeout: int = 8):
        super().__init__()
        self._timeout = timeout

    def run(self):
        try:
            fetch_raw(self._timeout)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.ok.emit()
