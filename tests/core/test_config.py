# tests/core/test_config.py
import json
import pytest
from app.core.config import ConfigManager, AppConfig


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = ConfigManager()
    assert cfg.data.overlay.x == 10
    assert cfg.data.overlay.width == 330
    assert cfg.data.ava_dancers.min_active == 3500
    assert cfg.data.ava_dancers.favorite is False


def test_load_partial_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"overlay": {"x": 50, "y": 100}}), encoding="utf-8"
    )
    cfg = ConfigManager()
    assert cfg.data.overlay.x == 50
    assert cfg.data.overlay.y == 100
    assert cfg.data.overlay.width == 330   # missing key → default


def test_corrupted_config_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text("not valid json {{{", encoding="utf-8")
    cfg = ConfigManager()
    assert isinstance(cfg.data, AppConfig)
    assert cfg.data.overlay.x == 10


def test_save_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = ConfigManager()
    cfg.data.overlay.x = 99
    cfg.data.ava_dancers.min_active = 4000
    cfg.save()
    raw = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert raw["overlay"]["x"] == 99
    assert raw["ava_dancers"]["min_active"] == 4000


def test_type_coercion_on_load(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"overlay": {"x": 50.9}}), encoding="utf-8"
    )
    cfg = ConfigManager()
    assert cfg.data.overlay.x == 50   # int, not float
