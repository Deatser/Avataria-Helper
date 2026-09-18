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
from modules.gardener.trash import BUTTERFLY, TRASH_KINDS, TrashFind
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


def _fake_screen(region):
    """A grab that never touches the real screen — only its shape has to
    be plausible; nothing in the cleaning loop reads this content any
    more, only grab_window's, stubbed separately in _stub_environment."""
    return np.zeros((region["height"], region["width"], 3), np.uint8)


def _window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = ConfigManager()
    return GardenerWindow(config.data.gardener, config.save, _WM()), config


def _stub_environment(monkeypatch, window, found, hwnd=4242):
    """The game is present, the garden rectangle is already known, and a
    scan finds exactly `found` — everything a run needs before it clicks
    anything. The screen itself is never really grabbed.

    Every click also walks straight through to being credited, whichever
    object it is: grab_window repeats a 7-call cycle — a baseline that is
    never compared to anything, a second call that differs from it
    (one tick of movement, enough to set did_move), and five more calls
    identical to that one (_STILL_TICKS quiet readings in a row, enough to
    end the watch) — so a test of picking order or bookkeeping across
    several objects is not also a test that waits out a real five-second
    stillness streak for each one. The tick itself is also shrunk to
    nothing.
    """
    window._garden_area = Area(score=0.9, left=0, top=0,
                               width=1600, height=900)
    monkeypatch.setattr(window._wm, "get_game_hwnd", lambda: hwnd,
                        raising=False)

    def fake_scan(*a, kinds=None, **k):
        # Real scan() only ever returns what was asked for — once the
        # bushes are done and the butterfly hunt's own scan(kinds=[...])
        # comes asking, it must not be handed the leftover bush/beetle
        # finds back and mistake them for butterflies.
        if kinds is None:
            return found
        keys = {kind.key for kind in kinds}
        return [item for item in found if item.kind.key in keys]

    monkeypatch.setattr(window_module, "scan", fake_scan)
    monkeypatch.setattr(window_module, "grab_screen_region", _fake_screen)
    monkeypatch.setattr(window_module, "_WATCH_MS", 1)
    # The watch itself now judges stillness by real elapsed time, not a
    # tick count, so a fast tick alone no longer makes it resolve fast —
    # these need shrinking too, or a test would sit through 2.5 real
    # seconds (0.75 for the final check) waiting for either to elapse.
    monkeypatch.setattr(window_module, "_STILL_S", 0.001)
    monkeypatch.setattr(window_module, "_FINAL_REJECT_S", 0.001)
    # Not 1 ms: this one keeps firing for as long as hunting stays active
    # after a test's own assertions are done with it, and a 1 ms repeat
    # timer left running (however briefly, before teardown catches it)
    # was enough event-loop churn to occasionally starve a later test's
    # own _wait_for budget.
    monkeypatch.setattr(window_module, "_HUNT_MS", 50)
    monkeypatch.setattr(window_module, "_CATCH_WATCH_MS", 1)
    frame_calls = {"n": 0}

    def moving_then_still(hwnd, region):
        idx = frame_calls["n"] % 7
        frame_calls["n"] += 1
        value = 0 if idx == 0 else 255
        return np.full((region["height"], region["width"], 3), value, np.uint8)

    monkeypatch.setattr(window_module, "grab_window", moving_then_still)


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
                      *[k.singular for k in TRASH_KINDS],  # one button each,
                                                            # hidden by default
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


def test_the_kind_buttons_are_hidden_until_settings_turns_them_on(
        tmp_path, monkeypatch, app):
    """Off by default — a troubleshooting tool, not something a normal run
    needs cluttering the window. The switch in Settings rolls the block
    open (and back shut) rather than snapping it, hence the animation
    rather than a plain setVisible."""
    window, config = _window(tmp_path, monkeypatch)
    assert config.data.gardener.show_kind_buttons is False
    assert window._kind_buttons_box.maximumHeight() == 0

    window._toggle_settings()
    switch = window._settings._kind_switch
    assert switch.isChecked() is False

    switch.setChecked(True)
    assert config.data.gardener.show_kind_buttons is True
    assert window._kind_buttons_anim is not None
    assert window._kind_buttons_anim.endValue() > 0

    switch.setChecked(False)
    assert config.data.gardener.show_kind_buttons is False
    assert window._kind_buttons_anim.endValue() == 0
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

    window._toggle_cleaning()
    app.processEvents()                 # the run starts a turn later
    assert window._running is True
    assert window._start_btn.text() == "■  Выключить бота по уборке"

    window._toggle_cleaning()
    assert window._running is False
    assert window._start_btn.text() == "▶  Запустить бота по уборке"
    window.close()


def test_starting_a_run_tells_the_helper_log_too(tmp_path, monkeypatch, app):
    """Not just the module's own log — the start of a run is worth a line
    in the helper's shared log too, [Садовник] marking whose line it is,
    so it is visible without the module even being open."""
    class _FakeOverlay:
        def __init__(self):
            self.logged = []

        def add_log_segments(self, segments, level="info"):
            self.logged.append((segments, level))

        def on_module_closed(self, name):
            pass   # window.close() below expects this to exist

    config = ConfigManager()
    overlay = _FakeOverlay()
    window = GardenerWindow(config.data.gardener, config.save, _WM(), overlay)
    _stub_environment(monkeypatch, window, [])
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)

    window._toggle_cleaning()
    app.processEvents()

    assert any(
        segs[0] == ("[Садовник] ", theme.GD_OLIVE)
        and "Запущена уборка" in segs[1][0]
        for segs, _level in overlay.logged)
    window.close()


def test_an_empty_garden_moves_straight_to_the_butterfly_hunt(
        tmp_path, monkeypatch, app):
    """Nothing walkable does not stop the run any more — it means the
    bushes and beetles are done, so it turns to the butterflies instead."""
    window, _config = _window(tmp_path, monkeypatch)
    _stub_environment(monkeypatch, window, [])
    clicks = []
    monkeypatch.setattr(window_module, "click_at",
                        lambda hwnd, x, y: clicks.append((x, y)) or True)

    window._toggle_cleaning()
    app.processEvents()

    assert clicks == []
    assert _wait_for(app, window, "Объект не найден")
    assert _wait_for(app, window, "Начинаем ловить бабочек")
    assert window._running is True
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
    """Credited once the character has walked to it, gone still, and the
    fixed clean-up wait has passed — nothing about a score enters into it
    any more, so _handled alone keeps the next search from finding it
    again once it is gone."""
    window, _config = _window(tmp_path, monkeypatch)
    target = TrashFind(TRASH_KINDS[0], 0.9, 50, 50, accepted=True)
    _stub_environment(monkeypatch, window, [target])
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "Объект убран")
    assert window._board.bar(TRASH_KINDS[0].key).done == 1
    assert window._board.bar("__total__").done == 1
    assert _wait_for(app, window, "Объект не найден")   # nothing left after it
    window.close()


# ── The movement check before watching the character arrive ─────────────────

def test_a_click_that_never_makes_the_character_move_is_rejected(
        tmp_path, monkeypatch, app):
    """A misdetection gets clicked exactly like real litter would, but the
    character never sets off toward it — two quiet looks in a row, not one
    of them showing more change than the garden already has on its own,
    and the pick is struck off rather than waited on for an arrival that
    was never coming."""
    window, _config = _window(tmp_path, monkeypatch)
    target = TrashFind(TRASH_KINDS[0], 0.9, 50, 50, accepted=True)
    _stub_environment(monkeypatch, window, [target])
    monkeypatch.setattr(window_module, "grab_window",
                        lambda hwnd, region: np.zeros(
                            (region["height"], region["width"], 3), np.uint8))
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "Игрок стоит")
    assert _wait_for(app, window, "объект фейк")
    assert window._board.bar(TRASH_KINDS[0].key).total == 0
    assert window._board.bar("__total__").total == 0
    window.close()


def test_objects_are_numbered_as_they_are_picked(tmp_path, monkeypatch, app):
    """A running count across the whole run: the search line spells out
    which attempt this is ("первый", "второй", ...), and once the
    character has actually arrived at one the arrival line names it by
    the same running number, as a plain "№N"."""
    window, _config = _window(tmp_path, monkeypatch)
    first  = TrashFind(TRASH_KINDS[0], 0.9, 50, 50, accepted=True)
    second = TrashFind(TRASH_KINDS[1], 0.9, 200, 20, accepted=True)
    _stub_environment(monkeypatch, window, [first, second])
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "Ищем второй объект")
    assert _wait_for(app, window, "объекту №2")
    assert _wait_for(app, window, "Объект не найден")
    text = window._log.toPlainText()
    assert "Ищем первый объект" in text
    assert "объекту №1" in text
    window.close()


# ── The background popup check ───────────────────────────────────────────────

def test_a_closed_popup_is_logged_once(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    window._running = True
    monkeypatch.setattr(window_module, "_POPUP_RECHECK_MS", 1)
    template = np.zeros((20, 60), np.uint8)
    monkeypatch.setattr(window_module, "load_template", lambda name: template)
    monkeypatch.setattr(window_module, "grab_screen_region", _fake_screen)
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
    monkeypatch.setattr(window_module, "grab_screen_region", _fake_screen)
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
    monkeypatch.setattr(window_module, "grab_screen_region", _fake_screen)
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


# ── The "garden finished" detector ───────────────────────────────────────────

def test_finishing_the_garden_counts_a_cleanup(tmp_path, monkeypatch, app):
    """gardener_done.png showing up is not just a log line and a stopped
    bot — it is also the one moment the completed-cleanups counter (shown
    in the Statistics window) is allowed to move."""
    from app.core.stats import StatsManager

    monkeypatch.chdir(tmp_path)
    config = ConfigManager()
    stats  = StatsManager()
    window = GardenerWindow(config.data.gardener, config.save, _WM(),
                            stats=stats)
    monkeypatch.setattr(window_module, "grab_window",
                        lambda hwnd: np.zeros((20, 20, 3), np.uint8))
    template = np.zeros((20, 20), np.uint8)
    monkeypatch.setattr(window_module, "load_template", lambda name: template)
    monkeypatch.setattr(window_module, "best_match",
                        lambda scene, tmpl: (0.99, (0, 0)))

    window._check_done(4242)

    assert stats.data.gardener.shifts_finished == 1
    window.close()


def test_a_match_below_threshold_does_not_count_a_cleanup(
        tmp_path, monkeypatch, app):
    from app.core.stats import StatsManager

    monkeypatch.chdir(tmp_path)
    config = ConfigManager()
    stats  = StatsManager()
    window = GardenerWindow(config.data.gardener, config.save, _WM(),
                            stats=stats)
    monkeypatch.setattr(window_module, "grab_window",
                        lambda hwnd: np.zeros((20, 20, 3), np.uint8))
    template = np.zeros((20, 20), np.uint8)
    monkeypatch.setattr(window_module, "load_template", lambda name: template)
    monkeypatch.setattr(window_module, "best_match",
                        lambda scene, tmpl: (0.10, (0, 0)))

    window._check_done(4242)

    assert stats.data.gardener.shifts_finished == 0
    window.close()


def test_after_one_object_the_next_leftmost_one_is_taken(
        tmp_path, monkeypatch, app):
    """The whole point of the loop: one done, the next is picked up on its
    own — with nowhere to measure from yet, the very first pick is the
    leftmost one."""
    window, _config = _window(tmp_path, monkeypatch)
    first  = TrashFind(TRASH_KINDS[0], 0.9, 50, 50, accepted=True)
    second = TrashFind(TRASH_KINDS[1], 0.9, 200, 20, accepted=True)
    _stub_environment(monkeypatch, window, [first, second])
    clicks = []
    monkeypatch.setattr(window_module, "click_at",
                        lambda hwnd, x, y: clicks.append((x, y)) or True)

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "Объект не найден")
    assert clicks == [(50, 50), (200, 20)]
    window.close()


def test_the_next_object_is_the_nearest_to_the_last_one_not_the_leftmost(
        tmp_path, monkeypatch, app):
    """A raster sweep would take the far one next, since it starts further
    left — but it sits nowhere near where the character already is, and
    the nearer one is only a little further right. Distance from the last
    stop wins over which one is more to the left."""
    window, _config = _window(tmp_path, monkeypatch)
    first = TrashFind(TRASH_KINDS[0], 0.9, 100, 100, accepted=True)
    far   = TrashFind(TRASH_KINDS[1], 0.9, 140, 900, accepted=True)   # x=140
    near  = TrashFind(TRASH_KINDS[2], 0.9, 300, 110, accepted=True)   # x=300
    _stub_environment(monkeypatch, window, [first, far, near])
    clicks = []
    monkeypatch.setattr(window_module, "click_at",
                        lambda hwnd, x, y: clicks.append((x, y)) or True)

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "Объект не найден")
    assert clicks == [(100, 100), (300, 110), (140, 900)]
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

    window._toggle_cleaning()
    app.processEvents()

    dots = window._markers._markers
    assert len(dots) == 1
    assert (dots[0].x, dots[0].y) == (100, 500)
    assert dots[0].colour == TRASH_KINDS[1].colour
    window._toggle_cleaning()
    assert window._markers._markers == []      # comes down when the run stops
    window.close()


# ── The butterfly hunt ────────────────────────────────────────────────────────

def test_the_butterfly_hunt_starts_once_nothing_walkable_is_left(
        tmp_path, monkeypatch, app):
    """Once the bushes and beetles are done, the run does not stop — it
    turns to butterflies instead and starts searching for them."""
    window, _config = _window(tmp_path, monkeypatch)
    window._garden_area = Area(score=0.9, left=0, top=0,
                               width=1600, height=900)
    monkeypatch.setattr(window._wm, "get_game_hwnd", lambda: 4242,
                        raising=False)
    monkeypatch.setattr(window_module, "grab_screen_region", _fake_screen)
    monkeypatch.setattr(window_module, "click_at", lambda *a: True)
    monkeypatch.setattr(window_module, "scan", lambda *a, **k: [])

    window._toggle_cleaning()
    app.processEvents()

    assert _wait_for(app, window, "Объект не найден")
    assert _wait_for(app, window, "Начинаем ловить бабочек")
    assert window._running is True
    assert window._hunting is True
    assert window._butterfly_timer is not None
    window.close()


def test_the_hunt_waits_for_a_trail_before_clicking_ahead_of_it(
        tmp_path, monkeypatch, app):
    """No click on the first sighting alone — only once there is enough
    of a trail (_HUNT_MIN_HISTORY points) to judge a direction from, and
    even then not at the last point but one step past it, the same way
    the trail itself had been moving."""
    window, _config = _window(tmp_path, monkeypatch)
    area = Area(score=0.9, left=0, top=0, width=1600, height=900)
    window._garden_area = area
    window._hunting = True
    window._butterfly_timer = window_module.QTimer(window)   # search "active"

    clock = {"t": 0.0}

    def fake_monotonic():
        clock["t"] += 0.2
        return clock["t"]

    monkeypatch.setattr(window_module.time, "monotonic", fake_monotonic)
    positions = iter([(500, 500), (510, 500)])   # moving right

    def fake_scan(*a, kinds=None, **k):
        x, y = next(positions)
        return [TrashFind(BUTTERFLY, 0.9, x, y, accepted=True)]

    monkeypatch.setattr(window_module, "scan", fake_scan)
    clicks = []
    monkeypatch.setattr(window_module, "click_at",
                        lambda hwnd, x, y: clicks.append((x, y)) or True)
    monkeypatch.setattr(window_module, "grab_window",
                        lambda hwnd, region: np.zeros(
                            (region["height"], region["width"], 3), np.uint8))

    window._hunt_tick(4242, area)                       # wide search — first sighting
    assert clicks == []                                  # still building the trail
    window._hunt_track_tick(4242, area, 500, 500)        # localized — second, triggers it

    assert len(clicks) == 1
    click_x, _click_y = clicks[0]
    assert click_x > 510                      # ahead of the trail, not its last point
    assert window._butterfly_timer is None    # search paused while this is watched
    assert _wait_for(app, window, "Кликнули по бабочке")
    window.close()


def test_a_lost_trail_is_dropped_and_the_dot_cleared(
        tmp_path, monkeypatch, app):
    """A butterfly that stops matching — flew off, or just turned to an
    angle none of the 26 templates catches — takes its dot and its trail
    with it, so an old trail never gets stitched onto a new sighting."""
    window, _config = _window(tmp_path, monkeypatch)
    area = Area(score=0.9, left=0, top=0, width=1600, height=900)
    window._garden_area = area
    window._hunting = True
    window._butterfly_timer = window_module.QTimer(window)

    monkeypatch.setattr(
        window_module, "scan",
        lambda *a, **k: [TrashFind(BUTTERFLY, 0.9, 500, 500, accepted=True)])
    window._hunt_tick(4242, area)
    assert len(window._markers._markers) == 1
    assert len(window._hunt_history) == 1

    monkeypatch.setattr(window_module, "scan", lambda *a, **k: [])
    window._hunt_tick(4242, area)

    assert window._markers._markers == []
    assert window._hunt_history == []
    window.close()
