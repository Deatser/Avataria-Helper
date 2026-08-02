# app/core/window_manager.py
from __future__ import annotations
from dataclasses import dataclass
import ctypes
import win32gui
import win32con

GA_ROOT         = 2    # GetAncestor: the top-level window of a chain
GW_OWNER        = 4    # GetWindow: the window that owns this one
GWLP_HWNDPARENT = -8   # the owner slot, not the parent one

# SetWindowLongPtrW where it exists (64-bit), SetWindowLongW otherwise. An
# owner is a handle, so the pointer-sized call is the correct one and the
# 32-bit version would truncate it.
_user32 = ctypes.windll.user32
_set_window_long_ptr = getattr(_user32, "SetWindowLongPtrW", None) or \
                       _user32.SetWindowLongW
_set_window_long_ptr.restype  = ctypes.c_void_p
_set_window_long_ptr.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]


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

    def raise_window(self, hwnd: int) -> bool:
        """Put hwnd on top of its siblings without moving or focusing it.

        NOACTIVATE matters: these windows are children of the game, and
        taking activation would pull focus off the game itself — the input
        goes there by PostMessage, but a focus change is still visible to
        the player and can pause some games outright.
        """
        if not hwnd:
            return False
        try:
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOP, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                | win32con.SWP_NOACTIVATE,
            )
            return True
        except win32gui.error:
            return False

    def root_of(self, hwnd: int) -> int:
        """The top-level window hwnd belongs to — the game, once attached."""
        if not hwnd:
            return 0
        try:
            return win32gui.GetAncestor(hwnd, GA_ROOT) or 0
        except win32gui.error:
            return 0

    def owner_of(self, hwnd: int) -> int:
        """Who currently owns hwnd — Qt resets this when it shows a window."""
        if not hwnd:
            return 0
        try:
            return win32gui.GetWindow(hwnd, GW_OWNER) or 0
        except win32gui.error:
            return 0

    def set_owner(self, hwnd: int, owner_hwnd: int) -> bool:
        """Make hwnd an owned window of owner_hwnd.

        Windows then keeps it above its owner and takes it away with it —
        minimised together, and covered together the moment another
        application comes to the front. That is precisely the behaviour
        wanted from anything drawn over the game, and it needs no polling of
        who is in the foreground, which is what the previous attempt did and
        what left the wires hidden whenever the game was not the active
        window.
        """
        if not hwnd or not owner_hwnd:
            return False
        try:
            _set_window_long_ptr(hwnd, GWLP_HWNDPARENT, owner_hwnd)
            return True
        except Exception:
            return False

    def window_rect_screen(self, hwnd: int) -> tuple[int, int, int, int] | None:
        """Where hwnd is on the screen, whoever its parent happens to be.

        window_origin answers in the space SetWindowPos wants, which for a
        re-parented window is the game's client area. Anything drawing across
        windows needs the one space they all share instead.
        """
        if not hwnd:
            return None
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            return left, top, right - left, bottom - top
        except win32gui.error:
            return None

    def make_click_through(self, hwnd: int) -> bool:
        """Let the mouse pass straight through hwnd to whatever is under it.

        WS_EX_TRANSPARENT on its own, not Qt's WindowTransparentForInput:
        that flag also brings WS_EX_LAYERED, and a layered window re-parented
        into the game's client area is exactly the combination that tends to
        end up invisible. Hit-testing is all that is wanted here.
        """
        if not hwnd:
            return False
        try:
            ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                                   ex | win32con.WS_EX_TRANSPARENT)
            return True
        except win32gui.error:
            return False

    def lower_window(self, hwnd: int) -> bool:
        """Put hwnd behind its siblings — where the node wires belong."""
        if not hwnd:
            return False
        try:
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_BOTTOM, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                | win32con.SWP_NOACTIVATE,
            )
            return True
        except win32gui.error:
            return False

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
