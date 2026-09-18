# tests/ui/test_overlay_game_watch.py
"""Слежение за размером игры должно быть заведено — и запущено.

Однажды оно уже оказалось затёрто: под тем же именем `_game_watch` в
оверлее лежал таймер точки статуса, и второе присваивание забирало имя
себе. Снаружи не менялось ничего — GameWatch оставался жив как ребёнок
QObject, — но размер игры не мерил больше никто, перевод координат
навсегда оставался тождественным, и клики модов уходили по числам
развёрнутой игры, в какой бы размер её ни ужали.

Оверлей здесь не закрывается: closeEvent поднимает модальный диалог
подтверждения, и в headless-прогоне он просто повиснет. Остальные тесты
оверлея живут по тому же правилу.
"""
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from app.core.config import ConfigManager
from app.core.game_watch import GameWatch
from app.ui.overlay import Overlay


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _WM:
    def get_game_hwnd(self): return 0
    def is_game_alive(self): return False
    def attach_child(self, hwnd): pass
    def move_window(self, *args): pass


def _overlay(tmp_path, monkeypatch) -> Overlay:
    monkeypatch.chdir(tmp_path)
    return Overlay(ConfigManager(), _WM())


def test_the_size_watch_survives_the_rest_of_the_constructor(tmp_path,
                                                             monkeypatch, app):
    overlay = _overlay(tmp_path, monkeypatch)

    assert isinstance(overlay._game_watch, GameWatch)


def test_start_game_watch_actually_starts_the_size_watch(tmp_path, monkeypatch,
                                                         app):
    """main.py зовёт это на каждом запуске; до сих пор оно запускало не то."""
    overlay = _overlay(tmp_path, monkeypatch)
    assert overlay._game_watch._timer.isActive() is False

    overlay.start_game_watch()

    assert overlay._game_watch._timer.isActive() is True


def test_the_status_dot_keeps_a_timer_of_its_own(tmp_path, monkeypatch, app):
    """Точке статуса нужен свой таймер, и отбирать имя у слежения нельзя."""
    overlay = _overlay(tmp_path, monkeypatch)

    assert isinstance(overlay._status_timer, QTimer)
    assert overlay._status_timer.isActive() is True
    assert overlay._status_timer is not overlay._game_watch


def test_remembering_the_reference_size_reaches_the_size_watch(tmp_path,
                                                               monkeypatch, app):
    """Кнопка «Запомнить как эталонный» звала record_reference у таймера —
    то есть падала с AttributeError вместо того, чтобы что-то записать."""
    overlay = _overlay(tmp_path, monkeypatch)

    overlay.record_game_reference()   # игры нет: строка в лог, а не падение
