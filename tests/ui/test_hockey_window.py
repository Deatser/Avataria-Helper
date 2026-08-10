# tests/ui/test_hockey_window.py
"""Хоккей's window: the calibration and detection tools the bot is built on.

Nothing here shoots. The game allows one attempt per level, so the module
deliberately has no automatic shot until the motion model can be shown to be
right — see docs/superpowers/specs/2026-08-08-hockey-bot-design.md.
"""
import numpy as np
import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QPushButton

from app.core.config import ConfigManager
from app.module_registry import MODULES
from app.ui import theme
from modules.hockey import HockeyModule
from modules.hockey.rink_area import from_config
from modules.hockey.window import HockeyWindow
import modules.hockey.window as window_module

RED = (0, 0, 255)   # BGR


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _WM:
    def get_game_hwnd(self): return 0
    def is_game_alive(self): return False
    def attach_child(self, hwnd): pass
    def move_window(self, *args): pass
    def window_rect_screen(self, hwnd): return (0, 0, 1600, 900)


def _window(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = ConfigManager()
    hockey = config.data.hockey
    hockey.rink_left, hockey.rink_top = 0, 0
    hockey.rink_width, hockey.rink_height = 400, 400
    hockey.goal_y, hockey.goal_left, hockey.goal_right = 50, 150, 250
    hockey.shooter_x, hockey.shooter_y = 200, 380
    hockey.wall_left, hockey.wall_right = 20, 380
    # Painted rectangles look nothing like a player photo, so the photo gate
    # is off here — what it does has its own tests in test_hockey_detect.py.
    hockey.player_match_min = 0.0
    return HockeyWindow(hockey, config.save, _WM()), config


def _with_game(monkeypatch, window, frame=None, hwnd=4242):
    """The game is there and every grab hands back `frame` — the screen
    itself is never really touched."""
    monkeypatch.setattr(window._wm, "get_game_hwnd", lambda: hwnd,
                        raising=False)
    if frame is None:
        monkeypatch.setattr(
            window_module, "grab_window",
            lambda h, region: np.zeros(
                (region["height"], region["width"], 3), np.uint8))
    else:
        monkeypatch.setattr(window_module, "grab_window",
                            lambda h, region: frame)


def _frame_with_helmet(left, top, size=24):
    frame = np.zeros((400, 400, 3), np.uint8)
    frame[top:top + size, left:left + size] = RED
    return frame


class _Clock:
    """Stands in for the `time` module inside the window, so a test can run
    a watch through seconds of model time in milliseconds of real time —
    the motion model reclassifies on a wall-clock interval, and forty ticks
    fired back to back would otherwise all land in the same instant."""

    def __init__(self, step: float = 0.03):
        self.t = 0.0
        self.step = step

    def monotonic(self) -> float:
        self.t += self.step
        return self.t


def _capture_log(monkeypatch, window) -> list[str]:
    """Every line handed to the log, as plain text.

    Not read back off the widget: LogPanel spells a line out a character at
    a time, so reading it means spinning the event loop until the animation
    catches up — which is slow, and quietly depends on how busy the loop
    already is with other tests' timers. What a line *says* is the thing
    under test here; how it arrives on screen has its own tests.
    """
    lines: list[str] = []
    monkeypatch.setattr(window._log, "add_log",
                        lambda text, *a, **kw: lines.append(text))
    monkeypatch.setattr(
        window._log, "add_log_segments",
        lambda segs, *a, **kw: lines.append(
            "".join(text for text, _colour in segs)))
    return lines


def _said(lines: list[str], needle: str) -> bool:
    return any(needle in line for line in lines)


# ── Registration and chrome ──────────────────────────────────────────────

def test_the_module_is_registered_with_the_ice_accent():
    assert HockeyModule in MODULES
    assert HockeyModule.config_key == "hockey"
    assert HockeyModule.color == theme.HK_ICE


def test_the_window_has_its_tools_and_a_log(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)

    labels = [b.text() for b in window.findChildren(QPushButton)]

    assert labels == ["▲", "☆", "×",                      # window chrome
                      "▶  Запустить слежение за вратарями",
                      "⚙  Настройки",
                      "Каток", "Ворота", "Шайба", "Ряд", "Борт", "Вратарь",
                      "1", "2", "3", "4", "5",        # which row is meant
                      "📐  Отметить область",
                      "🥅  Показать поле", "👁  Показать ряд",
                      "🔬  Тест детекции", "📊  Отчёт по модели",
                      "🎯  Центр", "🎯  Лево", "🎯  Право",
                      "💥  Сделать бросок",
                      "⚠  Сделать принудительный бросок",
                      "⧉", "×",                       # copies / clears the log
                      "Гайд"]
    assert window._log is not None
    window.close()


# ── Guard rails before anything runs ─────────────────────────────────────

def test_nothing_tracks_without_the_game(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)   # _WM has no game window
    config.data.hockey.lanes = [{"y": 100, "height": 60}]
    lines = _capture_log(monkeypatch, window)

    window._toggle_running()

    assert window._running is False
    assert _said(lines, "Игровое окно не найдено")
    window.close()


def test_tracking_refuses_to_start_with_no_rows(tmp_path, monkeypatch, app):
    """Rows are what the detector searches; without them there is nothing to
    scan, and starting anyway would just look like a bot that finds nobody."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = []
    _with_game(monkeypatch, window)
    lines = _capture_log(monkeypatch, window)

    window._toggle_running()

    assert window._running is False
    assert _said(lines, "отметьте ряды")
    window.close()


def test_tracking_starts_and_stops_once_rows_exist(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 100, "height": 60}]
    _with_game(monkeypatch, window)

    window._toggle_running()
    assert window._running is True
    assert window._start_btn.text() == "■  Остановить слежение"

    window._toggle_running()
    assert window._running is False
    assert window._scan_timer is None
    assert window._start_btn.text() == "▶  Запустить слежение за вратарями"
    window.close()


def test_the_game_disappearing_stops_tracking(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 100, "height": 60}]
    _with_game(monkeypatch, window)
    window._toggle_running()
    lines = _capture_log(monkeypatch, window)

    monkeypatch.setattr(window._wm, "get_game_hwnd", lambda: 0, raising=False)
    window._scan_tick(window._geometry())

    assert window._running is False
    assert _said(lines, "пропало")
    window.close()


# ── Calibration writes through to the config ─────────────────────────────

def test_marking_the_rink_writes_it(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    window._target.set_active(window_module._TARGET_RINK)

    window._apply_calibration(QRect(100, 200, 300, 400))

    assert (config.data.hockey.rink_left, config.data.hockey.rink_top) == (100, 200)
    assert (config.data.hockey.rink_width, config.data.hockey.rink_height) == (300, 400)
    assert ConfigManager().data.hockey.rink_left == 100   # and it survives a reload
    window.close()


def test_marking_the_goal_takes_its_bottom_edge_as_the_line(
        tmp_path, monkeypatch, app):
    """The goal sits at the top of the rink and the puck travels upward, so
    the edge it has to cross is the bottom one."""
    window, config = _window(tmp_path, monkeypatch)
    window._target.set_active(window_module._TARGET_GOAL)

    window._apply_calibration(QRect(500, 100, 200, 60))

    assert config.data.hockey.goal_left == 500
    assert config.data.hockey.goal_right == 699
    assert config.data.hockey.goal_y == 159
    window.close()


def test_marking_the_puck_takes_the_middle_of_the_box(
        tmp_path, monkeypatch, app):
    """119 rather than 120: QRect counts its right and bottom edges as the
    last pixel inside, not the first one past."""
    window, config = _window(tmp_path, monkeypatch)
    window._target.set_active(window_module._TARGET_PUCK)

    window._apply_calibration(QRect(100, 100, 40, 40))

    assert (config.data.hockey.shooter_x, config.data.hockey.shooter_y) == (119, 119)
    window.close()


def test_marking_a_row_adds_it(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = []
    window._target.set_active(window_module._TARGET_ROW)

    window._apply_calibration(QRect(0, 100, 400, 80))

    # The drawn box decides the height, not the config default — someone who
    # outlined a row meant that outline.
    assert config.data.hockey.lanes == [
        {"y": 139, "height": 80, "wall_left": 0, "wall_right": 0}]
    window.close()


def _mark_row(window, slot: int, top: int, height: int = 40):
    window._target.set_active(window_module._TARGET_ROW)
    window._row_slot.set_active(slot)
    window._apply_calibration(QRect(0, top, 400, height))


def test_marking_the_same_slot_again_overwrites_that_row(
        tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = []

    _mark_row(window, slot=0, top=100, height=80)
    _mark_row(window, slot=0, top=110, height=40)

    assert config.data.hockey.lanes == [
        {"y": 129, "height": 40, "wall_left": 0, "wall_right": 0}]
    window.close()


def test_rows_a_little_apart_both_survive(tmp_path, monkeypatch, app):
    """Measured 52px apart on the real rink, and the slot is what says which
    row is meant — proximity guessing used to swallow the second one."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = []

    _mark_row(window, slot=0, top=200)   # centre 219
    _mark_row(window, slot=1, top=252)   # centre 271

    assert [lane["y"] for lane in config.data.hockey.lanes] == [219, 271]
    window.close()


def test_every_slot_can_be_marked_not_just_four(tmp_path, monkeypatch, app):
    """The last level adds a fifth row (2026-08-09) — four was the count
    everywhere before that, and it was one short."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = []
    assert window_module._MAX_ROWS == 5

    for slot in range(window_module._MAX_ROWS):
        _mark_row(window, slot=slot, top=100 + slot * 50, height=40)

    assert [lane["y"] for lane in config.data.hockey.lanes] == [
        119, 169, 219, 269, 319]
    window.close()


def test_a_row_marked_out_of_order_is_renumbered_top_to_bottom(
        tmp_path, monkeypatch, app):
    """Slot N and "ряд N" have to mean the same row everywhere, so the file
    is kept in the same order the overlay labels are."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = []
    lines = _capture_log(monkeypatch, window)

    _mark_row(window, slot=0, top=300)   # centre 319
    _mark_row(window, slot=1, top=100)   # centre 119 — above the first

    assert [lane["y"] for lane in config.data.hockey.lanes] == [119, 319]
    assert _said(lines, "слот 2 → ряд 1")
    window.close()


# ── Per-row boards ───────────────────────────────────────────────────────

def test_boards_are_written_to_the_chosen_row(tmp_path, monkeypatch, app):
    """Each row's defender turns round at its own place, so one pair of
    boards for the whole rink was simply wrong (2026-08-09)."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = []
    _mark_row(window, slot=0, top=100)
    _mark_row(window, slot=1, top=200)

    window._target.set_active(window_module._TARGET_WALLS)
    window._row_slot.set_active(1)
    window._apply_calibration(QRect(60, 0, 200, 400))

    assert config.data.hockey.lanes[0]["wall_left"] == 0      # untouched
    assert config.data.hockey.lanes[1]["wall_left"] == 60
    assert config.data.hockey.lanes[1]["wall_right"] == 259
    window.close()


def test_boards_for_a_row_that_does_not_exist_say_so(
        tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = []
    lines = _capture_log(monkeypatch, window)

    window._target.set_active(window_module._TARGET_WALLS)
    window._row_slot.set_active(3)
    window._apply_calibration(QRect(60, 0, 200, 400))

    assert config.data.hockey.lanes == []
    assert _said(lines, "ещё нет")
    window.close()


def test_the_defender_outline_is_marked_separately_from_the_helmet(
        tmp_path, monkeypatch, app):
    """Detection finds a helmet; a shot is blocked by the body under it. The
    two are different rectangles, and only the second one decides whether a
    puck gets past (2026-08-09)."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = []
    _mark_row(window, slot=0, top=180, height=40)      # row centre y=199

    window._target.set_active(window_module._TARGET_BODY)
    window._apply_calibration(QRect(100, 190, 46, 120))

    lane = config.data.hockey.lanes[0]
    assert (lane["body_w"], lane["body_h"]) == (46, 120)
    assert lane["body_dy"] == 190 - 199                # hangs off the helmet
    window.close()


def test_the_outline_follows_the_helmet_it_was_hung_off(
        tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = []
    _mark_row(window, slot=0, top=180, height=40)
    window._target.set_active(window_module._TARGET_BODY)
    window._apply_calibration(QRect(100, 190, 46, 120))

    lane = from_config(config.data.hockey).lanes[0]
    box = from_config(config.data.hockey).body_box(lane, x=300, y=250)

    assert box == (300 - 23, 250 - 9, 46, 120)
    window.close()


def test_an_unmarked_outline_still_dodges_something(tmp_path, monkeypatch, app):
    """A row nobody has measured must not read as a row nothing blocks."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]

    geom = from_config(config.data.hockey)
    left, top, width, height = geom.body_box(geom.lanes[0], x=300, y=200)

    assert width == 2 * config.data.hockey.goalie_half_w
    assert height == 56
    window.close()


def test_boards_for_the_outline_of_a_row_that_does_not_exist_say_so(
        tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = []
    lines = _capture_log(monkeypatch, window)

    window._target.set_active(window_module._TARGET_BODY)
    window._apply_calibration(QRect(100, 190, 46, 120))

    assert config.data.hockey.lanes == []
    assert _said(lines, "ещё нет")
    window.close()


def test_remarking_a_row_keeps_the_boards_it_already_had(
        tmp_path, monkeypatch, app):
    """Nudging a row's strip is not a statement about where its defender
    turns round."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = []
    _mark_row(window, slot=0, top=100)
    window._target.set_active(window_module._TARGET_WALLS)
    window._apply_calibration(QRect(60, 0, 200, 400))

    _mark_row(window, slot=0, top=105)

    assert config.data.hockey.lanes[0]["wall_left"] == 60
    assert config.data.hockey.lanes[0]["wall_right"] == 259
    window.close()


# ── The motion model ─────────────────────────────────────────────────────

def test_tracking_feeds_the_motion_model(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))

    window._toggle_running()
    for _ in range(5):
        window._scan_tick(window._geometry())

    report = window._motion.reports()[0]
    assert report.frames == 5
    assert report.seen_share == 1.0
    assert report.occupants == 1
    window._toggle_running()
    window.close()


def test_a_model_that_knows_nothing_yet_draws_nothing(
        tmp_path, monkeypatch, app):
    """No honest prediction has to read as no box at all — a box parked
    somewhere harmless would be a claim the model has not earned."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()

    window._scan_tick(window._geometry())

    assert window._predicted._boxes == []
    window._toggle_running()
    window.close()


def test_a_still_defender_is_predicted_where_it_stands(
        tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())

    for _ in range(60):        # enough to decide nobody here is moving
        window._scan_tick(window._geometry())

    report = window._motion.reports()[0]
    assert report.has_mover is False
    # Black, on the standers' layer — his box is a measurement, not a
    # prediction, and painting it the colour of "where the model thinks
    # somebody will have got to" would claim more than is being claimed.
    assert len(window._standing._boxes) == 1
    assert window._predicted._boxes == []
    window._toggle_running()
    window.close()


def test_a_watch_reports_its_own_accuracy_as_it_goes(
        tmp_path, monkeypatch, app):
    """Watching a ghost and waiting for the real box to walk into it cannot
    answer "and was that exactly 1.5 seconds?" by eye (2026-08-09). The
    model measures precisely that, so the number belongs in front of you
    rather than behind a button."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())
    lines = _capture_log(monkeypatch, window)

    for _ in range(400):        # twelve seconds of model time
        window._scan_tick(window._geometry())

    assert _said(lines, "Точность —")
    assert _said(lines, "ряд 1")
    window._toggle_running()
    window.close()


def test_the_moment_the_model_converges_is_announced_once(
        tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())
    lines = _capture_log(monkeypatch, window)

    for _ in range(600):
        window._scan_tick(window._geometry())

    assert sum("Все ряды откалиброваны" in line for line in lines) == 1
    window._toggle_running()
    window.close()


def test_an_empty_row_is_not_dressed_up_as_a_checked_one(
        tmp_path, monkeypatch, app):
    """"ошибка — по 0 проверкам, виден в 0% кадров, готов" reads like a row
    that was verified and passed, when it is a row nobody was ever seen
    in."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window)          # a blank frame — nobody there
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())
    for _ in range(60):
        window._scan_tick(window._geometry())
    lines = _capture_log(monkeypatch, window)

    window._model_report()

    assert any("никого не видно" in line for line in lines)
    assert not any("по 0 проверкам" in line for line in lines)
    window._toggle_running()
    window.close()


def test_the_report_says_no_shot_is_possible_without_measurements(
        tmp_path, monkeypatch, app):
    """A plan needs a measured trajectory as much as a verified model, and
    saying so beats printing nothing."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window)
    window._toggle_running()
    window._scan_tick(window._geometry())
    lines = _capture_log(monkeypatch, window)

    window._model_report()

    assert _said(lines, "Плана нет")
    window._toggle_running()
    window.close()


def test_the_report_needs_a_model_first(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    lines = _capture_log(monkeypatch, window)

    window._model_report()

    assert _said(lines, "Модель пуста")
    window.close()


def test_the_report_names_the_rows_that_cannot_be_trusted(
        tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    window._scan_tick(window._geometry())
    lines = _capture_log(monkeypatch, window)

    window._model_report()

    assert _said(lines, "Модель не готова")
    assert _said(lines, "не готов")
    window._toggle_running()
    window.close()


def test_the_report_notices_a_strip_drawn_round_the_whole_defender(
        tmp_path, monkeypatch, app):
    """Marked live on 2026-08-09: row 1 sat at y=616 while its helmets kept
    turning up at y=574. A strip centred on the body still catches the
    helmet most of the time, so it shows up as a row that keeps losing its
    defender rather than as anything obviously wrong."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 240, "height": 120}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    window._scan_tick(window._geometry())
    lines = _capture_log(monkeypatch, window)

    window._model_report()

    assert _said(lines, "полоса смещена")
    assert _said(lines, "y 200")           # where the helmets really are
    window._toggle_running()
    window.close()


def test_a_well_placed_strip_draws_no_complaint(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    window._scan_tick(window._geometry())
    lines = _capture_log(monkeypatch, window)

    window._model_report()

    assert not _said(lines, "полоса смещена")
    window._toggle_running()
    window.close()


def test_the_model_survives_the_watch_being_stopped(tmp_path, monkeypatch, app):
    """The report is most wanted right after a watch, not only during one."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    window._scan_tick(window._geometry())

    window._toggle_running()

    assert window._motion is not None
    assert window._predicted._boxes == []
    window.close()


# ── Firing for real ──────────────────────────────────────────────────────
# Every refusal here is the point of the module: one attempt per level means
# a shot on an unproven model does not cost a miss, it costs the level.

def test_nothing_is_fired_without_a_watch_running(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window)
    lines = _capture_log(monkeypatch, window)
    pressed = []
    monkeypatch.setattr(window_module, "mouse_down_at",
                        lambda *a: pressed.append(a))

    window._fire()

    assert pressed == []
    assert _said(lines, "запустите слежение")
    window.close()


def test_nothing_is_fired_while_the_prediction_is_being_checked(
        tmp_path, monkeypatch, app):
    """At a zero horizon the model's own accuracy check compares a
    prediction against the instant it was made for, which is true by
    construction — it would look perfect having proved nothing."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    config.data.hockey.predict_now = True
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())
    for _ in range(60):
        window._scan_tick(window._geometry())
    lines = _capture_log(monkeypatch, window)
    pressed = []
    monkeypatch.setattr(window_module, "mouse_down_at",
                        lambda *a: pressed.append(a))

    window._fire()

    assert pressed == []
    assert _said(lines, "режим проверки")
    # The model still measures itself over a real flight, so the accuracy
    # figures stay honest while the boxes are drawn for now.
    assert window._motion.horizon == window_module._PUCK_TRAVEL_S
    window._toggle_running()
    window.close()


def test_the_checking_switch_takes_effect_at_once(tmp_path, monkeypatch, app):
    """A setting that needs a restart to show anything is a setting that
    looks broken — and did (2026-08-10)."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())
    for _ in range(60):
        window._scan_tick(window._geometry())
    assert window._checking() is False

    config.data.hockey.predict_now = True         # flipped mid-watch
    window._scan_tick(window._geometry())

    assert window._checking() is True
    # The helmet sits at x=92; the box is the whole defender around it. This
    # one never moved, so it is on the standers' layer.
    box = window._standing._boxes[0]
    assert abs(box.target.center().x() - 92) < 4
    window._toggle_running()
    window.close()


def test_nothing_is_fired_before_the_model_has_converged(
        tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    window._scan_tick(window._geometry())      # far too little to be ready
    lines = _capture_log(monkeypatch, window)
    pressed = []
    monkeypatch.setattr(window_module, "mouse_down_at",
                        lambda *a: pressed.append(a))

    window._fire()

    assert pressed == []
    assert _said(lines, "модель ещё не сошлась")
    window._toggle_running()
    window.close()


def test_nothing_is_fired_without_a_measured_trajectory(
        tmp_path, monkeypatch, app):
    """A verified model still says nothing about where the puck will be."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())
    for _ in range(60):
        window._scan_tick(window._geometry())
    assert window._motion.ready()
    lines = _capture_log(monkeypatch, window)
    pressed = []
    monkeypatch.setattr(window_module, "mouse_down_at",
                        lambda *a: pressed.append(a))

    window._fire()

    assert pressed == []
    assert _said(lines, "ни один прицел не промерен")
    window._toggle_running()
    window.close()


def test_the_forced_shot_needs_the_same_groundwork(tmp_path, monkeypatch, app):
    """It skips the thresholds, not the preconditions. Firing without a
    converged model is not a bolder shot, it is a random one."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window)
    lines = _capture_log(monkeypatch, window)
    pressed = []
    monkeypatch.setattr(window_module, "mouse_down_at",
                        lambda *a: pressed.append(a))

    window._force()

    assert pressed == []
    assert _said(lines, "запустите слежение")
    window.close()


# ── Detection test button ────────────────────────────────────────────────

def _burst(window, frames: int = 3):
    """Run "Тест детекции" to the end without an event loop."""
    window._test_detect()
    for _ in range(frames - 1):
        window._burst_tick()
    window._finish_burst()


def test_the_detection_test_reports_one_line_per_defender(
        tmp_path, monkeypatch, app):
    """One line per row on the first frame, and nothing else per frame.
    Timings, rejected candidates and the mask's warm-up all used to go here
    and crowded out the only thing the button exists to answer
    (2026-08-09) — and a run of a hundred frames would bury it completely."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56},
                                {"y": 340, "height": 60}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    lines = _capture_log(monkeypatch, window)

    _burst(window)

    assert lines[1:3] == ["Ряд 1 — x 92  y 200  площадь 576  фото 0%",
                          "Ряд 2 — пусто"]
    window.close()


def test_the_detection_run_tallies_how_often_each_row_was_found(
        tmp_path, monkeypatch, app):
    """The whole reason it records more than one frame: a row found in two
    frames out of three looks perfect in any single snapshot and starves the
    tracker."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56},
                                {"y": 340, "height": 60}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    lines = _capture_log(monkeypatch, window)

    _burst(window, frames=4)

    assert _said(lines, "Снято 4 кадров")
    assert _said(lines, "ряд 1 1.00")      # found in every one of them
    assert _said(lines, "ряд 2 0.00")
    window.close()


def test_with_no_rows_the_test_falls_back_to_the_whole_rink(
        tmp_path, monkeypatch, app):
    """Otherwise the one button that could tell you the detector works at
    all would report nothing whenever rows are what is misconfigured."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = []
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    lines = _capture_log(monkeypatch, window)

    _burst(window)

    assert _said(lines, "по всему катку")
    window.close()


def test_the_detection_test_leaves_its_pictures_and_nothing_on_screen(
        tmp_path, monkeypatch, app):
    """A box drawn over the game stays where it was put while the defender
    moves on, which reads as a detector that has lost somebody
    (2026-08-09). The judged frames are the record instead, and they cannot
    go stale.

    A folder of their own, numbered in capture order: a hundred frames are
    only worth anything read together, and against the run before them.
    """
    import cv2

    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [
        {"y": 200, "height": 56, "body_w": 80, "body_h": 120, "body_dy": -20}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))

    _burst(window, frames=3)

    assert window._predicted._boxes == []
    runs = list((tmp_path / "debug_frames").glob("detect_*"))
    assert len(runs) == 1
    shots = sorted(runs[0].glob("*.png"))
    assert [path.name for path in shots] == ["0000.png", "0001.png",
                                             "0002.png"]
    saved = cv2.imread(str(shots[0]))
    assert saved.shape == (400, 400, 3)
    assert (saved[:, :, 2] > 200).any()     # red round the helmet

    # And the same frames untouched, because the boxes are drawn in the very
    # colour the detector reads: an annotated frame cannot be scanned again.
    raws = sorted((runs[0] / "raw").glob("*.png"))
    assert [path.name for path in raws] == [path.name for path in shots]
    assert (cv2.imread(str(raws[0])) == _frame_with_helmet(80, 188)).all()
    window.close()


def test_the_picture_marks_the_helmet_and_the_whole_defender(
        tmp_path, monkeypatch, app):
    """Two boxes, not one: the helmet says whether detection works, the
    outline says what the planner will be dodging, and a calibration that
    got one right and the other wrong looks fine until a puck goes through
    a shoulder."""
    from modules.hockey import debug_frame as df
    from modules.hockey.detect import HelmetDetector

    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [
        {"y": 200, "height": 56, "body_w": 80, "body_h": 120, "body_dy": -20}]
    geom = window._geometry()
    # Grey, not black: a black outline on a black frame proves nothing.
    frame = np.full((400, 400, 3), 128, np.uint8)
    frame[188:212, 80:104] = RED
    hits = HelmetDetector(geom, config.data.hockey).scan(frame)[0]
    assert hits

    drawn = df.annotate(frame, geom, hits)

    # The helmet spans x 80..103; the 80px outline is centred on it, so it
    # reaches out to about x 52 — well left of anything the helmet touches.
    black = np.argwhere((drawn == 0).all(axis=2))
    assert black.size
    assert black[:, 1].min() < 70
    # And the outline hangs below the helmet, down to y 180 + 120.
    assert black[:, 0].max() > 250
    window.close()


def test_a_row_with_nobody_in_it_says_so(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    config.data.hockey.player_match_min = 0.9   # nothing painted will pass
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    lines = _capture_log(monkeypatch, window)

    _burst(window)

    assert _said(lines, "Ряд 1 — пусто")
    window.close()


# ── Overlays ─────────────────────────────────────────────────────────────

def test_only_the_chosen_row_is_drawn(tmp_path, monkeypatch, app):
    """Strips are taller than the gaps between rows, so all of them at once
    is a stack of overlapping rectangles nobody can read."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 100, "height": 40},
                                {"y": 200, "height": 40},
                                {"y": 300, "height": 40}]
    window._row_slot.set_active(1)

    window._toggle_row_view()

    # One strip, two boards, and the defender's outline against each board.
    assert len(window._zones._boxes) == 5
    strip = max(window._zones._boxes, key=lambda pair: pair[0].width())[0]
    assert strip.height() == 40

    window._toggle_row_view()
    assert window._zones._boxes == []
    window.close()


def test_switching_the_row_number_redraws_it(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [
        {"y": 100, "height": 40, "wall_left": 60, "wall_right": 260},
        {"y": 200, "height": 40, "wall_left": 120, "wall_right": 340}]
    window._toggle_row_view()

    window._row_slot._select(1)     # as a click on "2" would

    marks = [box for box, _c in window._zones._boxes if box.width() == 4]
    assert max(b.left() for b in marks) - min(b.left() for b in marks) == 220
    window.close()


def test_showing_a_row_reports_its_numbers(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [
        {"y": 200, "height": 40, "wall_left": 60, "wall_right": 260}]
    lines = _capture_log(monkeypatch, window)

    window._toggle_row_view()

    assert _said(lines, "Ряд 1 —")
    assert _said(lines, "y 200")
    assert _said(lines, "60…260")
    window.close()


def test_a_row_that_was_never_marked_says_so(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 100, "height": 40}]
    lines = _capture_log(monkeypatch, window)
    window._row_slot.set_active(3)

    window._toggle_row_view()

    assert window._zones._boxes == []
    assert _said(lines, "Ряда 4 нет")
    window.close()


def test_the_field_overlay_toggles(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)

    window._toggle_field()
    assert window._field_shown is True
    assert len(window._zones._boxes) == 3   # rink, goal, puck — boards are
                                            # per row, not per rink

    window._toggle_field()
    assert window._field_shown is False
    assert window._zones._boxes == []
    window.close()


def test_field_and_row_can_be_shown_at_once(tmp_path, monkeypatch, app):
    """One overlay carries both layers, so switching either does not wipe
    the other."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 100, "height": 60},
                                {"y": 200, "height": 60}]

    window._toggle_field()
    window._toggle_row_view()

    # 3 for the field, then the chosen row's strip, boards and outlines.
    assert len(window._zones._boxes) == 3 + 5
    window.close()


def test_a_rows_boards_stop_with_its_own_strip(tmp_path, monkeypatch, app):
    """Boards belong to a row, not to the rink — one pair running the full
    height was a picture of something that is not true (2026-08-09)."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [
        {"y": 100, "height": 40, "wall_left": 60, "wall_right": 260}]

    window._toggle_row_view()

    marks = [box for box, _colour in window._zones._boxes if box.width() == 4]
    assert len(marks) == 2
    assert {box.height() for box in marks} == {40}
    assert max(b.left() for b in marks) - min(b.left() for b in marks) == 200
    window.close()


# ── Settings ─────────────────────────────────────────────────────────────

def test_clearing_the_rows_empties_them_and_stops_tracking(
        tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 100, "height": 60}]
    _with_game(monkeypatch, window)
    window._toggle_running()
    window._toggle_settings()

    window._settings._on_clear_rows()

    assert config.data.hockey.lanes == []
    assert window._running is False
    assert ConfigManager().data.hockey.lanes == []
    window.close()


def test_the_sensitivity_control_writes_both_thresholds(
        tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    window._toggle_settings()

    window._settings._on_sensitivity(2)   # "Мягко"

    assert config.data.hockey.red_sat_min == 80
    assert config.data.hockey.red_val_min == 55
    window.close()


def test_the_sensitivity_control_opens_on_whatever_was_stored(
        tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.red_sat_min = 160

    window._toggle_settings()

    assert window._settings._sensitivity.active() == 0   # "Строго"
    window.close()


def test_a_watch_counts_heads_before_it_calibrates_anything(
        tmp_path, monkeypatch, app):
    """A run spends its time proving what is in each row, so what is in each
    row belongs in front of you before that starts — and it is a fact the
    watch then holds on to rather than rediscovers every five seconds."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56},
                                {"y": 340, "height": 60}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())
    lines = _capture_log(monkeypatch, window)

    for _ in range(400):
        window._scan_tick(window._geometry())

    assert _said(lines, "Ряд 1 — найден 1 вратарь, стоит")
    assert _said(lines, "Ряд 2 — пусто")
    assert _said(lines, "Состав посчитан")
    # And the accuracy line only starts after the count, not before it.
    counted = next(i for i, line in enumerate(lines) if "Состав" in line)
    accuracy = next(i for i, line in enumerate(lines) if "Точность" in line)
    assert accuracy > counted
    window._toggle_running()
    window.close()
