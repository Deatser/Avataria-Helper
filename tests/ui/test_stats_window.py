# tests/ui/test_stats_window.py
import pytest
from PySide6.QtWidgets import QApplication

from app.core.config import ConfigManager
from app.core.stats import StatsManager
from app.ui.stats_window import StatsWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _WM:
    def get_game_hwnd(self): return 0
    def is_game_alive(self): return False
    def attach_child(self, hwnd): pass
    def move_window(self, *args): pass


def _window(tmp_path, monkeypatch, app, stats=None):
    monkeypatch.chdir(tmp_path)
    config = ConfigManager()
    window = StatsWindow(config.data.stats_window, config.save,
                         stats if stats is not None else StatsManager(), _WM())
    return window, config


def test_the_star_starts_hollow_and_fills_in(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch, app)

    assert window.favorite is False
    assert window._fav_btn.text() == "☆"

    window._toggle_favorite()

    assert window.favorite is True
    assert window._fav_btn.text() == "★"
    assert config.data.stats_window.favorite is True
    window.close()


def test_the_star_survives_a_restart(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch, app)
    window._toggle_favorite()
    window.close()

    assert ConfigManager().data.stats_window.favorite is True


def test_unstarring_writes_through_too(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch, app)
    window._toggle_favorite()
    window._toggle_favorite()

    assert window.favorite is False
    assert window._fav_btn.text() == "☆"
    assert ConfigManager().data.stats_window.favorite is False
    window.close()


def test_placeholders_show_for_the_fields_nobody_filled_in(tmp_path,
                                                           monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch, app)

    assert window._id_value.text() == "%player_id%"
    assert window._name_value.text() == "%player_name%"
    assert window._date_value.text() == "%registration_date%"
    assert window._games_tile.value == "0"   # a real zero, not a placeholder
    window.close()


def test_the_gardener_row_shows_a_real_zero_and_the_time_left(
        tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch, app)

    assert window._cleanup_tile.value == "0"          # real zero, not %...%
    assert window._next_tile.value == "Доступна!"       # nothing recorded yet
    window.close()


def test_the_gardener_countdown_counts_down(tmp_path, monkeypatch, app):
    from datetime import datetime

    monkeypatch.chdir(tmp_path)
    stats = StatsManager()
    stats.data.gardener.clean_next_time = "1:02:03"
    stats.data.gardener.clean_was_time = datetime.now().isoformat()
    window, _config = _window(tmp_path, monkeypatch, app, stats)

    assert window._next_tile.value in ("1:02:03", "1:02:02")   # tick skew
    window.close()


def test_the_gardener_countdown_catches_up_after_being_closed(
        tmp_path, monkeypatch, app):
    """The mod does not run while it's closed, so nothing decrements
    clean_next_time by hand — the countdown has to be worked out from how
    long ago clean_was_time was instead."""
    from datetime import datetime, timedelta

    monkeypatch.chdir(tmp_path)
    stats = StatsManager()
    stats.data.gardener.clean_next_time = "1:00:00"
    stats.data.gardener.clean_was_time = (
        datetime.now() - timedelta(minutes=45)).isoformat()
    window, _config = _window(tmp_path, monkeypatch, app, stats)

    assert window._next_tile.value in ("0:15:00", "0:14:59")   # tick skew
    window.close()


def test_a_starred_window_is_reopened_at_startup(tmp_path, monkeypatch, app):
    from app.ui.overlay import Overlay

    monkeypatch.chdir(tmp_path)
    config = ConfigManager()
    config.data.stats_window.favorite = True
    config.save()

    overlay = Overlay(ConfigManager(), _WM())
    overlay.restore_favorite_windows()

    assert overlay._stats_window is not None
    assert overlay._stats_window.isVisible()
    overlay._stats_window.close()
