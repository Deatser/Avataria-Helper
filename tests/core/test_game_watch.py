# tests/core/test_game_watch.py
"""Слежение за размером игры и запись эталона."""
import pytest

from app.core import game_watch as watch_module
from app.core.config import ConfigManager
from app.core.game_geometry import Frame, geometry, set_primary
from app.core.game_watch import GameWatch

HWND = 1
FULL = Frame(left=0, top=0, width=2560, height=1368)
SMALL = Frame(left=100, top=100, width=1280, height=684)


@pytest.fixture(autouse=True)
def clean_geometry():
    set_primary(HWND)
    geom = geometry(HWND)
    geom.set_reference(None)
    geom.set_current(None)
    yield geom
    geom.set_reference(None)
    geom.set_current(None)
    set_primary(0)


class _WM:
    def __init__(self, hwnd=HWND):
        self.hwnd = hwnd

    def get_game_hwnd(self): return self.hwnd
    def is_game_alive(self): return bool(self.hwnd)


@pytest.fixture
def stage(monkeypatch, tmp_path):
    """Игра нужного размера, без единого настоящего вызова Win32."""
    monkeypatch.chdir(tmp_path)
    state = {"frame": FULL, "maximised": True}

    monkeypatch.setattr(watch_module, "client_frame",
                        lambda hwnd: state["frame"])
    monkeypatch.setattr(watch_module, "picture_frame",
                        lambda hwnd, image=None, origin=None:
                            state.get("picture") or state["frame"])
    monkeypatch.setattr(watch_module, "grab_window_raw",
                        lambda hwnd, fresh=True: (None, (0, 0)))
    monkeypatch.setattr(watch_module, "_looks_maximised",
                        lambda hwnd: state["maximised"])
    return state


def _watch(stage) -> tuple[GameWatch, ConfigManager]:
    config = ConfigManager()
    return GameWatch(_WM(), config), config


# ── Запись эталона ──────────────────────────────────────────────────────────

def test_a_maximised_game_becomes_the_reference(stage, clean_geometry):
    """Первый запуск: игра развёрнута, значит она сейчас ровно в том виде,
    в каком снимались все шаблоны."""
    watcher, config = _watch(stage)

    watcher.check()

    assert clean_geometry.reference == FULL
    assert config.data.game.reference_width == 2560
    assert config.data.game.reference_height == 1368


def test_a_windowed_game_is_not_taken_for_the_reference(stage,
                                                        clean_geometry):
    """Объявить эталоном окно в четверть экрана значит промахиваться
    отныне везде — под этот размер ничего не резалось."""
    stage["frame"] = SMALL
    stage["maximised"] = False
    watcher, config = _watch(stage)

    watcher.check()

    assert clean_geometry.reference is None
    assert config.data.game.reference_width == 0


def test_the_reference_is_taken_only_once(stage, clean_geometry):
    watcher, _config = _watch(stage)
    watcher.check()

    stage["frame"] = Frame(0, 0, 1920, 1080)
    watcher.check()

    assert clean_geometry.reference == FULL


def test_the_reference_comes_back_from_config(stage, clean_geometry):
    watcher, config = _watch(stage)
    watcher.check()
    clean_geometry.set_reference(None)

    GameWatch(_WM(), config).restore_reference()

    assert clean_geometry.reference == FULL


def test_asking_for_it_by_hand_overwrites_the_old_one(stage, clean_geometry):
    """Кнопка в настройках: шаблоны пересняты, монитор другой."""
    watcher, config = _watch(stage)
    watcher.check()

    stage["frame"] = SMALL
    assert watcher.record_reference() == SMALL

    assert clean_geometry.reference == SMALL
    assert config.data.game.reference_left == 100


# ── Такт ────────────────────────────────────────────────────────────────────

def test_a_resize_is_noticed(stage, clean_geometry):
    watcher, _config = _watch(stage)
    watcher.check()
    seen = []
    watcher.changed.connect(lambda: seen.append(True))

    stage["frame"] = SMALL
    watcher.check()

    assert seen == [True]
    assert clean_geometry.current == SMALL
    assert clean_geometry.scale == 0.5


def test_a_quiet_tick_costs_nothing_once_the_game_has_settled(stage,
                                                              clean_geometry,
                                                              monkeypatch):
    """Дорогая часть — снять кадр — не должна случаться, пока игру не
    трогали: такт крутится четыре раза в секунду."""
    watcher, _config = _watch(stage)
    watcher.check()
    for _ in range(watch_module.SETTLE_TICKS):
        watcher.check()          # игра догоняет рамку — эти такты мерят
    grabs = []
    monkeypatch.setattr(watch_module, "grab_window_raw",
                        lambda hwnd, fresh=True: (grabs.append(1), (None, (0, 0)))[1])

    watcher.check()
    watcher.check()

    assert grabs == []


def test_a_closed_game_leaves_no_scale_behind(stage, clean_geometry):
    """Иначе следующий мод считал бы по размеру окна, которого уже нет."""
    watcher, _config = _watch(stage)
    watcher.check()
    watcher._wm.hwnd = 0

    watcher.check()

    assert clean_geometry.current is None
    assert clean_geometry.scale == 1.0


# ── Молчать нельзя ──────────────────────────────────────────────────────────

def test_a_windowed_game_without_a_reference_says_so(stage, clean_geometry):
    """Без эталона помощник просто ничего не подстраивает, и снаружи это
    выглядит как «не работает». Значит, надо сказать."""
    stage["frame"] = SMALL
    stage["maximised"] = False
    watcher, _config = _watch(stage)
    said = []
    watcher.hint.connect(said.append)

    watcher.check()

    assert len(said) == 1
    assert "разверн" in said[0].lower()


def test_it_says_so_only_once(stage, clean_geometry):
    stage["frame"] = SMALL
    stage["maximised"] = False
    watcher, _config = _watch(stage)
    said = []
    watcher.hint.connect(said.append)

    watcher.check()
    stage["frame"] = Frame(0, 0, 900, 500)
    watcher.check()

    assert len(said) == 1


def test_a_game_with_a_reference_says_nothing(stage, clean_geometry):
    watcher, _config = _watch(stage)
    said = []
    watcher.hint.connect(said.append)

    watcher.check()                     # развёрнута — эталон записан
    stage["frame"] = SMALL
    stage["maximised"] = False
    watcher.check()

    assert said == []


def test_a_game_that_turns_up_later_is_still_picked_up(stage, clean_geometry):
    """Помощник запускают и до игры тоже. Раньше слежение в такой сессии не
    начиналось уже никогда — и всё оставалось без масштаба до перезапуска."""
    watcher, config = _watch(stage)
    watcher._wm.hwnd = 0

    watcher.check()                     # игры ещё нет
    assert config.data.game.reference_width == 0

    watcher._wm.hwnd = HWND
    watcher.check()                     # поднялась, развёрнута

    assert clean_geometry.reference == FULL


def test_a_restarted_game_gets_its_reference_back(stage, clean_geometry):
    """У перезапущенной игры новый hwnd, а значит и новая, пустая геометрия:
    эталон ей надо поднять из config заново."""
    watcher, config = _watch(stage)
    watcher.check()

    watcher._wm.hwnd = HWND + 1         # окно то же, ручка другая
    watcher.check()

    assert geometry(HWND + 1).reference == FULL
    geometry(HWND + 1).set_reference(None)


# ── Игра догоняет свою рамку не сразу ───────────────────────────────────────

def test_the_picture_is_measured_again_after_the_frame_stops_moving(
        stage, clean_geometry):
    """Рамку Windows меняет сразу, а игра внутри перерисовывается сама и не
    в тот же миг: кадр, снятый ровно в момент движения, — это ещё старая
    картинка в новой рамке. Померив один раз и на этом успокоившись, помощник
    оставался с масштабом, которого уже нет: окна встают криво, клики модов
    уходят мимо."""
    watcher, _config = _watch(stage)
    watcher.check()                     # эталон записан с развёрнутой игры

    stage["frame"] = SMALL              # рамку потянули…
    stage["picture"] = FULL             # …а игра рисует ещё старую картинку
    watcher.check()
    assert clean_geometry.current == FULL

    stage["picture"] = SMALL            # игра догнала рамку
    watcher.check()

    assert clean_geometry.current == SMALL


def test_a_frame_that_never_came_is_measured_again(stage, clean_geometry,
                                                   monkeypatch):
    """Кадр может не отдаться — игра перерисовывается, свернулась на миг.
    Запомнить рамку как промеренную значит решить следующим тактом, что всё
    улеглось, и остаться с чужим размером навсегда."""
    watcher, _config = _watch(stage)
    watcher.check()
    monkeypatch.setattr(watch_module, "picture_frame",
                        lambda hwnd, image=None, origin=None: None)

    stage["frame"] = SMALL
    for _ in range(watch_module.SETTLE_TICKS + 2):
        watcher.check()
    assert clean_geometry.current == FULL     # мерить было нечего

    monkeypatch.setattr(watch_module, "picture_frame",
                        lambda hwnd, image=None, origin=None: SMALL)
    watcher.check()

    assert clean_geometry.current == SMALL
