# tests/ui/test_gardener_window.py
import time
from datetime import datetime, timedelta

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from app.core.config import ConfigManager
from app.module_registry import MODULES
from app.ui import theme
from modules.gardener import GardenerModule
from modules.gardener.trash import TRASH_KINDS, TrashFind
from modules.gardener.window import GardenerWindow
import modules.gardener.cleaning as cleaning
import modules.gardener.window as window_module


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _WM:
    def get_game_hwnd(self): return 0
    def is_game_alive(self): return False
    def attach_child(self, hwnd): pass
    def move_window(self, *args): pass


def _window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = ConfigManager()
    return GardenerWindow(config.data.gardener, config.save, _WM()), config


def _stub_garden(monkeypatch, window, kinds):
    """A garden with exactly this litter in it, a game to click, no screen."""
    found = [TrashFind(kind, 0.9, 10 + n * 5, 20, accepted=True)
             for n, kind in enumerate(kinds)]
    monkeypatch.setattr(window_module, "scan", lambda *a, **k: found)
    monkeypatch.setattr(cleaning, "click_at", lambda *a: True)
    monkeypatch.setattr(window._wm, "get_game_hwnd", lambda: 4242,
                        raising=False)
    # No screen to look at here, so nothing is ever seen to go: whatever the
    # bars end up showing came from the counting, not from the clicking.
    monkeypatch.setattr(window, "_recount", lambda key: 99)
    return found


def _wait_for(app, window, needle, seconds=4.0):
    """Log lines arrive a character at a time; give one time to spell itself."""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if needle in window._log.toPlainText():
            return True
    return False


def test_the_module_is_registered_below_the_placeholders():
    assert GardenerModule in MODULES
    assert GardenerModule.panel_slot == "bottom"
    assert GardenerModule.config_key == "gardener"


def test_it_wears_the_olive_accent():
    assert GardenerModule.color == theme.GD_OLIVE


def test_the_window_has_its_controls_and_a_log(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)

    labels = [b.text() for b in window.findChildren(QPushButton)]

    assert labels == ["▲", "☆", "×",                    # window chrome
                      "▶  Запустить бота по уборке",
                      "⚙  Настройки",
                      "◎  Определить мусор",
                      "⌫"]                              # clears the log                              # clears the log
    assert window._log is not None
    window.close()


def test_the_start_button_flips_and_says_what_it_does(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    _stub_garden(monkeypatch, window, [TRASH_KINDS[0]] * 3)

    window._toggle_cleaning()
    app.processEvents()                 # the run starts a turn later
    assert window._running is True
    assert window._start_btn.text() == "■  Выключить бота по уборке"

    window._toggle_cleaning()
    assert window._running is False
    assert window._start_btn.text() == "▶  Запустить бота по уборке"
    window.close()


def test_nothing_starts_without_the_game(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)   # _WM has no game window

    window._toggle_cleaning()
    app.processEvents()

    assert window._running is False
    assert _wait_for(app, window, "не найдено")
    window.close()


# ── The bars ────────────────────────────────────────────────────────────────

def test_a_run_puts_up_a_bar_for_the_total_and_for_each_kind_present(
        tmp_path, monkeypatch, app):
    """Kinds that are not in the garden get no bar — 0/0 is nothing to watch."""
    window, _config = _window(tmp_path, monkeypatch)
    _stub_garden(monkeypatch, window,
                 [TRASH_KINDS[0]] * 4 + [TRASH_KINDS[4]] * 2)

    window._toggle_cleaning()
    app.processEvents()

    board = window._board
    assert board.isHidden() is False    # the window itself is never shown here
    assert set(board._bars) == {"__total__", TRASH_KINDS[0].key,
                                TRASH_KINDS[4].key}
    assert board.bar("__total__")._total == 6
    assert board.bar(TRASH_KINDS[0].key)._total == 4
    window._toggle_cleaning()
    window.close()


def test_the_bars_fill_as_the_litter_goes(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    _stub_garden(monkeypatch, window, [TRASH_KINDS[0]] * 3)

    window._toggle_cleaning()
    app.processEvents()
    window._run.cleaned.emit(TRASH_KINDS[0].key, 2, 2)

    assert window._board.bar(TRASH_KINDS[0].key).done == 2
    assert window._board.bar("__total__").done == 2
    window._toggle_cleaning()
    window.close()


def test_the_end_of_a_run_is_timed_and_the_next_one_is_an_hour_off(
        tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    _stub_garden(monkeypatch, window, [TRASH_KINDS[0]])

    window._toggle_cleaning()
    app.processEvents()
    window._run.finished.emit(187)

    assert _wait_for(app, window, "Уборка завершена за 3 мин 7 сек")
    assert window._running is False
    # Nothing was seen to go, so nothing is claimed: the bar stays empty and
    # the log says what was left standing.
    assert window._board.bar("__total__").done == 0
    assert _wait_for(app, window, "Не удалось убрать")

    ahead = window._next_run_at - datetime.now()
    assert timedelta(minutes=59) < ahead <= timedelta(minutes=60)

    # And it will not go again until then
    window._toggle_cleaning()
    app.processEvents()
    assert window._running is False
    assert _wait_for(app, window, "Цикл ещё не доступен")
    window.close()


def test_the_settings_sheet_opens_inside_the_window(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    window.resize(380, 480)
    window.show()          # a child widget follows its parent on screen

    window._toggle_settings()

    sheet = window._settings
    assert sheet.parentWidget() is window
    assert sheet.isVisible()
    assert sheet.width() <= window.width()

    window._toggle_settings()
    assert not sheet.isVisible()
    window.close()


def test_the_star_writes_through_to_the_config(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)

    window._toggle_favorite()

    assert window._fav_btn.text() == "★"
    assert config.data.gardener.favorite is True
    assert ConfigManager().data.gardener.favorite is True
    window.close()


def test_it_behaves_like_every_other_module_window(tmp_path, monkeypatch, app):
    """Animations, wiring, dragging and resizing all come from the base."""
    from app.ui.crt_power_mixin import CrtPowerMixin
    from app.ui.drag_mixin import BackgroundDragMixin
    from app.ui.resize_mixin import ResizeMixin

    window, _config = _window(tmp_path, monkeypatch)

    assert isinstance(window, (CrtPowerMixin, BackgroundDragMixin, ResizeMixin))
    assert window.minimumWidth() > 0 and window.minimumHeight() > 0
    window.close()


def test_the_overlay_puts_its_button_under_the_snowboard_one(tmp_path,
                                                             monkeypatch, app):
    from app.ui.overlay import Overlay

    monkeypatch.chdir(tmp_path)
    overlay = Overlay(ConfigManager(), _WM())
    labels = [b.text() for b in overlay.findChildren(QPushButton)]

    assert labels.index("Включить мод Садовник") \
        > labels.index("Включить мод Сноуборд")
