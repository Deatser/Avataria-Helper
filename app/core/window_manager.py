# app/core/window_manager.py
from __future__ import annotations
from dataclasses import dataclass
import win32gui
import win32con


@dataclass
class WindowRect:
    left: int
    top: int
    width: int
    height: int


class WindowManager:
    def __init__(self):
        self._game_hwnd: int | None = None

    def find_game(self, title_contains: str) -> bool:
        """Scan all visible windows for one whose title contains title_contains."""
        result: int | None = None

        def callback(hwnd: int, _):
            nonlocal result
            if win32gui.IsWindowVisible(hwnd):
                if title_contains in win32gui.GetWindowText(hwnd):
                    result = hwnd

        win32gui.EnumWindows(callback, None)
        self._game_hwnd = result
        return result is not None

    def get_game_hwnd(self) -> int | None:
        return self._game_hwnd

    def is_game_alive(self) -> bool:
        """True while the stored game window still exists (no re-scan)."""
        return bool(self._game_hwnd) and bool(win32gui.IsWindow(self._game_hwnd))

    def attach_overlay(self, overlay_hwnd: int, x: int, y: int, w: int, h: int):
        """Make overlay_hwnd a WS_CHILD of the game window at position (x, y)."""
        if not self._game_hwnd:
            return
        win32gui.SetParent(overlay_hwnd, self._game_hwnd)
        style = win32gui.GetWindowLong(overlay_hwnd, win32con.GWL_STYLE)
        win32gui.SetWindowLong(overlay_hwnd, win32con.GWL_STYLE, style | win32con.WS_CHILD)
        self.move_window(overlay_hwnd, x, y, w, h)

    def attach_child(self, child_hwnd: int):
        """Make child_hwnd a WS_CHILD of the game window, keep current position."""
        if not self._game_hwnd:
            return
        win32gui.SetParent(child_hwnd, self._game_hwnd)
        style = win32gui.GetWindowLong(child_hwnd, win32con.GWL_STYLE)
        win32gui.SetWindowLong(child_hwnd, win32con.GWL_STYLE, style | win32con.WS_CHILD)
        win32gui.SetWindowPos(
            child_hwnd, win32con.HWND_TOP, 0, 0, 0, 0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )

    def move_window(self, hwnd: int, x: int, y: int, w: int, h: int):
        """Move hwnd to (x, y) clamped to game client area."""
        if not self._game_hwnd or not hwnd:
            return
        x, y = self._clamp(x, y, w, h)
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOP, x, y, w, h, win32con.SWP_SHOWWINDOW
        )

    def window_origin(self, hwnd: int) -> tuple[int, int] | None:
        """Top-left of hwnd in the coordinate space SetWindowPos expects:
        parent-client for a re-parented window, screen for a top-level one.
        None when hwnd is not a live window — the caller falls back to Qt."""
        if not hwnd:
            return None
        try:
            left, top, _, _ = win32gui.GetWindowRect(hwnd)
            parent = win32gui.GetParent(hwnd)
            if parent:
                left, top = win32gui.ScreenToClient(parent, (left, top))
            return left, top
        except win32gui.error:
            return None

    def set_window_rect(self, hwnd: int, x: int, y: int, w: int, h: int) -> bool:
        """Place hwnd exactly, no clamping — used to pin a window while it
        animates, so nothing (Qt included) can drift it. False if it failed."""
        if not hwnd:
            return False
        try:
            win32gui.SetWindowPos(
                hwnd, 0, x, y, w, h,
                win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW,
            )
            return True
        except win32gui.error:
            return False

    def _clamp(self, x: int, y: int, w: int, h: int) -> tuple[int, int]:
        if not self._game_hwnd:
            return x, y
        l, t, r, b = win32gui.GetClientRect(self._game_hwnd)
        gw, gh = r - l, b - t
        return max(0, min(x, gw - w)), max(0, min(y, gh - h))

    def get_game_rect(self) -> WindowRect | None:
        if not self._game_hwnd:
            return None
        l, t, r, b = win32gui.GetClientRect(self._game_hwnd)
        return WindowRect(l, t, r - l, b - t)
