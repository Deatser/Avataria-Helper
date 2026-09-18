# app/core/game_watch.py
"""Следит за размером окна игры и пересчитывает геометрию, когда он менялся.

Опрос, а не подписка на WM_SIZE: окно игры чужое, ставить в него хук значит
лезть в чужой процесс, а два вызова Win32 четыре раза в секунду не стоят
ничего. Дорогая часть — снять кадр и промерить по нему поля — делается
только тогда, когда клиентская область действительно стала другой, — пока
пользователь тянет угол игры и ещё секунду после, пока сама игра
догоняет свою рамку (см. SETTLE_TICKS).

Здесь же записывается эталон: размер игры, при котором калибровалось всё
остальное в этом репозитории. Записывается сам и только с развёрнутой игры —
на ней всё и снималось. Записать его с окна в четверть экрана значило бы
объявить эталоном то, подо что ни один шаблон не резался, и промахиваться
теперь уже везде.
"""
from __future__ import annotations

import win32api
import win32con
import win32gui
from PySide6.QtCore import QObject, QTimer, Signal

from app.core.capture import grab_window_raw
from app.core.game_geometry import (Frame, client_frame, geometry,
                                    picture_frame, set_primary)

# Четыре раза в секунду: тянущееся окно успевает догнать руку, а холостой
# такт — это GetClientRect и ClientToScreen, и больше ничего.
CHECK_MS = 250

# Сколько тактов после последнего движения рамки продолжать перемерять
# картинку. Рамку окна Windows меняет сразу, а игра внутри перерисовывается
# сама и не в тот же миг: кадр, снятый ровно в момент движения, — это ещё
# старая картинка в новой рамке, и посчитанный по нему масштаб уводит и
# клики модов, и окна помощника. Секунда, за которую всё укладывается.
SETTLE_TICKS = 4

# Насколько окно должно закрывать рабочую область монитора, чтобы считаться
# развёрнутым. Не «ровно»: развёрнутое окно на пиксель-другой выходит за
# рабочую область собственной невидимой рамкой.
_MAXIMISED_SHARE = 0.94


class GameWatch(QObject):
    """Раз в CHECK_MS: не поменялась ли игра — и если да, кому сказать."""

    changed  = Signal()        # кадр игры стал другим
    recorded = Signal(object)  # записан эталон (Frame)
    hint     = Signal(str)     # что сказать пользователю в лог помощника

    def __init__(self, window_manager, config, parent=None):
        super().__init__(parent)
        self._wm     = window_manager
        self._config = config
        self._told   = False
        self._known_hwnd = 0
        self._last_client: Frame | None = None
        self._settle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(CHECK_MS)
        self._timer.timeout.connect(self.check)

    # ── Жизненный цикл ───────────────────────────────────────────────────────

    def start(self):
        """Запускается всегда, даже если игры ещё нет.

        Игру находят при старте помощника, но она может подняться и позже —
        а до того окно ей объявлено не было, и раньше слежение в такой
        сессии не начиналось уже никогда: помощник молча оставался без
        масштаба до самого перезапуска.
        """
        self.check()
        self._timer.start()

    def stop(self):
        self._timer.stop()

    # ── Эталон ───────────────────────────────────────────────────────────────

    def restore_reference(self):
        """Поднять записанный эталон из config."""
        section = getattr(self._config.data, "game", None)
        if section is None or not section.reference_width:
            return
        geometry(self._wm.get_game_hwnd()).set_reference(
            Frame(section.reference_left, section.reference_top,
                  section.reference_width, section.reference_height))

    def record_reference(self, frame: Frame | None = None) -> Frame | None:
        """Объявить нынешний размер игры эталонным.

        Отсюда же её берёт кнопка в настройках: пересняли шаблоны на другом
        мониторе — пересняли и эталон.
        """
        hwnd = self._wm.get_game_hwnd()
        if not hwnd:
            return None
        frame = frame or self._measure(hwnd)
        if frame is None:
            return None

        section = self._config.data.game
        section.reference_left   = frame.left
        section.reference_top    = frame.top
        section.reference_width  = frame.width
        section.reference_height = frame.height
        self._config.save()
        geometry(hwnd).set_reference(frame)
        self._told = False
        self.recorded.emit(frame)
        return frame

    # ── Такт ─────────────────────────────────────────────────────────────────

    def check(self):
        hwnd = self._wm.get_game_hwnd()
        if not hwnd or not self._wm.is_game_alive():
            self._last_client = None
            if geometry(hwnd).set_current(None):
                self.changed.emit()
            return

        if hwnd != self._known_hwnd:
            # Игра появилась или её перезапустили: у нового окна свой hwnd,
            # своя (пустая) геометрия — и эталон ей надо поднять заново.
            self._known_hwnd = hwnd
            self._last_client = None
            set_primary(hwnd)
            self.restore_reference()

        client = client_frame(hwnd)
        if client is None:
            return          # свёрнутая игра: мерить нечего, ждём разворота
        if client != self._last_client:
            # Рамку только что двигали — перемерять будем и после того, как
            # её отпустят: игра догоняет размер своей рамки не мгновенно.
            self._settle = SETTLE_TICKS
        elif self._settle > 0:
            self._settle -= 1
        else:
            return          # дешёвая проверка: ничего не двигалось

        frame = self._measure(hwnd)
        if frame is None:
            # Клиентскую область не запоминаем: иначе следующий такт решит,
            # что всё улеглось, и размер игры так и останется непромеренным.
            return
        self._last_client = client

        geom = geometry(hwnd)
        if geom.reference is None:
            if _looks_maximised(hwnd):
                # Первый запуск: игра развёрнута, значит она сейчас ровно в
                # том виде, в каком снимались все шаблоны и мерились все
                # координаты.
                self.record_reference(frame)
            elif not self._told:
                # Молчать тут нельзя: без эталона помощник просто ничего не
                # подстраивает, и снаружи это выглядит как «не работает».
                self._told = True
                self.hint.emit(
                    "Эталонный размер игры ещё не записан — окна и детекты "
                    "пока не подстраиваются под размер игры. Разверните игру "
                    "на весь экран один раз, и он запишется сам.")
        if geom.set_current(frame, client):
            self.changed.emit()

    def _measure(self, hwnd: int) -> Frame | None:
        try:
            image, origin = grab_window_raw(hwnd)
        except Exception:
            # Свёрнутая игра, кадр не отдался — клиентская область всё равно
            # известна, а поля по краям подождут следующего такта.
            return client_frame(hwnd)
        return picture_frame(hwnd, image, origin)


def _looks_maximised(hwnd: int) -> bool:
    """Развёрнута ли игра — по флагу Windows или просто по тому, что она
    занимает почти весь монитор (бывает и «безрамочное на весь экран»)."""
    try:
        if win32gui.GetWindowPlacement(hwnd)[1] == win32con.SW_SHOWMAXIMIZED:
            return True
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        # win32api, не win32gui: GetMonitorInfo и MonitorFromWindow живут
        # только там, и обращение к win32gui молча роняло весь запасной
        # путь в except — «безрамочное на весь экран» не опознавалось
        # никогда.
        monitor = win32api.GetMonitorInfo(
            win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST))
        ml, mt, mr, mb = monitor["Monitor"]
    except Exception:
        return False
    if mr <= ml or mb <= mt:
        return False
    share = (((right - left) / (mr - ml)) * ((bottom - top) / (mb - mt)))
    return share >= _MAXIMISED_SHARE
