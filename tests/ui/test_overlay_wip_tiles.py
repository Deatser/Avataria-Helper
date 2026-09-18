# tests/ui/test_overlay_wip_tiles.py
"""Тайлы модов, которых ещё нет: Snowboard, Hockey, Кулинар.

Они стоят на доске, чтобы место за ними было видно, но нажать их нельзя —
и ни один из входов в _toggle_module не должен их открыть.

Оверлей здесь не закрывается: closeEvent поднимает модальный диалог
подтверждения, и в headless-прогоне он просто повиснет. Остальные тесты
оверлея живут по тому же правилу.
"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.core.config import ConfigManager
from app.ui.overlay import Overlay, _WIP_MODULES
from app.ui.widgets.nt_button import NtButton, WIP_TEXT


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


def _tile(overlay: Overlay, text: str) -> NtButton:
    for button in overlay.findChildren(NtButton):
        if button.text() == text:
            return button
    raise AssertionError(f"на доске нет тайла {text!r}")


def test_the_unfinished_tiles_are_dimmed_and_say_why(tmp_path, monkeypatch,
                                                     app):
    overlay = _overlay(tmp_path, monkeypatch)

    for label in ("Snowboard", "Hockey", "Кулинар"):
        tile = _tile(overlay, label)
        assert tile._wip is True, label
        assert tile.toolTip() == WIP_TEXT, label


def test_a_finished_tile_stays_a_normal_button(tmp_path, monkeypatch, app):
    """Соседи по доске не подхватили погасший вид заодно."""
    overlay = _overlay(tmp_path, monkeypatch)

    tile = _tile(overlay, "Ava Dancers")

    assert tile._wip is False
    assert tile.toolTip() == ""


def test_clicking_an_unfinished_tile_opens_nothing(tmp_path, monkeypatch, app):
    overlay = _overlay(tmp_path, monkeypatch)
    tile = _tile(overlay, "Hockey")
    fired = []
    tile.clicked.connect(lambda: fired.append(True))

    QTest.mouseClick(tile, Qt.LeftButton)

    assert fired == []
    assert overlay._open_windows == {}


def test_a_starred_unfinished_module_stays_shut_at_startup(tmp_path,
                                                           monkeypatch, app):
    """Звезда могла остаться в config.json с тех пор, когда мод открывался, —
    автозагрузка не должна поднять окно, которого ещё нет."""
    monkeypatch.chdir(tmp_path)
    config = ConfigManager()
    config.data.hockey.favorite = True
    config.save()

    overlay = Overlay(ConfigManager(), _WM())
    overlay.restore_favorite_windows()

    assert "Хоккей" in _WIP_MODULES
    assert overlay._open_windows == {}
