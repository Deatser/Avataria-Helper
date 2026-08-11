# tests/core/test_input_sender.py
from app.core.input_sender import VK_MAP, press_key


def test_vk_map_has_dance_keys():
    assert VK_MAP["a"] == 0x41
    assert VK_MAP["s"] == 0x53
    assert VK_MAP["w"] == 0x57
    assert VK_MAP["d"] == 0x44


def test_vk_map_has_arrow_keys():
    assert VK_MAP["left"]  == 0x25
    assert VK_MAP["up"]    == 0x26
    assert VK_MAP["right"] == 0x27
    assert VK_MAP["down"]  == 0x28


def test_press_key_returns_false_for_zero_hwnd():
    assert press_key(0, "a") is False


def test_press_key_returns_false_for_unknown_key():
    assert press_key(0, "xyz_not_a_key") is False


def test_press_key_case_insensitive():
    assert VK_MAP.get("a") == VK_MAP.get("A".lower())


# ── A scheduled press is exactly one press ──────────────────────────────────

def test_press_key_after_sends_the_key_once(monkeypatch):
    """AvaDancers hits a note once or not at all.

    This used to fire the same key three times 30ms apart, to cover for a
    detector that could only guess the moment to within a whole poll. Once
    the moment became accurate, the two extra presses landed on an empty
    lane and the game scored them as wrong presses.
    """
    from app.core import input_sender

    downs = []
    monkeypatch.setattr(input_sender.win32api, "PostMessage",
                        lambda hwnd, msg, wp, lp:
                        downs.append(wp) if msg == input_sender.win32con.WM_KEYDOWN
                        else None)
    monkeypatch.setattr(input_sender, "_input_target", lambda hwnd: hwnd)

    input_sender.press_key_after(4242, "a", 0.0)

    assert downs == [input_sender.VK_MAP["a"]]


def test_press_key_after_defers_a_future_press(monkeypatch):
    from app.core import input_sender

    scheduled = []
    monkeypatch.setattr(input_sender._delayed, "call_later",
                        lambda delay, fn, *args: scheduled.append((delay, args)))

    input_sender.press_key_after(4242, "s", 0.42)

    assert scheduled == [(0.42, (4242, "s"))]


# ── Clicks give the pointer back ────────────────────────────────────────────

def test_a_point_outside_the_window_still_packs(monkeypatch):
    """The mouse is often nowhere near the window; its coordinates are then
    negative, and a naive MAKELONG refuses them."""
    from app.core.input_sender import _lparam

    assert _lparam(10, 20) == (20 << 16) | 10
    assert _lparam(-1, -1) == 0xFFFFFFFF


def test_the_release_hands_the_pointer_back_to_the_real_mouse(monkeypatch):
    """The game draws its pointer where it was last told the mouse is, so a
    run that clicks all over the garden must put it back afterwards."""
    from app.core import input_sender

    posted = []
    monkeypatch.setattr(input_sender.win32api, "PostMessage",
                        lambda hwnd, msg, wp, lp: posted.append((msg, lp)))
    monkeypatch.setattr(input_sender.win32api, "GetCursorPos",
                        lambda: (700, 400))
    monkeypatch.setattr(input_sender.win32gui, "ScreenToClient",
                        lambda hwnd, point: point)

    input_sender._release(4242, input_sender._lparam(10, 20), True)

    assert posted[0][0] == input_sender.win32con.WM_LBUTTONUP
    assert posted[-1] == (input_sender.win32con.WM_MOUSEMOVE,
                          input_sender._lparam(700, 400))


def test_the_pointer_is_left_alone_when_it_is_not_asked_for(monkeypatch):
    from app.core import input_sender

    posted = []
    monkeypatch.setattr(input_sender.win32api, "PostMessage",
                        lambda hwnd, msg, wp, lp: posted.append((msg, lp)))

    input_sender._release(4242, input_sender._lparam(10, 20), False)

    assert [msg for msg, _lp in posted] == [input_sender.win32con.WM_LBUTTONUP]
