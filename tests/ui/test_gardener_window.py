# tests/ui/test_gardener_window.py
import time

import numpy as np
import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QPushButton

from app.core.config import ConfigManager
from app.module_registry import MODULES
from app.ui import theme
from modules.gardener import GardenerModule
from modules.gardener.garden_area import Area
from modules.gardener.trash import TRASH_KINDS, TrashFind
from modules.gardener.window import GardenerWindow
import modules.gardener.window as window_module


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _WM:
    def get_game_hwnd(self): return 0
    def is_game_alive(self): return False
    def attach_child(self, hwnd): pass
    def move_window(self, *args): pass
    def window_rect_screen(self, hwnd): return (0, 0, 1600, 900)


class _FakeCapture:
    """A grab that never touches the real screen — its content does not
    matter to these tests, since score_at is stubbed wherever it would be
    read; it only has to be a plausible-shaped image."""

    def grab(self, region):
        return np.zeros((region["height"], region["width"], 3), np.uint8)


def _window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = ConfigManager()
    return GardenerWindow(config.data.gardener, config.save, _WM()), config


def _stub_environment(monkeypatch, window, found, hwnd=4242):
    """The game is present, the garden rectangle is already known, and a
    scan finds exactly `found` — everything a run needs before it clicks
    anything. The screen itself is never really grabbed.

    Every click also passes the movement-verification gate on its first
    look: grab_window alternates between two starkly different frames, a
    world apart from the noise threshold, and the wait between looks is
    shrunk to nothing — so a test of the scoring loop below it is not also
    a test that waits out five real seconds first.
    """
    window._garden_area = Area(score=0.9, left=0, top=0,
                               width=1600, height=900)
    monkeypatch.setattr(window._wm, "get_game_hwnd", lambda: hwnd,
                        raising=False)
    monkeypatch.setattr(window_module, "scan", lambda *a, **k: found)
    monkeypatch.setattr(window_module.ScreenCapture, "get",
                        classmethod(lambda cls: _FakeCapture()))
    monkeypatch.setattr(window_module, "_MOVE_CHECK_MS", 1)
    frame_calls = {"n": 0}

    def moving_frame(hwnd, region):
        frame_calls["n"] += 1
        value = 255 if frame_calls["n"] % 2 else 0
        return np.full((region["height"], region["width"], 3), value, np.uint8)

    monkeypatch.setattr(window_module, "grab_window", moving_frame)


def _stub_scan_rounds(monkeypatch, rounds):
    """scan() returns each of `rounds` in turn, then repeats the last one —
    for simulating an object no longer being there on the look after it."""
    calls = {"n": 0}

    def fake_scan(*a, **k):
        i = min(calls["n"], len(rounds) - 1)
        calls["n"] += 1
        return rounds[i]

    monkeypatch.setattr(window_module, "scan", fake_scan)


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
                      "▭  Определить игрока",
                      *[k.singular for k in TRASH_KINDS],  # one button each
                      "⌫"]                              # clears the log
    assert window._log is not None
    window.close()


def test_nothing_starts_without_the_game(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)   # _WM has no game window

    window._toggle_cleaning()
    app.processEvents()

    assert window._running is False
    assert _wait_for(app, window, "не найдено")
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


# ── Scanning within the found garden rectangle ──────────────────────────────

def test_every_scan_is_pointed_at_the_fixed_garden_rectangle(
        tmp_path, monkeypatch, app):
    """No searching for the garden any more — the rectangle is calibrated
    and fixed, so every scan is asked for the same region from the start."""
    window, _config = _window(tmp_path, monkeypatch)
    seen_regions = []
    monkeypatch.setattr(window_module, "scan",
                        lambda *a, **k: seen_regions.append(k.get("region"))
                        or [])

    window._run_scan()
    window._run_scan()

    assert seen_regions == [window._garden_area.region] * 2
    window.close()


def test_a_kind_button_shows_only_that_kind_with_its_threshold(
        tmp_path, monkeypatch, app):
    """Its own coordinates and the bar it is judged against — a near miss
    included, drawn hollow, so a threshold that is a little too strict is
    as visible as one that is a little too loose."""
    window, _config = _window(tmp_path, monkeypatch)
    window._garden_area = Area(score=0.9, left=0, top=0,
                               width=1600, height=900)
    kind      = TRASH_KINDS[3]   # pink_bush
    other     = TRASH_KINDS[0]   # dry_bush — must not show up at all
    hit       = TrashFind(kind, 0.72, 100, 100, accepted=True)
    near      = TrashFind(kind, 0.50, 300, 50, accepted=False)
    unrelated = TrashFind(other, 0.9, 10, 10, accepted=True)
    monkeypatch.setattr(window_module, "scan",
                        lambda *a, **k: [hit, near, unrelated])

    window._run_kind_scan(kind)

    dots = window._markers._markers
    assert {(d.x, d.y, d.solid) for d in dots} == {
        (100, 100, True), (300, 50, False)}

    assert _wait_for(app, window, f"{kind.threshold * 100:.0f}%")
    assert _wait_for(app, window, "(100, 100)")
    assert _wait_for(app, window, "(300, 50)")
    assert "(10, 10)" not in window._log.toPlainText()  # the other kind stays out
    window.close()


def test_a_kind_scan_marks_pass_and_fail_in_different_colours(
        tmp_path, monkeypatch, app):
    """Whatever cleared the bar keeps the kind's own colour; whatever
    didn't turns red regardless of kind — dot and log line alike, so a
    failing match reads as a failure at a glance, not just a lower number."""
    window, _config = _window(tmp_path, monkeypatch)
    window._garden_area = Area(score=0.9, left=0, top=0,
                               width=1600, height=900)
    kind = TRASH_KINDS[3]   # pink_bush
    hit  = TrashFind(kind, 0.72, 100, 100, accepted=True)
    near = TrashFind(kind, 0.50, 300, 50, accepted=False)
    monkeypatch.setattr(window_module, "scan", lambda *a, **k: [hit, near])

    logged = []
    real_add = window._log.add_log_segments
    monkeypatch.setattr(
        window._log, "add_log_segments",
        lambda segs, **kw: logged.append(segs) or real_add(segs, **kw))

    window._run_kind_scan(kind)

    dots = {(d.x, d.y): d for d in window._markers._markers}
    assert dots[(100, 100)].colour == kind.colour
    assert dots[(300, 50)].colour == theme.ACCENT_RED

    def _line_with(needle):
        return next(segs for segs in logged
                    if any(needle in text for text, _colour in segs))

    hit_colours  = {colour for text, colour in _line_with("(100, 100)")
                    if "(" in text or "%" in text}
    near_colours = {colour for text, colour in _line_with("(300, 50)")
                    if "(" in text or "%" in text}
    assert hit_colours == {kind.colour}
    assert near_colours == {theme.ACCENT_RED}
    window.close()


# ── Showing the scanned rectangle, and watching it change ───────────────────

def test_the_player_button_starts_and_stops_real_time_tracking(
        tmp_path, monkeypatch, app):
    """"Определить игрока" no longer draws its own small box — the
    character can spawn in more than one spot, so a rectangle sized and
    placed for it can't be calibrated once and trusted. Instead it shows
    the same full-garden rectangle the other debug button does, and starts
    watching how much that rectangle changes frame to frame."""
    window, _config = _window(tmp_path, monkeypatch)
    window._garden_area = Area(score=0.9, left=780, top=313,
                               width=1000, height=797)
    monkeypatch.setattr(window._wm, "get_game_hwnd", lambda: 4242,
                        raising=False)
    monkeypatch.setattr(
        window_module, "grab_window",
        lambda hwnd, region: np.zeros(
            (region["height"], region["width"], 3), np.uint8))

    assert not window._area_overlay.isVisible()
    window._toggle_area_overlay()

    assert window._area_overlay.isVisible()
    assert window._area_overlay.geometry() == QRect(780, 313, 1000, 797)
    assert window._track_timer is not None
    assert window._track_timer.interval() == 1000
    assert _wait_for(app, window, "Начинаем отслеживать")

    window._toggle_area_overlay()
    assert not window._area_overlay.isVisible()
    assert window._track_timer is None
    window.close()


class _FrameCapture:
    """Hands back whatever frame it is currently holding — swapped out
    between ticks to stand in for the game window having changed. Matches
    grab_window's own (hwnd, region) signature so it can stand in for it
    directly."""

    def __init__(self, frame):
        self.frame = frame

    def __call__(self, hwnd, region):
        return self.frame


def test_movement_starting_and_stopping_are_each_logged_once(
        tmp_path, monkeypatch, app):
    """Only the two edges get a line — not every second movement holds,
    and not the quiet stretches either — so a beetle's small, constant
    flicker never crosses into "started moving" at all."""
    window, _config = _window(tmp_path, monkeypatch)
    area = Area(score=0.9, left=0, top=0, width=10, height=10)

    blank = np.zeros((10, 10, 3), np.uint8)
    window._track_prev_frame = blank
    capture = _FrameCapture(blank)
    monkeypatch.setattr(window_module, "grab_window", capture)

    window._track_tick(4242, area)   # identical frame — below the threshold
    assert "движение" not in window._log.toPlainText()

    capture.frame = np.full((10, 10, 3), 128, np.uint8)
    window._track_tick(4242, area)   # blank to grey — well past it
    assert _wait_for(app, window, "Игрок начал движение")
    assert window._track_moving

    capture.frame = np.full((10, 10, 3), 255, np.uint8)
    window._track_tick(4242, area)   # grey to white — still moving, quiet
    assert window._log.toPlainText().count("Игрок начал движение") == 1

    window._track_tick(4242, area)   # white to white — the change stops
    assert _wait_for(app, window, "Игрок прекратил движение")
    assert not window._track_moving
    window.close()


# ── The one-object debug pass ────────────────────────────────────────────────

def test_the_first_object_is_the_one_most_top_left(tmp_path, monkeypatch, app):
    """Sorted by x then y — the same order a real sweep would walk them."""
    window, _config = _window(tmp_path, monkeypatch)
    far    = TrashFind(TRASH_KINDS[0], 0.9, 900, 50, accepted=True)
    near   = TrashFind(TRASH_KINDS[1], 0.9, 100, 500, accepted=True)
    a_miss = TrashFind(TRASH_KINDS[2], 0.3, 10, 10, accepted=False)
    _stub_environment(monkeypatch, window, [far, near, a_miss])
    clicks = []
    monkeypatch.setattr(window_module, "click_at",
                        lambda hwnd, x, y: clicks.append((x, y)) or True)
    monkeypatch.setattr(window_module, "score_at", lambda *a, **k: 0.9)

    window._toggle_cleaning()
    app.processEvents()

    assert clicks == [(100, 500)]      # leftmost of the accepted ones
    window.close()


def test_the_start_button_flips_while_the_one_object_is_watched(
        tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    found = [TrashFind(TRASH_KINDS[0], 0.9, 100, 100, accepted=True)]
    _stub_environment(monkeypatch, window, found)
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)
    monkeypatch.setattr(window_module, "score_at", lambda *a, **k: 0.9)

    window._toggle_cleaning()
    app.processEvents()                 # the run starts a turn later
    assert window._running is True
    assert window._start_btn.text() == "■  Выключить бота по уборке"

    window._toggle_cleaning()
    assert window._running is False
    assert window._start_btn.text() == "▶  Запустить бота по уборке"
    window.close()


def test_an_empty_garden_is_reported_and_nothing_is_clicked(
        tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    _stub_environment(monkeypatch, window, [])
    clicks = []
    monkeypatch.setattr(window_module, "click_at",
                        lambda hwnd, x, y: clicks.append((x, y)) or True)

    window._toggle_cleaning()
    app.processEvents()

    assert clicks == []
    assert window._running is False
    assert _wait_for(app, window, "Объект не найден")
    window.close()


# ── The progress board ───────────────────────────────────────────────────────

def test_the_board_is_built_from_the_opening_count(tmp_path, monkeypatch, app):
    """One bar for the whole haul, one for each kind actually present —
    kinds with nothing found get no bar of their own."""
    window, _config = _window(tmp_path, monkeypatch)
    dry  = TrashFind(TRASH_KINDS[0], 0.9, 10, 10, accepted=True)
    blue = TrashFind(TRASH_KINDS[1], 0.9, 900, 50, accepted=True)
    another_dry = TrashFind(TRASH_KINDS[0], 0.9, 300, 10, accepted=True)
    _stub_environment(monkeypatch, window, [dry, blue, another_dry])
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)
    monkeypatch.setattr(window_module, "score_at", lambda *a, **k: 0.9)

    window._toggle_cleaning()
    app.processEvents()

    board = window._board
    assert set(board._bars) == {"__total__", TRASH_KINDS[0].key, TRASH_KINDS[1].key}
    assert board.bar("__total__")._total == 3
    assert board.bar(TRASH_KINDS[0].key)._total == 2
    assert board.bar(TRASH_KINDS[1].key)._total == 1
    window.close()


def test_a_cleared_object_advances_its_bar_and_the_total(
        tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    monkeypatch.setattr(window_module, "_POLL_MS", 1)
    target = TrashFind(TRASH_KINDS[0], 0.9, 50, 50, accepted=True)
    _stub_environment(monkeypatch, window, [target])
    _stub_scan_rounds(monkeypatch, [[target], [target], []])
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)
    scores = iter([0.9, 0.2])   # still there once, then a sharp drop
    monkeypatch.setattr(window_module, "score_at",
                        lambda *a, **k: next(scores, 0.2))

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "Объект не найден")
    assert window._board.bar(TRASH_KINDS[0].key).done == 1
    assert window._board.bar("__total__").done == 1
    window.close()


# ── The movement check before settling in to watch a score ──────────────────

def test_a_click_that_never_makes_the_character_move_is_rejected(
        tmp_path, monkeypatch, app):
    """A misdetection gets clicked exactly like real litter would, but the
    character never sets off toward it — five quiet looks in a row, not
    one of them showing more change than the garden already has on its
    own, and the pick is struck off rather than watched for a score that
    was never going to move either."""
    window, _config = _window(tmp_path, monkeypatch)
    target = TrashFind(TRASH_KINDS[0], 0.9, 50, 50, accepted=True)
    _stub_environment(monkeypatch, window, [target])
    monkeypatch.setattr(window_module, "grab_window",
                        lambda hwnd, region: np.zeros(
                            (region["height"], region["width"], 3), np.uint8))
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)
    score_calls = []
    monkeypatch.setattr(window_module, "score_at",
                        lambda *a, **k: score_calls.append(1) or 0.9)

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "Игрок не стал двигаться")
    assert _wait_for(app, window, "определили неверно")
    assert window._board.bar(TRASH_KINDS[0].key).total == 0
    assert window._board.bar("__total__").total == 0
    assert not score_calls   # never got as far as watching its score
    window.close()


def test_objects_are_numbered_as_they_are_picked(tmp_path, monkeypatch, app):
    """A running count across the whole run, not restarted per object —
    "объект №2" means the second one taken on, whichever circle it is in."""
    window, _config = _window(tmp_path, monkeypatch)
    monkeypatch.setattr(window_module, "_POLL_MS", 1)
    first  = TrashFind(TRASH_KINDS[0], 0.9, 50, 50, accepted=True)
    second = TrashFind(TRASH_KINDS[1], 0.9, 200, 20, accepted=True)
    _stub_environment(monkeypatch, window, [first, second])
    _stub_scan_rounds(monkeypatch,
                      [[first, second], [first, second], [second], []])
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)
    monkeypatch.setattr(window_module, "score_at", lambda *a, **k: 0.2)

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "Объект не найден")
    text = window._log.toPlainText()
    assert "Выбираем объект №1" in text
    assert "Выбираем объект №2" in text
    window.close()


# ── The background popup check ───────────────────────────────────────────────

def test_a_closed_popup_is_logged_once(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    window._running = True
    monkeypatch.setattr(window_module, "_POPUP_RECHECK_MS", 1)
    template = np.zeros((20, 60), np.uint8)
    monkeypatch.setattr(window_module, "load_template", lambda name: template)
    monkeypatch.setattr(window_module.ScreenCapture, "get",
                        classmethod(lambda cls: _FakeCapture()))
    clicks = []
    monkeypatch.setattr(window_module, "click_at",
                        lambda hwnd, x, y: clicks.append((hwnd, x, y)) or True)
    scores = iter([0.99, 0.10])   # found, then gone once the click lands
    monkeypatch.setattr(window_module, "best_match",
                        lambda scene, tmpl: (next(scores), (10, 10)))

    window._check_popup(4242)
    app.processEvents()

    region = window_module._POPUP_REGION
    assert clicks == [(4242, region["left"] + 10 + 60 // 2,
                       region["top"] + 10 + 20 // 2)]
    assert _wait_for(app, window, "Закрыли лишнее всплывающее окно")
    window.close()


def test_a_click_that_does_not_close_the_popup_stays_silent(
        tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    window._running = True
    monkeypatch.setattr(window_module, "_POPUP_RECHECK_MS", 1)
    template = np.zeros((20, 60), np.uint8)
    monkeypatch.setattr(window_module, "load_template", lambda name: template)
    monkeypatch.setattr(window_module.ScreenCapture, "get",
                        classmethod(lambda cls: _FakeCapture()))
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)
    # Stays just as convinced after the click as before it
    monkeypatch.setattr(window_module, "best_match",
                        lambda scene, tmpl: (0.99, (10, 10)))

    window._check_popup(4242)
    app.processEvents()

    assert "Закрыли" not in window._log.toPlainText()
    window.close()


def test_a_popup_below_threshold_is_ignored(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    window._running = True
    template = np.zeros((20, 60), np.uint8)
    monkeypatch.setattr(window_module, "load_template", lambda name: template)
    monkeypatch.setattr(window_module.ScreenCapture, "get",
                        classmethod(lambda cls: _FakeCapture()))
    clicks = []
    monkeypatch.setattr(window_module, "click_at",
                        lambda *a: clicks.append(a) or True)
    monkeypatch.setattr(window_module, "best_match",
                        lambda scene, tmpl: (0.2, (10, 10)))

    window._check_popup(4242)
    app.processEvents()

    assert clicks == []
    assert window._log.toPlainText().strip() == ""
    window.close()


def test_a_sharp_drop_in_score_reads_as_the_object_being_gone(
        tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    monkeypatch.setattr(window_module, "_POLL_MS", 1)
    target = TrashFind(TRASH_KINDS[0], 0.9, 50, 50, accepted=True)
    _stub_environment(monkeypatch, window, [target])
    # One round for the opening count, one to pick the target, then it's gone
    _stub_scan_rounds(monkeypatch, [[target], [target], []])
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)
    scores = iter([0.9, 0.85, 0.2])   # still there, then a sharp drop
    monkeypatch.setattr(window_module, "score_at",
                        lambda *a, **k: next(scores, 0.2))

    window._toggle_cleaning()
    app.processEvents()
    assert window._running is True

    assert _wait_for(app, window, "Удалили объект")
    assert _wait_for(app, window, "Объект не найден")   # nothing left after it
    assert window._running is False
    window.close()


def test_a_small_drop_that_settles_still_counts_as_gone(
        tmp_path, monkeypatch, app):
    """Some kinds barely look different clean vs dirty — this is the real
    reading from one that never reaches _DROP_RATIO's halving at all, only
    a ~5% drop that then holds. Three steady readings after the drop is
    enough to call it gone, well short of the _STUCK_LOOKS defer."""
    window, _config = _window(tmp_path, monkeypatch)
    monkeypatch.setattr(window_module, "_POLL_MS", 1)
    target = TrashFind(TRASH_KINDS[0], 0.870, 50, 50, accepted=True)
    _stub_environment(monkeypatch, window, [target])
    _stub_scan_rounds(monkeypatch, [[target], [target], []])
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)
    readings = iter([0.864, 0.861, 0.862, 0.863, 0.828, 0.828, 0.828])
    monkeypatch.setattr(window_module, "score_at",
                        lambda *a, **k: next(readings, 0.828))

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "Удалили объект")
    assert "второй круг" not in window._log.toPlainText()
    window.close()


def test_a_mark_that_never_changes_gets_one_retry_in_the_second_circle(
        tmp_path, monkeypatch, app):
    """Clicking it again right away would not help the game along, so a
    reading that never moves is set aside for the second circle instead —
    where, if it still never moves, it is finally left for good."""
    window, _config = _window(tmp_path, monkeypatch)
    monkeypatch.setattr(window_module, "_POLL_MS", 1)
    monkeypatch.setattr(window_module, "_STUCK_LOOKS", 2)
    stuck = TrashFind(TRASH_KINDS[0], 0.9, 50, 50, accepted=True)
    _stub_environment(monkeypatch, window, [stuck])
    monkeypatch.setattr(window_module, "scan", lambda *a, **k: [stuck])
    clicks = []
    monkeypatch.setattr(window_module, "click_at",
                        lambda hwnd, x, y: clicks.append((x, y)) or True)
    monkeypatch.setattr(window_module, "score_at", lambda *a, **k: 0.9)

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "второй круг")
    assert _wait_for(app, window, "Объект не найден")
    assert window._running is False
    assert clicks == [(50, 50), (50, 50)]   # one try in each circle, no more
    window.close()


def test_a_score_that_wobbles_but_never_drops_still_gets_deferred(
        tmp_path, monkeypatch, app):
    """A real screen never holds perfectly still — a character walking near
    an object it has not reached yet wobbles the reading up and down. That
    must not be mistaken for progress and left to poll forever; only an
    actual drop below the bar counts."""
    window, _config = _window(tmp_path, monkeypatch)
    monkeypatch.setattr(window_module, "_POLL_MS", 1)
    monkeypatch.setattr(window_module, "_STUCK_LOOKS", 4)
    target = TrashFind(TRASH_KINDS[0], 0.9, 50, 50, accepted=True)
    _stub_environment(monkeypatch, window, [target])
    monkeypatch.setattr(window_module, "scan", lambda *a, **k: [target])
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)
    # Never identical two reads running, never below the 0.45 drop bar either
    wobble = iter([0.85, 0.73, 0.60, 0.72, 0.58, 0.61, 0.55, 0.63, 0.50, 0.59])
    monkeypatch.setattr(window_module, "score_at",
                        lambda *a, **k: next(wobble, 0.55))

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "второй круг")
    window.close()


def test_a_refilling_spot_does_not_starve_a_stalled_object_of_its_turn(
        tmp_path, monkeypatch, app):
    """A spot that keeps scanning as freshly-found — real regrowth, or a
    false match — must not let the main circle run forever and starve a
    genuinely stalled object of ever reaching the second circle."""
    window, _config = _window(tmp_path, monkeypatch)
    monkeypatch.setattr(window_module, "_POLL_MS", 1)
    monkeypatch.setattr(window_module, "_STUCK_LOOKS", 2)
    recurring = TrashFind(TRASH_KINDS[0], 0.9, 10, 10, accepted=True)
    stuck     = TrashFind(TRASH_KINDS[1], 0.9, 500, 500, accepted=True)
    _stub_environment(monkeypatch, window, [recurring, stuck])
    # Always both there, as if whatever is at (10, 10) refills instantly
    monkeypatch.setattr(window_module, "scan",
                        lambda *a, **k: [recurring, stuck])
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)

    stuck_calls = {"n": 0}

    def fake_score(kind, gray, x, y, origin):
        if (x, y) == (500, 500):
            stuck_calls["n"] += 1
            return 0.9 if stuck_calls["n"] <= 2 else 0.1   # clears on retry
        return 0.1   # the refilling spot clears every time it is tried

    monkeypatch.setattr(window_module, "score_at", fake_score)

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "второй круг")
    assert _wait_for(app, window, "Объект не найден")
    assert window._running is False
    window.close()


def test_the_second_circle_retries_only_what_stalled_in_the_first(
        tmp_path, monkeypatch, app):
    """A stalled object does not block the rest of the main circle, and it
    gets exactly one more try once that circle is done — which this time
    is enough for it to clear."""
    window, _config = _window(tmp_path, monkeypatch)
    monkeypatch.setattr(window_module, "_POLL_MS", 1)
    monkeypatch.setattr(window_module, "_STUCK_LOOKS", 2)
    stuck = TrashFind(TRASH_KINDS[0], 0.9, 10, 10, accepted=True)
    real  = TrashFind(TRASH_KINDS[1], 0.9, 200, 20, accepted=True)
    _stub_environment(monkeypatch, window, [stuck, real])
    state = {"real_cleared": False}

    def fake_scan(*a, **k):
        items = [stuck]
        if not state["real_cleared"]:
            items.append(real)
        return items

    monkeypatch.setattr(window_module, "scan", fake_scan)
    clicks = []
    monkeypatch.setattr(window_module, "click_at",
                        lambda hwnd, x, y: clicks.append((x, y)) or True)

    stuck_calls = {"n": 0}

    def fake_score(kind, gray, x, y, origin):
        if (x, y) == (10, 10):
            stuck_calls["n"] += 1
            return 0.9 if stuck_calls["n"] <= 2 else 0.1   # clears on retry
        state["real_cleared"] = True
        return 0.1                                          # `real` clears at once

    monkeypatch.setattr(window_module, "score_at", fake_score)

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "второй круг")
    assert _wait_for(app, window, "Объект не найден")
    assert window._running is False
    # `stuck` clicked once in the main circle, once more in the second;
    # `real` clicked once, in between, without waiting on `stuck` first
    assert clicks == [(10, 10), (200, 20), (10, 10)]
    window.close()


def test_after_one_object_the_next_leftmost_one_is_taken(
        tmp_path, monkeypatch, app):
    """The whole point of the loop: one done, the next is picked up on its
    own, in the same left-to-right order, without anyone asking again."""
    window, _config = _window(tmp_path, monkeypatch)
    monkeypatch.setattr(window_module, "_POLL_MS", 1)
    first  = TrashFind(TRASH_KINDS[0], 0.9, 50, 50, accepted=True)
    second = TrashFind(TRASH_KINDS[1], 0.9, 200, 20, accepted=True)
    _stub_environment(monkeypatch, window, [first, second])
    # Opening count, pick `first`, `first` is gone so pick `second`, then none
    _stub_scan_rounds(monkeypatch,
                      [[first, second], [first, second], [second], []])
    clicks = []
    monkeypatch.setattr(window_module, "click_at",
                        lambda hwnd, x, y: clicks.append((x, y)) or True)
    monkeypatch.setattr(window_module, "score_at", lambda *a, **k: 0.2)

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "Объект не найден")
    assert window._running is False
    assert clicks == [(50, 50), (200, 20)]
    window.close()


def test_the_object_gets_a_single_dot_of_its_own_colour(
        tmp_path, monkeypatch, app):
    """Only the one object being handled is marked — the rest are left
    exactly as found, untouched."""
    window, _config = _window(tmp_path, monkeypatch)
    found = [TrashFind(TRASH_KINDS[0], 0.9, 900, 50, accepted=True),
             TrashFind(TRASH_KINDS[1], 0.9, 100, 500, accepted=True)]
    _stub_environment(monkeypatch, window, found)
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)
    monkeypatch.setattr(window_module, "score_at", lambda *a, **k: 0.9)

    window._toggle_cleaning()
    app.processEvents()

    dots = window._markers._markers
    assert len(dots) == 1
    assert (dots[0].x, dots[0].y) == (100, 500)
    assert dots[0].colour == TRASH_KINDS[1].colour
    window._toggle_cleaning()
    assert window._markers._markers == []      # comes down when the run stops
    window.close()
