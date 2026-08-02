# tests/ui/test_gardener_window.py
import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from app.core.config import ConfigManager
from app.module_registry import MODULES
from app.ui import theme
from modules.gardener import GardenerModule
from modules.gardener.window import GardenerWindow


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


def test_the_module_is_registered_below_the_placeholders():
    assert GardenerModule in MODULES
    assert GardenerModule.panel_slot == "bottom"
    assert GardenerModule.config_key == "gardener"


def test_it_wears_the_olive_accent():
    assert GardenerModule.color == theme.GD_OLIVE


def test_the_shell_has_the_usual_window_buttons(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)

    labels = [b.text() for b in window.findChildren(QPushButton)]

    assert labels == ["▲", "☆", "×"]   # collapse, favourite, close — no more
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
