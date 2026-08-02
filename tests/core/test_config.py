# tests/core/test_config.py
import json
import pytest
from app.core.config import ConfigManager, AppConfig
from app.core.template_match import FINISH_GOLD, FINISH_SILVER


def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = ConfigManager()
    assert cfg.data.overlay.x == 10
    assert cfg.data.overlay.width == 330
    assert cfg.data.ava_dancers.red_share == 0.03
    assert cfg.data.ava_dancers.favorite is False


def test_finish_target_defaults_to_gold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert ConfigManager().data.ava_dancers.finish_on == FINISH_GOLD


def test_finish_target_survives_a_save_and_reload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = ConfigManager()
    cfg.data.ava_dancers.finish_on = FINISH_SILVER
    cfg.save()
    assert ConfigManager().data.ava_dancers.finish_on == FINISH_SILVER


def test_auto_restart_defaults_to_on(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert ConfigManager().data.ava_dancers.auto_restart is True


def test_auto_restart_survives_a_save_and_reload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = ConfigManager()
    cfg.data.ava_dancers.auto_restart = False
    cfg.save()
    assert ConfigManager().data.ava_dancers.auto_restart is False


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
    cfg.data.ava_dancers.red_share = 0.07
    cfg.save()
    raw = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert raw["overlay"]["x"] == 99
    assert raw["ava_dancers"]["red_share"] == 0.07


def test_type_coercion_on_load(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"overlay": {"x": 50.9}}), encoding="utf-8"
    )
    cfg = ConfigManager()
    assert cfg.data.overlay.x == 50   # int, not float
