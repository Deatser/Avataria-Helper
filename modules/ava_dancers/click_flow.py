# modules/ava_dancers/click_flow.py
from __future__ import annotations
import threading
import time

import cv2
from PySide6.QtCore import QThread, Signal

from app.core.capture import ScreenCapture
from app.core.input_sender import click_at
from app.core.template_match import (best_match, load_template,
                                     primary_monitor_region)

# These are fixed pieces of chrome — no number changes inside them the way
# the reward line's does — so they can be held to a stricter bar than the
# reward templates. Not 0.96 though: the same render differences that kept
# the timer marks off 0.98 apply here.
BUTTON_THRESHOLD = 0.93

# How far a button's score has to fall from its own best reading before the
# click is believed. Small on purpose: measured live, "Игры" went 99.7% →
# 93.8% the moment its screen opened — the button is still drawn, just no
# longer highlighted the same way. A wider bar (0.20 was the first guess)
# never fires on a change like that, and the step sat there until it timed
# out. Six points of real movement, so three is enough to notice it.
CONFIRM_DROP = 0.03

# ...held for this many polls in a row. A single dipped frame can be an
# animation flickering, two in a row is the screen having actually changed.
DROP_POLLS = 2

# A button that is still there gets clicked again — but on its own clock, not
# once per poll. A fast flow polls several times a second, and a menu button
# taking a moment to respond would otherwise collect a dozen clicks, which for
# a toggle means opening and closing the very screen we are trying to reach.
RECLICK_INTERVAL = 0.3

POLL_INTERVAL = 0.4    # UI navigation, not gameplay — no need to be fast
STEP_TIMEOUT  = 30.0   # give up on a button rather than spin forever


def confirmed_gone(peak: float, score: float,
                   drop: float = CONFIRM_DROP) -> bool:
    """True when a button that was on screen has clearly left it."""
    return score <= peak - drop


class ClickFlow(QThread):
    """Walks a list of buttons, clicking each one until the screen moves on.

    Every step is the same little loop — find the button, click it, and keep
    watching until its match score collapses. Waiting for the collapse is the
    whole point: it is the only evidence available that the click actually
    landed, so the next button is never hunted for while the previous screen
    is still up, and no click is ever aimed at a screen in mid-transition.
    """
    step_started = Signal(str)                   # label
    clicked      = Signal(str, float, int, int)  # label, score, x, y
    confirmed    = Signal(str)                   # label — the screen moved on
    flow_done    = Signal()
    error        = Signal(str)

    poll_interval = POLL_INTERVAL
    step_timeout  = STEP_TIMEOUT

    def __init__(self, game_hwnd: int):
        super().__init__()
        self._hwnd       = game_hwnd
        self._stop_event = threading.Event()

    # ── To implement ─────────────────────────────────────────────────────────

    def steps(self) -> list[tuple[str, str]]:
        """[(label, template filename), ...] in the order they are clicked."""
        raise NotImplementedError

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def stop_flow(self):
        self._stop_event.set()

    def run(self):
        self._stop_event.clear()
        try:
            self._execute()
        finally:
            ScreenCapture.release()

    def _execute(self):
        """Overridden where a flow has to decide something before it starts."""
        if self.run_all_steps():
            self.flow_done.emit()

    def run_all_steps(self, steps: list[tuple[str, str]] = None) -> bool:
        steps     = self.steps() if steps is None else steps
        templates = []
        for _label, filename in steps:
            template = load_template(filename)
            if template is None:
                self.error.emit(f"Не найден шаблон кнопки: {filename}")
                return False
            templates.append(template)

        for i, (label, _filename) in enumerate(steps):
            following = templates[i + 1] if i + 1 < len(templates) else None
            if not self.run_step(label, templates[i], following):
                return False
        return True

    # ── One button ───────────────────────────────────────────────────────────

    def grab_gray(self, capture, region):
        return cv2.cvtColor(capture.grab(region), cv2.COLOR_BGR2GRAY)

    def run_step(self, label: str, template, following=None) -> bool:
        """Click one button until the flow has visibly moved past it.

        The button always gets clicked first — a step is never skipped on the
        strength of what else is on screen — and keeps being clicked until
        one of two things says the screen moved:

          * its own score dropped off its best reading. Note the score does
            not have to fall below the click bar: "Игры" goes 99.7% → 93.8%
            and stays perfectly clickable, so the drop is checked before the
            click, or the step would just keep pressing a button whose work
            is already done;
          * the *next* button turned up, having not been there when the step
            began. That qualifier matters. "Места" holds a flat 100% after
            it is clicked and never drops at all, so the only proof it
            worked is "Игры" appearing. The scroll arrow, on the other hand,
            is already on screen inside the Места panel, so its presence
            proves nothing about the Игры click — a next button that was up
            from the start is ignored for the rest of the step.

        No timers anywhere: every step ends on evidence, and the only wait is
        the poll interval.
        """
        self.step_started.emit(label)
        capture    = ScreenCapture.get()
        region     = primary_monitor_region()
        deadline   = time.monotonic() + self.step_timeout
        h, w       = template.shape[:2]
        peak       = 0.0
        seen       = False
        last_click = 0.0
        first_poll = True
        next_useless = False   # the next button was already up when we began
        low_polls    = 0       # consecutive polls reading below the peak

        while not self._stop_event.is_set():
            if time.monotonic() > deadline:
                self.error.emit(
                    f"{label}: не дождался за {int(self.step_timeout)} с — "
                    f"лучшее совпадение {peak:.0%}")
                return False
            try:
                gray = self.grab_gray(capture, region)

                if following is not None and not next_useless:
                    next_score, _ = best_match(gray, following)
                    if first_poll:
                        next_useless = next_score >= BUTTON_THRESHOLD
                    elif seen and next_score >= BUTTON_THRESHOLD:
                        self.confirmed.emit(label)
                        return True
                first_poll = False

                score, (x, y) = best_match(gray, template)

                # Checked ahead of the click: a button can drop and still sit
                # above the click bar, and re-pressing it then would be
                # pressing a screen that has already moved on.
                if seen and confirmed_gone(peak, score):
                    low_polls += 1
                    if low_polls >= DROP_POLLS:
                        self.confirmed.emit(label)
                        return True
                    continue_clicking = False
                else:
                    low_polls = 0
                    continue_clicking = True

                if continue_clicking and score >= BUTTON_THRESHOLD:
                    peak = max(peak, score)
                    now  = time.monotonic()
                    if now - last_click >= RECLICK_INTERVAL:
                        last_click = now
                        cx, cy = x + w // 2, y + h // 2
                        click_at(self._hwnd, cx, cy)
                        # Only the first click is announced: the repeats are
                        # insurance, not news, and a button that lingers would
                        # otherwise fill the log by itself.
                        if not seen:
                            self.clicked.emit(label, score, cx, cy)
                        seen = True

            except Exception as exc:
                self.error.emit(str(exc))

            self._stop_event.wait(self.poll_interval)
        return False
