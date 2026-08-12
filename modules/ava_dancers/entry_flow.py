# modules/ava_dancers/entry_flow.py
from __future__ import annotations

from PySide6.QtCore import Signal

from app.core.template_match import (best_match, load_template,
                                     primary_monitor_region)
from modules.ava_dancers.click_flow import BUTTON_THRESHOLD, ClickFlow
from modules.ava_dancers.exit_flow import (OK_STEP, RESTART_STEPS,
                                           START_CLICK_LIMITS)

# The in-game EXIT sign — the one piece of chrome that is on screen during a
# round and nowhere else, which makes it the cheapest possible answer to "are
# we already in Ava Dancers?".
EXIT_BUTTON = ("Кнопка EXIT", "button_exit.png")

# Deliberately below the 0.93 a click is held to. This reading is not aimed
# at, only looked at, and being slightly generous here is the safe direction:
# mistaking "in the game" for "in the menus" would send the click chain
# hunting for menu buttons that are not there, which costs a whole timeout.
EXIT_PRESENT = 0.85

# The tail of the chain: Ava Dancers' own lobby, where a mode is picked and
# the round is started. Reachable two ways — through the menus, or by being
# dropped there already — so it is named separately. Its last step is the
# same Начать the restart path ends on, taken from one definition so the two
# can never drift apart.
SOLO_STEP   = ("Кнопка ОДИНОЧНЫЙ РЕЖИМ", "button_solo.png")
REPEAT_STEP, START_STEP = RESTART_STEPS
LOBBY_STEPS = [SOLO_STEP, START_STEP]

# Each of these is its own unambiguous marker for "inside Ava Dancers but not
# playing" — the results screen of a round that just ended (ОК, ЗАНОВО) or
# the lobby (Одиночный режим) — checked directly rather than gated behind the
# EXIT sign first: that gate used to mean a flaky EXIT-sign reading on the
# results screen could send the flow down the full menu-walk chain instead,
# where none of Места/Игры/... are there to find. All three markers are
# scored off the same screenshot; the first one recognised says where to pick
# the chain up, and the steps after it follow from there. Ordered the way the
# screens appear, so a results screen showing both ОК and ЗАНОВО starts at ОК.
RESUME_POINTS = [
    (OK_STEP,     [OK_STEP] + RESTART_STEPS),
    (REPEAT_STEP, RESTART_STEPS),
    (SOLO_STEP,   LOBBY_STEPS),
]

# From wherever the game is sitting to a running round: Места, the games
# list, the scroll that brings Ava Dancers into view, the game itself, then
# the lobby. Order matters twice over — it is the click order, and each step
# also uses the one after it to tell that its own click worked (see
# ClickFlow.run_step).
ENTRY_STEPS = [
    ("Кнопка МЕСТА",       "button_places.png"),
    ("Кнопка ИГРЫ",        "button_games.png"),
    ("Прокрутка вниз",     "button_scroll_down.png"),
    ("Кнопка AVA DANCERS", "button_avadancers.png"),
] + LOBBY_STEPS


class EntryFlow(ClickFlow):
    """Gets the game to a running round before the detector is switched on.

    One screenshot decides where the game already is:

      * one of the resume markers (ОК, ЗАНОВО, Одиночный режим) — inside Ava
        Dancers but not playing: on the results screen of a finished round,
        or in the lobby. The chain is picked up from there rather than
        walked from the start;
      * no resume marker, but the EXIT sign is up — a round is already
        running, nothing to do, and the screenshots stop right there;
      * neither — out in the menus, so the whole chain runs.
    """
    exit_checked = Signal(float)   # score of the EXIT sign
    in_game      = Signal()        # already playing — chain skipped
    in_lobby     = Signal(str)     # inside the game but not playing; carries
                                    # the label of the button to resume from

    # As fast as the grabs allow: this runs while the user waits for the bot
    # to come up, and every poll is a full screen capture anyway. Re-clicks
    # stay on their own slower clock — see RECLICK_INTERVAL.
    poll_interval = 0.05

    # Единственная кнопка с потолком нажатий — см. START_CLICK_LIMITS.
    click_limits = START_CLICK_LIMITS

    def steps(self) -> list[tuple[str, str]]:
        return ENTRY_STEPS

    def _execute(self):
        gray = self._screen()
        if gray is None:
            return

        for (label, filename), steps in RESUME_POINTS:
            if self._score(gray, filename) >= BUTTON_THRESHOLD:
                self.in_lobby.emit(label)
                if self.run_all_steps(steps):
                    self.flow_done.emit()
                return

        exit_score = self._score(gray, EXIT_BUTTON[1])
        self.exit_checked.emit(exit_score)

        if exit_score >= EXIT_PRESENT:
            self.in_game.emit()
            self.flow_done.emit()
            return

        if self.run_all_steps():
            self.flow_done.emit()

    def _screen(self):
        """One grab, shared by every check below; None if the grab failed."""
        try:
            return self.grab_gray(self._hwnd, primary_monitor_region())
        except Exception as exc:
            self.error.emit(str(exc))
            return None

    def _score(self, gray, filename: str) -> float:
        """How well one template matches the given screen; 0.0 if missing."""
        template = load_template(filename)
        if template is None:
            self.error.emit(f"Не найден шаблон: {filename}")
            return 0.0
        score, _ = best_match(gray, template)
        return score
