# app/core/input_sender.py
import heapq
import threading
import time
import win32api
import win32con
import win32gui

_HOLD_S = 0.02   # KEYDOWN to KEYUP gap

# AvaDancers' isolated HSV-threshold test tool (testlogs/test#16-33.json)
# spent a long stretch chasing keys that logged as sent but didn't register
# in the game — a focus-settle delay, then SendInput with a forced real
# OS-focus switch, neither one actually more reliable there than plain
# PostMessage. The one mechanism confirmed to reliably land real presses,
# across full AvaDancers runs, start to finish, without ever needing real
# OS focus on the game at all, is this one, exactly as it already was —
# so whatever the test tool's own problem is, it isn't this function.


class _DelayedCalls:
    """Runs KEYUP/mouse-release callbacks off one background thread instead
    of a fresh threading.Timer (== a fresh OS thread) per call. A finish-run
    mash posts up to ~266 delayed releases in 4 seconds — that many short-
    lived threads spun up back to back visibly loads the OS scheduler right
    when press timing matters most, so one persistent thread with a small
    priority queue replaces all of them.
    """

    def __init__(self):
        self._heap: list[tuple[float, int, callable, tuple]] = []
        self._counter = 0
        self._cv = threading.Condition()
        threading.Thread(target=self._run, daemon=True).start()

    def call_later(self, delay: float, func, *args):
        fire_at = time.monotonic() + delay
        with self._cv:
            self._counter += 1
            heapq.heappush(self._heap, (fire_at, self._counter, func, args))
            self._cv.notify()

    def _run(self):
        with self._cv:
            while True:
                if not self._heap:
                    self._cv.wait()
                    continue
                fire_at, _, func, args = self._heap[0]
                remaining = fire_at - time.monotonic()
                if remaining <= 0:
                    heapq.heappop(self._heap)
                    self._cv.release()
                    try:
                        func(*args)
                    except Exception:
                        pass
                    finally:
                        self._cv.acquire()
                else:
                    self._cv.wait(remaining)


_delayed = _DelayedCalls()

# The game is a Chromium/Electron window: the top-level hwnd (Chrome_WidgetWin_1)
# only hosts the real input target, this child, which is what actually turns a
# posted message into a DOM event — and which additionally checks its own idea
# of focus before doing so, unlike a plain Win32 control.
_RENDER_CLASS = "Chrome_RenderWidgetHostHWND"


def _input_target(hwnd: int) -> int:
    """The window a message actually has to land on to reach the page.

    Falls back to hwnd itself for anything that isn't this kind of window
    (or where the child hasn't been found), so plain Win32 targets still work
    exactly as before.
    """
    child = win32gui.FindWindowEx(hwnd, 0, _RENDER_CLASS, None)
    return child or hwnd


def _prime_focus(hwnd: int, target: int):
    """Tell the page it is focused without touching real OS focus.

    Posted, exactly like the input that follows — never SetFocus/
    SetForegroundWindow, which would pull focus (and the real cursor) away
    from whatever the user is actually doing. Chromium only turns a posted
    key or click into a page event once it believes it is the focused
    window; nothing here ever tells it otherwise afterwards (that would take
    a real WM_KILLFOCUS, which only arrives if the window actually had OS
    focus to lose), so once this sticks it keeps sticking.
    """
    win32api.PostMessage(hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
    win32api.PostMessage(target, win32con.WM_SETFOCUS, 0, 0)


VK_MAP: dict[str, int] = {
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45,
    "f": 0x46, "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A,
    "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E, "o": 0x4F,
    "p": 0x50, "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54,
    "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59,
    "z": 0x5A,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "space": 0x20, "enter": 0x0D, "escape": 0x1B,
    "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38,
}


def press_key(hwnd: int, key: str) -> bool:
    """Send WM_KEYDOWN, then WM_KEYUP after a short hold, to hwnd.

    Posting KEYUP in the same instant as KEYDOWN is a classic way for a game
    to silently drop the input: many engines only read key state once per
    render frame, and a down+up pair that lands between two of the game's own
    message-pump drains can toggle to true then back to false before the
    game's frame ever samples it — worse under load, exactly when the bot's
    own poll rate is highest. The KEYUP fires on a timer so this doesn't block
    the caller (the bot's detection loop).
    """
    vk = VK_MAP.get(key.lower())
    if not vk or not hwnd:
        return False
    target = _input_target(hwnd)
    _prime_focus(hwnd, target)
    win32api.PostMessage(target, win32con.WM_KEYDOWN, vk, 0)
    _delayed.call_later(_HOLD_S, win32api.PostMessage,
                        target, win32con.WM_KEYUP, vk, 0)
    return True


def _lparam(x: int, y: int) -> int:
    """Pack a point the way a mouse message carries it.

    Masked rather than passed to MAKELONG: a point outside the window is a
    perfectly ordinary thing to report, and its coordinates are negative.
    """
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def click_at(hwnd: int, screen_x: int, screen_y: int,
             give_back: bool = True) -> bool:
    """Post a left click at a screen point, in hwnd's own client coordinates.

    Posted, not synthesised with the real cursor, for the same reason keys
    are: the game does not have to be focused, the pointer never jumps out
    from under the user, and — since the overlay sits on top of the game as a
    child window — a click aimed at the game cannot be swallowed by our own
    UI on the way.

    A move is posted first: a button that only lights its hover state on
    WM_MOUSEMOVE can otherwise ignore a press that arrives on a spot the
    cursor was never over. Release goes out on a timer, like KEYUP.

    That move is then taken back. The game draws its own pointer wherever it
    was last told the mouse is, so a run that clicks dozens of places leaves
    that pointer skidding around the garden and fighting whoever is holding
    the real mouse. Posting one last move to where the mouse actually is puts
    it back under their hand within a frame of the click.
    """
    if not hwnd:
        return False
    target = _input_target(hwnd)
    try:
        cx, cy = win32gui.ScreenToClient(target, (int(screen_x), int(screen_y)))
    except win32gui.error:
        return False
    pos = _lparam(cx, cy)
    _prime_focus(hwnd, target)
    win32api.PostMessage(target, win32con.WM_MOUSEMOVE, 0, pos)
    win32api.PostMessage(target, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, pos)
    _delayed.call_later(_HOLD_S, _release, target, pos, give_back)
    return True


def _release(hwnd: int, pos: int, give_back: bool):
    win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, pos)
    if not give_back:
        return
    try:
        x, y = win32gui.ScreenToClient(hwnd, win32api.GetCursorPos())
    except Exception:
        return          # no cursor to hand back to; the click still stands
    win32api.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, _lparam(x, y))


def _to_client(hwnd: int, screen_x: int, screen_y: int) -> tuple[int, int] | None:
    try:
        return win32gui.ScreenToClient(hwnd, (int(screen_x), int(screen_y)))
    except win32gui.error:
        return None


def mouse_down_at(hwnd: int, screen_x: int, screen_y: int) -> bool:
    """Press and hold the left button at a screen point — the opening move
    of a drag (Хоккей's curved shot: hold, drag left/right, release).
    Pairs with mouse_move_to (while held) and mouse_up_at (to let go);
    unlike click_at, nothing here posts its own release on a timer, since a
    drag's whole point is the button staying down across several moves the
    caller spaces out itself.
    """
    if not hwnd:
        return False
    target = _input_target(hwnd)
    client = _to_client(target, screen_x, screen_y)
    if client is None:
        return False
    pos = _lparam(*client)
    _prime_focus(hwnd, target)
    win32api.PostMessage(target, win32con.WM_MOUSEMOVE, 0, pos)
    win32api.PostMessage(target, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, pos)
    return True


def mouse_move_to(hwnd: int, screen_x: int, screen_y: int) -> bool:
    """One point along a held drag — WM_MOUSEMOVE carrying MK_LBUTTON, the
    flag that tells a game a drag is still in progress rather than the
    pointer just wandering with nothing held."""
    if not hwnd:
        return False
    target = _input_target(hwnd)
    client = _to_client(target, screen_x, screen_y)
    if client is None:
        return False
    win32api.PostMessage(target, win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON,
                         _lparam(*client))
    return True


def mouse_up_at(hwnd: int, screen_x: int, screen_y: int,
                give_back: bool = True) -> bool:
    """Release the drag mouse_down_at started, at a screen point — the same
    cursor-restoring courtesy click_at's own release does."""
    if not hwnd:
        return False
    target = _input_target(hwnd)
    client = _to_client(target, screen_x, screen_y)
    if client is None:
        return False
    pos = _lparam(*client)
    win32api.PostMessage(target, win32con.WM_MOUSEMOVE, win32con.MK_LBUTTON, pos)
    _release(target, pos, give_back)
    return True
