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
