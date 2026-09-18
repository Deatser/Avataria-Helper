# app/core/tropikania_farm.py
""""Запустить фарм опыта" run: market → blueberry → plant → close → search →
sell → confirm, each step strictly after the previous one, once through —
not a loop (yet).

Same shape as promo_activate's own steps (QThread, stop_event, poll-and-
click), and the same reasoning behind PLANT_AREA as Gardener's own
FIXED_AREA (see the gardener-fixed-area memory): template-locating the
planting spot drifted, a manually-measured, read-back box did not — so the
spot the user dragged out with the calibration tool on 2026-08-05
(x=1103, y=689, w=102, h=53) is hardcoded rather than searched for.
"""
from __future__ import annotations
import threading

import cv2
from PySide6.QtCore import QThread, Signal

from app.core.capture import grab_window
from app.core.input_sender import click_at
from app.core.template_match import best_match, load_template, primary_monitor_region

SEARCH_INTERVAL = 1.0
# Held after every click before the next step starts polling — same reason
# as promo_activate's own CLICK_SETTLE_S: the very next poll landing within
# milliseconds of a click can still see the pre-click screen, and a stray
# match there fires a second, unwanted click that the game reads as a
# double-click on what the first one just opened.
CLICK_SETTLE_S  = 0.8
MATCH_THRESHOLD = 0.85
# tropi_seed.png's own bar, lower than the button templates' — see the
# debug button's real readings: the sprouted seedling scores much lower
# than a fixed piece of UI chrome does (it sits on shifting grass/soil,
# not a flat background), so the shared 85% bar never tripped for it.
SEED_MATCH_THRESHOLD = 0.40

MARKET_TPL    = "Market.png"
BLUEBERRY_TPL = "blueberry10.png"
SEED_TPL      = "tropi_seed.png"   # sprouted seedling — confirms the plant landed
CLOSE_TPL     = "tropi_close.png"
SEARCH_TPL    = "tropi_search.png"
SELL_TPL      = "tropi_sell.png"
CONFIRM_TPL   = "tropi_confirm.png"

# left, top, width, height — screen coordinates, see module docstring.
PLANT_AREA = (1103, 689, 102, 53)
# Margin around PLANT_AREA when checking for the sprouted seedling — the
# planting animation can settle a few pixels off from the exact click spot,
# same reasoning as trash.py's own CHECK_PAD.
SEED_CHECK_PAD = 30


def _grab_gray(hwnd: int, region: dict):
    return cv2.cvtColor(grab_window(hwnd, region), cv2.COLOR_BGR2GRAY)


def _wait_for_and_click(hwnd: int, filename: str, stop_event: threading.Event,
                        on_error) -> bool:
    """Polls once a second, forever, until `filename` is on screen, then
    clicks its centre once and settles — a single click_at, never a double:
    the click itself posts one WM_LBUTTONDOWN/UP pair, nothing here calls it
    twice for the same match, and the settle wait keeps the next step's
    first poll from landing on the pre-click screen and firing again."""
    template = load_template(filename)
    if template is None:
        on_error(f"Не найден шаблон: {filename}")
        return False

    region = primary_monitor_region()
    h, w = template.shape[:2]
    while not stop_event.is_set():
        gray = _grab_gray(hwnd, region)
        score, (x, y) = best_match(gray, template)
        if score >= MATCH_THRESHOLD:
            click_at(hwnd, x + w // 2, y + h // 2)
            stop_event.wait(CLICK_SETTLE_S)
            return True
        stop_event.wait(SEARCH_INTERVAL)
    return False


def _wait_for_seed(hwnd: int, stop_event: threading.Event, on_error) -> bool:
    """Polls once a second, forever, until the sprouted-seedling template
    (SEED_TPL) shows up inside PLANT_AREA — planting sometimes takes longer
    than usual to actually land, and moving on to tropi_close before it has
    is exactly what this is here to stop. Only the padded PLANT_AREA box is
    searched, not the whole screen: cheaper, and it cannot be confused by a
    seedling appearing anywhere else."""
    template = load_template(SEED_TPL)
    if template is None:
        on_error(f"Не найден шаблон: {SEED_TPL}")
        return False

    left, top, w, h = PLANT_AREA
    region = dict(left=left - SEED_CHECK_PAD, top=top - SEED_CHECK_PAD,
                 width=w + 2 * SEED_CHECK_PAD, height=h + 2 * SEED_CHECK_PAD)
    while not stop_event.is_set():
        gray = _grab_gray(hwnd, region)
        score, _ = best_match(gray, template)
        if score >= SEED_MATCH_THRESHOLD:
            return True
        stop_event.wait(SEARCH_INTERVAL)
    return False


class TropikaniaFarmLoop(QThread):
    """One farm cycle, start to finish: market → blueberry10 → plant →
    tropi_close → tropi_search → tropi_sell → tropi_confirm, each step
    strictly after the one before it.

    A confirmed cycle immediately starts the next one from MARKET_TPL
    again — until either stop_loop() is called (a manual "Завершить"
    press) or, with a numeric target_exp, until enough cycles have
    confirmed to reach it, at which point target_reached fires and the
    thread ends on its own. target_exp=None means no target — loop forever
    until manually stopped, same as the old "Автопродолжение" toggle's on
    state.
    """

    market_clicked    = Signal()
    blueberry_clicked = Signal()
    planted           = Signal(int, int)   # centre of PLANT_AREA, screen coords
    sprouted          = Signal()           # tropi_seed.png confirmed in PLANT_AREA
    closed            = Signal()
    searched          = Signal()
    sold              = Signal()
    replanted         = Signal(int, int)   # centre of PLANT_AREA again, after selling
    confirmed         = Signal()
    target_reached    = Signal(int)        # earned — the target was met, not a manual stop
    error             = Signal(str)

    def __init__(self, get_hwnd, target_exp: int | None = None):
        super().__init__()
        self._get_hwnd = get_hwnd
        self.target_exp = target_exp
        self._stop_event = threading.Event()

    def stop_loop(self):
        self._stop_event.set()

    def run(self):
        self._stop_event.clear()
        earned = 0
        while True:
            if not self._run_cycle():
                return
            earned += 1
            if self._stop_event.is_set():
                return
            if self.target_exp is not None and earned >= self.target_exp:
                self.target_reached.emit(earned)
                return
            # A brief gap before the next cycle starts polling — same
            # reasoning as CLICK_SETTLE_S: the game needs a moment after
            # tropi_confirm before Market.png is worth searching for again.
            self._stop_event.wait(CLICK_SETTLE_S)

    def _run_cycle(self) -> bool:
        """One full market→confirm pass. True if it reached tropi_confirm,
        False on any failure or stop (already reported/logged upstream)."""
        hwnd = self._get_hwnd()
        if not hwnd:
            self.error.emit("Игровое окно не найдено")
            return False

        if not _wait_for_and_click(hwnd, MARKET_TPL, self._stop_event, self.error.emit):
            return False
        self.market_clicked.emit()
        if self._stop_event.is_set():
            return False

        if not _wait_for_and_click(hwnd, BLUEBERRY_TPL, self._stop_event, self.error.emit):
            return False
        self.blueberry_clicked.emit()
        if self._stop_event.is_set():
            return False

        left, top, w, h = PLANT_AREA
        cx, cy = left + w // 2, top + h // 2
        click_at(hwnd, cx, cy)
        self.planted.emit(cx, cy)
        self._stop_event.wait(CLICK_SETTLE_S)
        if self._stop_event.is_set():
            return False

        if not _wait_for_seed(hwnd, self._stop_event, self.error.emit):
            return False
        self.sprouted.emit()
        if self._stop_event.is_set():
            return False

        if not _wait_for_and_click(hwnd, CLOSE_TPL, self._stop_event, self.error.emit):
            return False
        self.closed.emit()
        if self._stop_event.is_set():
            return False

        if not _wait_for_and_click(hwnd, SEARCH_TPL, self._stop_event, self.error.emit):
            return False
        self.searched.emit()
        if self._stop_event.is_set():
            return False

        if not _wait_for_and_click(hwnd, SELL_TPL, self._stop_event, self.error.emit):
            return False
        self.sold.emit()
        if self._stop_event.is_set():
            return False

        click_at(hwnd, cx, cy)
        self.replanted.emit(cx, cy)
        self._stop_event.wait(CLICK_SETTLE_S)
        if self._stop_event.is_set():
            return False

        if not _wait_for_and_click(hwnd, CONFIRM_TPL, self._stop_event, self.error.emit):
            return False
        self.confirmed.emit()
        return True
