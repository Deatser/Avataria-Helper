# app/core/input_sender.py
import threading
import win32api
import win32con

_HOLD_S = 0.02   # WM_KEYDOWN to WM_KEYUP gap

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
    win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
    threading.Timer(_HOLD_S, win32api.PostMessage,
                     args=(hwnd, win32con.WM_KEYUP, vk, 0)).start()
    return True
