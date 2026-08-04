# tests/core/test_daily_log.py
import json
import os
from datetime import datetime

from app.core.daily_log import KEEP_DAYS, DailyLog


def _today_path(tmp_path):
    name = f"Статистика за {datetime.now().strftime('%d.%m.%Y')}.json"
    return tmp_path / "logs" / name


def test_ensure_today_creates_a_zeroed_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    DailyLog().ensure_today()

    raw = json.loads(_today_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["ava_dancers"] == {"games_played": 0, "gold_won": 0, "silver_won": 0}
    assert raw["gardener"] == {"shifts_finished": 0}


def test_ensure_today_does_not_clobber_an_existing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    log = DailyLog()
    log.bump_run("ava_dancers", gold=30, silver=2250)

    log.ensure_today()   # a second "startup" the same day

    raw = json.loads(_today_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["ava_dancers"]["gold_won"] == 30   # not reset back to 0


def test_bump_run_accumulates_across_calls(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    log = DailyLog()
    log.bump_run("ava_dancers", gold=30, silver=2250)
    log.bump_run("ava_dancers", silver=2250)

    raw = json.loads(_today_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["ava_dancers"] == {"games_played": 2, "gold_won": 30, "silver_won": 4500}


def test_different_modules_stay_independent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    log = DailyLog()
    log.bump_run("ava_dancers", gold=30, silver=2250)
    log.bump_run("hockey", silver=100)

    raw = json.loads(_today_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["ava_dancers"]["gold_won"] == 30
    assert raw["hockey"] == {"games_played": 1, "gold_won": 0, "silver_won": 100}


def test_bump_cleanup_accumulates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    log = DailyLog()
    log.bump_cleanup("gardener")
    log.bump_cleanup("gardener")
    log.bump_cleanup("janitor")

    raw = json.loads(_today_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["gardener"]["shifts_finished"] == 2
    assert raw["janitor"]["shifts_finished"] == 1


def test_prune_keeps_only_the_newest_kept_days(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    extra = 5
    old_paths = []
    for i in range(KEEP_DAYS + extra):
        p = logs_dir / f"Статистика за старый-{i:02d}.json"
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (i, i))   # increasing — i is oldest, then newest
        old_paths.append(p)

    DailyLog().ensure_today()   # one more (newest) file — pushes the total
                                # to KEEP_DAYS + extra + 1, so extra + 1 of
                                # the oldest fall off the kept window

    remaining = set(logs_dir.glob("Статистика за *.json"))
    assert len(remaining) == KEEP_DAYS
    assert all(p not in remaining for p in old_paths[:extra + 1])   # oldest pruned
    assert all(p in remaining for p in old_paths[extra + 1:])       # rest kept
    assert _today_path(tmp_path) in remaining
