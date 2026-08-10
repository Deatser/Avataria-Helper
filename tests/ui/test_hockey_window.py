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
                      "⚠  Сделать принудительный бросок",   # hidden
                      "🏅  Отметить ячейку 1/9",            # hidden
                      "🏅  Проверить уровни",               # hidden
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


# ── The level counter ────────────────────────────────────────────────────

def test_marking_the_level_cells_walks_through_all_nine(
        tmp_path, monkeypatch, app):
    """Nine cells, one press each way, and the button says which is next."""
    window, config = _window(tmp_path, monkeypatch)
    _with_game(monkeypatch, window)
    lines = _capture_log(monkeypatch, window)

    for slot in range(9):
        assert window._level_btn.text().endswith(f"{slot + 1}/9")
        window._toggle_level_calib()                 # box appears
        window._calib.show_at(QRect(100 + slot * 30, 40, 24, 24))
        window._toggle_level_calib()                 # and is written

    assert len(config.data.hockey.level_cells) == 9
    assert config.data.hockey.level_cells[0] == {
        "left": 100, "top": 40, "width": 24, "height": 24}
    assert _said(lines, "Ячейка 9 —")
    assert window._level_btn.text().endswith("1/9")   # wrapped round
    window.close()


def test_the_level_is_unknown_until_the_strip_is_marked(
        tmp_path, monkeypatch, app):
    """A number nobody has read is not a number — no chip is lit rather than
    level 1 being claimed."""
    window, _config = _window(tmp_path, monkeypatch)

    window._refresh_level()

    assert window._levels.level is None
    window.close()


def _with_strip(monkeypatch, window, states: list):
    """The nine cells marked, and reading them gives `states`."""
    window.config.level_cells = [
        {"left": 10 + i * 30, "top": 10, "width": 24, "height": 24}
        for i in range(9)]
    _with_game(monkeypatch, window)
    monkeypatch.setattr(window, "_read_cells", lambda cells: list(states))


def test_the_level_is_the_first_cell_still_empty(tmp_path, monkeypatch, app):
    """Nine cells hold levels 1 to 9; the one being played is the first that
    carries neither a tick nor a cross."""
    window, _config = _window(tmp_path, monkeypatch)
    _with_strip(monkeypatch, window,
                ["nice", "bad", "nice"] + [None] * 6)

    window._refresh_level()

    assert window._levels.level == 4


def test_the_strip_keeps_how_every_played_level_went(
        tmp_path, monkeypatch, app):
    """All ten numbers are on screen from the start, so it reads as a route
    rather than a counter: the ones behind carry their verdict, the one being
    played is lit, the rest wait."""
    from modules.hockey.level_strip import _look

    window, _config = _window(tmp_path, monkeypatch)
    _with_strip(monkeypatch, window,
                ["nice", "bad", "nice"] + [None] * 6)

    window._refresh_level()

    assert window._levels.level == 4
    assert [_look(s, False) for s in window._levels._states[:3]] == [
        (theme.ACCENT_GREEN, "tick"),
        (theme.ACCENT_RED, "cross"),
        (theme.ACCENT_GREEN, "tick")]
    assert _look(None, True) == (theme.HK_ICE, "dot")    # the one in progress
    assert _look(None, False) == (theme.TEXT_DIM, "")    # and the ones ahead


def test_a_full_strip_reads_as_the_last_level(tmp_path, monkeypatch, app):
    window, _config = _window(tmp_path, monkeypatch)
    _with_strip(monkeypatch, window, ["nice"] * 9)

    window._refresh_level()

    assert window._levels.level == 10


def test_a_cell_filling_is_announced_once(tmp_path, monkeypatch, app):
    """The cell filling is the only thing on screen that says a level
    ended — so it is worth a line, and exactly one."""
    window, _config = _window(tmp_path, monkeypatch)
    _with_strip(monkeypatch, window, ["nice"] + [None] * 8)
    for _ in range(3):
        window._refresh_level()
    lines = _capture_log(monkeypatch, window)

    _with_strip(monkeypatch, window, ["nice", "bad"] + [None] * 7)
    for _ in range(4):
        window._refresh_level()

    assert sum("Уровень 2 провален" in line for line in lines) == 1
    assert _said(lines, "идёт 3-й из 10")


def test_the_level_check_says_what_every_cell_holds(
        tmp_path, monkeypatch, app):
    """The counter shows one number and cannot say why it is that number.
    This says it for all ten, with the scores it decided on."""
    window, _config = _window(tmp_path, monkeypatch)
    _with_strip(monkeypatch, window, ["nice", "bad"] + [None] * 7)
    monkeypatch.setattr(
        window, "_cell_detail",
        lambda cells: [("nice", {"nice": 0.9, "bad": 0.1}),
                       ("bad", {"nice": 0.1, "bad": 0.9})]
        + [(None, {"nice": 0.2, "bad": 0.2})] * 7)
    lines = _capture_log(monkeypatch, window)

    window._check_levels()

    assert _said(lines, "Уровень 1 — Пройден")
    assert _said(lines, "Уровень 2 — Не пройден")
    assert _said(lines, "Уровень 3 — Текущий")
    assert _said(lines, "Уровень 10 — ")
    assert _said(lines, "порог")             # the numbers behind the verdict
    window.close()


def test_an_icon_bigger_than_its_cell_is_still_matched(
        tmp_path, monkeypatch, app):
    """The icons are 43x43 and 41x39, the cells were marked 40x40, and a
    template bigger than what it is searched in scores zero — which is not
    "no match" but "could not look". Every cell read empty and the counter
    sat on level 1 for ever (2026-08-10)."""
    from app.core.template_match import TEMPLATES_DIR
    import cv2 as _cv2

    window, _config = _window(tmp_path, monkeypatch)
    icon = _cv2.imread(str(TEMPLATES_DIR / "hockey_nice.png"),
                       _cv2.IMREAD_GRAYSCALE)
    assert icon is not None
    # A patch the size of a marked cell, smaller than the icon itself.
    patch = _cv2.resize(icon, (40, 40), interpolation=_cv2.INTER_AREA)

    scores = window._cell_scores(patch)

    assert scores["nice"] > 0.55
    assert scores["nice"] > scores["bad"]
    window.close()


def test_a_shot_short_of_room_goes_out_anyway_and_says_by_how_much(
        tmp_path, monkeypatch, app):
    """Declining costs the attempt just the same — a level ends whether or
    not a shot is taken — and a few pixels of overlap against a body
    outline drawn by hand is not a miss anybody can be sure of. So the shot
    goes out and the shortfall goes in the log, all three lines of it.
    """
    from modules.hockey.planner import Plan

    window, _config = _window(tmp_path, monkeypatch)
    lines = _capture_log(monkeypatch, window)
    pressed = []
    monkeypatch.setattr(window, "_shot_setup", lambda: (1, object(), {}))
    monkeypatch.setattr(window, "_pull_and_hold",
                        lambda hwnd, geom, found: pressed.append(found))
    monkeypatch.setattr(window_module, "plan_shot",
                        lambda *a, **kw: None)      # no clean window at all
    monkeypatch.setattr(
        window_module, "best_effort_each",
        lambda geom, motion, timings, now, lead=0.0: {
            -0.5: Plan(-0.5, now + 2.0, clearance=-12.0, window=0.0),
            0.0:  Plan(0.0,  now + 2.0, clearance=-3.0,  window=0.0),
            0.5:  Plan(0.5,  now + 2.0, clearance=-21.0, window=0.0)})

    window._fire()

    assert len(pressed) == 1
    assert pressed[0].aim == 0.0                    # the least-bad line
    assert _said(lines, "не хватает пикселей")
    assert _said(lines, "лево 12 px")
    assert _said(lines, "центр 3 px")
    assert _said(lines, "право 21 px")
    assert _said(lines, "выбираю центр ворот")
    window.close()


def test_a_shot_is_never_planned_for_less_than_a_second_away(
        tmp_path, monkeypatch, app):
    """A third of a second leaves nothing between the plan and the release
    for anything to run slightly slower than it did while the plan was being
    drawn up. Waiting costs nothing — the next window is as good."""
    from modules.hockey.planner import Plan

    window, _config = _window(tmp_path, monkeypatch)
    lines = _capture_log(monkeypatch, window)
    pressed = []
    monkeypatch.setattr(window, "_shot_setup", lambda: (1, object(), {}))
    monkeypatch.setattr(window, "_pull_and_hold",
                        lambda hwnd, geom, found: pressed.append(found))
    leads = []

    def too_soon(geom, motion, timings, now, lead=0.0, **kw):
        leads.append(lead)
        return Plan(0.0, now + 0.2, clearance=40.0, window=0.5)

    monkeypatch.setattr(window_module, "plan_shot", too_soon)
    monkeypatch.setattr(window_module, "best_effort_each",
                        lambda *a, **kw: {})

    window._fire()

    assert pressed == []                      # nothing fired that close
    assert len(leads) == 3                    # it did try again, further out
    # Every attempt asks for at least the minimum, plus whatever the last
    # plan cost to draw up — here nothing, since the planner is a stub.
    assert all(lead >= window_module._MIN_DELAY_S for lead in leads)
    assert _said(lines, "Не успеваю")
    window.close()


def test_a_new_level_throws_the_old_defenders_away(tmp_path, monkeypatch, app):
    """Speeds are drawn afresh every level — a row that patrolled slowly
    last time can be the quickest thing on the ice this time — so a period
    carried across is not an old measurement but a wrong one. The latch that
    makes a row's verdict permanent is sound only inside one level, and the
    cell filling is what releases it."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())
    for _ in range(400):
        window._scan_tick(window._geometry())
    assert window._motion.ready() is True
    assert window._counted is True
    lines = _capture_log(monkeypatch, window)

    _with_strip(monkeypatch, window, ["nice"] + [None] * 8)
    for _ in range(3):                        # level 1 done, level 2 begins
        window._refresh_level()
    _with_strip(monkeypatch, window, ["nice", "bad"] + [None] * 7)
    for _ in range(3):
        window._refresh_level()

    # The watch goes down and comes back up, exactly as a hand would do it.
    assert _said(lines, "Слежение запущено")
    assert window._running is True
    assert window._counted is False           # the head count starts over
    assert window._motion.ready() is False    # and nothing is trusted
    window._toggle_running()
    window.close()


# ── The light log ────────────────────────────────────────────────────────

def test_the_light_log_keeps_only_what_happened(tmp_path, monkeypatch, app):
    """The level, who is in each row, one line per row as it comes good, and
    the shot. Everything else still happens — it just stops being printed."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    config.data.hockey.light_log = True
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    lines = _capture_log(monkeypatch, window)
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())

    for _ in range(400):
        window._scan_tick(window._geometry())

    assert _said(lines, "Ряд 1 — найден 1 вратарь")
    assert _said(lines, "Идёт калибровка рядов")
    assert _said(lines, "Ряд 1 откалиброван")
    assert _said(lines, "Все ряды откалиброваны")
    assert any("─" in line for line in lines)          # the dividers
    # And none of the running commentary.
    assert not _said(lines, "Точность —")
    assert not _said(lines, "Малиновые рамки")
    assert not _said(lines, "Слежение запущено")
    window._toggle_running()
    window.close()


def test_and_the_full_log_still_says_everything(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    config.data.hockey.light_log = False
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    lines = _capture_log(monkeypatch, window)
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())

    for _ in range(400):
        window._scan_tick(window._geometry())

    assert _said(lines, "Слежение запущено")
    assert _said(lines, "Точность —")
    assert not any("─" * 10 in line for line in lines)
    window._toggle_running()
    window.close()


def test_hiding_the_overlays_leaves_the_plan_alone(tmp_path, monkeypatch, app):
    """They are a way of showing the plan, not part of making it."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    config.data.hockey.hide_overlays = True
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())

    for _ in range(120):
        window._scan_tick(window._geometry())

    assert window._predicted._boxes == []
    assert window._standing._boxes == []
    # ...and the model is no worse off for it.
    assert window._motion.reports()[0].ready is True
    window._toggle_running()
    window.close()


def test_no_window_still_names_the_nearest_line(tmp_path, monkeypatch, app):
    """"No window" on its own is unactionable. The nearest line there is,
    and what it would clip, says whether this is a rink full of defenders or
    an outline marked a few pixels too wide."""
    from modules.hockey.planner import Plan

    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.light_log = True
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    class _Track:
        aim = 0.0

    monkeypatch.setattr(window_module.trajectory, "load", lambda: [_Track()])
    monkeypatch.setattr(window_module.trajectory, "averaged",
                        lambda geom, tracks, aim: ["row"])
    monkeypatch.setattr(window_module.trajectory, "mirror_fill",
                        lambda geom, timings: [])
    monkeypatch.setattr(window_module, "plan_shot", lambda *a, **kw: None)
    monkeypatch.setattr(
        window_module, "best_effort_shot",
        lambda *a, **kw: Plan(-0.35, 0.0, clearance=-8.0, window=0.0))
    lines = _capture_log(monkeypatch, window)

    window._log_plan()

    assert _said(lines, "Чистого окна нет")
    assert _said(lines, "натяжение 0.35 влево")
    assert _said(lines, "не хватает 8 px")
    window.close()


def test_the_auto_shot_fires_itself_once_the_model_is_ready(
        tmp_path, monkeypatch, app):
    """Exactly the shot the button would take — same plan, same thresholds,
    same refusals. Once per level: the game allows one attempt."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    config.data.hockey.auto_shot = True
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    fired = []
    monkeypatch.setattr(window, "_fire", lambda: fired.append(1))
    lines = _capture_log(monkeypatch, window)
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())

    for _ in range(500):
        window._scan_tick(window._geometry())

    assert fired == [1]                      # and not once per status line
    assert _said(lines, "Автоудар")
    window._toggle_running()
    window.close()


def test_and_waits_for_the_button_when_it_is_off(tmp_path, monkeypatch, app):
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    config.data.hockey.auto_shot = False
    _with_game(monkeypatch, window, frame=_frame_with_helmet(left=80, top=188))
    fired = []
    monkeypatch.setattr(window, "_fire", lambda: fired.append(1))
    window._toggle_running()
    monkeypatch.setattr(window_module, "time", _Clock())

    for _ in range(500):
        window._scan_tick(window._geometry())

    assert window._motion.ready() is True
    assert fired == []
    window._toggle_running()
    window.close()


def test_a_row_that_will_not_settle_is_settled_for_after_a_minute(
        tmp_path, monkeypatch, app):
    """A level ends whether or not a shot is taken, so a row still arguing
    with itself has cost the attempt as surely as a miss. The bar comes down
    rather than the answer being invented."""
    window, config = _window(tmp_path, monkeypatch)
    config.data.hockey.lanes = [{"y": 200, "height": 56}]
    _with_game(monkeypatch, window)
    window._toggle_running()
    clock = _Clock()
    monkeypatch.setattr(window_module, "time", clock)
    monkeypatch.setattr(window._motion, "ready", lambda: False)
    settled = []
    monkeypatch.setattr(window._motion, "settle_for_now",
                        lambda: settled.append(1) or [0])
    lines = _capture_log(monkeypatch, window)

    for _ in range(300):                      # nine seconds of model time
        window._scan_tick(window._geometry())
    assert settled == []

    clock.t += 60
    window._scan_tick(window._geometry())

    assert settled == [1]
    assert _said(lines, "Минута вышла")
    window._toggle_running()
    window.close()
