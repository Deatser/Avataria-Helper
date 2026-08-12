# modules/ava_dancers/exit_flow.py
from __future__ import annotations

import time

from app.core.input_sender import click_at
from app.core.template_match import (best_match, load_template,
                                     primary_monitor_region)
from modules.ava_dancers.click_flow import DEFAULT_CLICK_LIMIT, ClickFlow
from modules.ava_dancers.game_over_watch import (GAMEOVER_TEMPLATE,
                                                 MATCH_THRESHOLD)

# The screens that follow a finished round, in the order they appear.
START_LABEL = "Кнопка НАЧАТЬ"
RESTART_STEPS = [
    ("Кнопка ЗАНОВО", "leave_repeat.png"),
    (START_LABEL,     "leave_start.png"),
]

# НАЧАТЬ — то место, где подвисшая игра видна лучше всего: кнопка нарисована,
# нажатие проходит, а лобби не открывается. Шесть нажатий подряд без реакции —
# это уже не медленный экран (2026-08-12).
START_CLICK_LIMITS = {START_LABEL: DEFAULT_CLICK_LIMIT}

# Still how EntryFlow recognises the results screen when the bot is started
# on one, which is a different job: there it has to work out where it is
# from what is drawn, with no banner to lean on. Only the exit path below
# stopped matching the button and started clicking its known spot.
OK_STEP = ("Кнопка ОК", "leave_ok.png")

# ── The OK button ────────────────────────────────────────────────────────
# Not found by template like the rest. leave_ok.png matched only some of the
# time, and a round that missed it simply never started the next one — the
# whole chain hangs off this one click.
#
# Its position does not move, so it is clicked where it is. What is checked
# instead is the "ИГРА ОКОНЧЕНА" banner: that is the screen the button lives
# on, and seeing it is what makes the button certainly there. Waiting on the
# banner rather than on the button turns "is this exact artwork on screen"
# into "are we on the right screen", which is the question that actually
# matters.
# One banner, one crop, one bar: the same ones GameOverWatch uses. Two
# templates of the same artwork with two different thresholds lived here
# until 2026-08-12 and only made it possible for one half of the chain to
# see the screen the other half was already sure of.
OK_LABEL    = "Кнопка ОК"
OK_BANNER   = GAMEOVER_TEMPLATE
OK_POINT    = (1283, 842)   # hand-marked 2026-08-11 over the live game:
                            # x 1139 y 798, 289x89 — this is its centre
OK_WAIT_S   = 10.0          # …and if the banner never turns up, click anyway
OK_POLL_S   = 0.3
OK_TRIES    = 3             # re-click while the banner is still up
OK_SETTLE_S = 1.2           # how long one click gets to clear it


class ExitFlow(ClickFlow):
    """Clicks the round out: ОК, then optionally Повтор and Начать."""

    click_limits = START_CLICK_LIMITS

    def __init__(self, game_hwnd: int, restart: bool):
        super().__init__(game_hwnd)
        self._restart = restart

    def steps(self) -> list[tuple[str, str]]:
        return RESTART_STEPS if self._restart else []

    def _execute(self):
        if not self._press_ok():
            return
        if self._restart and not self.run_all_steps(RESTART_STEPS):
            return
        self.flow_done.emit()

    # ── OK ───────────────────────────────────────────────────────────────

    def _press_ok(self) -> bool:
        self.step_started.emit(OK_LABEL)
        banner = load_template(OK_BANNER)
        if banner is None:
            self.error.emit(f"Не найден шаблон: {OK_BANNER}")
            return False

        seen = self._await_banner(banner)
        for attempt in range(OK_TRIES):
            if self._stop_event.is_set():
                return False
            x, y = OK_POINT
            click_at(self._hwnd, x, y)
            self.clicked.emit(OK_LABEL, 1.0 if seen else 0.0, x, y)
            if not seen or self._banner_gone(banner):
                self.confirmed.emit(OK_LABEL)
                return True
        # Still up after every try. Say so and carry on anyway — the buttons
        # after this one look for themselves, and stopping here guarantees
        # the run never restarts, which is the failure being fixed.
        self.error.emit("Экран не закрылся после ОК — иду дальше")
        return True

    def _await_banner(self, banner) -> bool:
        """Wait for ИГРА ОКОНЧЕНА, or give up after OK_WAIT_S.

        Giving up still clicks. The banner is a way of knowing the button is
        there, not a permission slip — a round that stalls here because the
        artwork changed is exactly the outcome this replaced.
        """
        region   = primary_monitor_region()
        deadline = time.monotonic() + OK_WAIT_S
        while time.monotonic() < deadline and not self._stop_event.is_set():
            try:
                score, _ = best_match(self.grab_gray(self._hwnd, region), banner)
            except Exception as exc:
                self.error.emit(str(exc))
                return False
            if score >= self.banner_threshold:
                return True
            self._stop_event.wait(OK_POLL_S)
        return False

    def _banner_gone(self, banner) -> bool:
        """Has the click cleared the screen — the only evidence there is that
        it landed, now that the button itself is not being matched."""
        region   = primary_monitor_region()
        deadline = time.monotonic() + OK_SETTLE_S
        while time.monotonic() < deadline and not self._stop_event.is_set():
            self._stop_event.wait(OK_POLL_S)
            try:
                score, _ = best_match(self.grab_gray(self._hwnd, region), banner)
            except Exception:
                return True
            if score < self.banner_threshold:
                return True
        return False

    # Looser than the button bar: the banner is large, flat text and always
    # renders the same, but it sits over whatever the results screen is
    # animating behind it. Shared with GameOverWatch — see OK_BANNER.
    banner_threshold = MATCH_THRESHOLD
