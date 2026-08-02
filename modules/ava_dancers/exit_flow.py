# modules/ava_dancers/exit_flow.py
from __future__ import annotations

from modules.ava_dancers.click_flow import ClickFlow

# The screens that follow a finished round, in the order they appear.
OK_STEP       = ("Кнопка ОК", "leave_ok.png")
RESTART_STEPS = [
    ("Кнопка ЗАНОВО", "leave_repeat.png"),
    ("Кнопка НАЧАТЬ", "leave_start.png"),
]


class ExitFlow(ClickFlow):
    """Clicks the round out: ОК, then optionally Повтор and Начать."""

    def __init__(self, game_hwnd: int, restart: bool):
        super().__init__(game_hwnd)
        self._restart = restart

    def steps(self) -> list[tuple[str, str]]:
        """ОК always; the two restart buttons only when asked for."""
        return [OK_STEP] + (RESTART_STEPS if self._restart else [])
