# tests/core/test_stats.py
import json

from app.core.stats import AppStats, StatsManager, shown


# ── Placeholders ────────────────────────────────────────────────────────────

def test_a_blank_field_shows_its_own_name():
    assert shown("", "player_name") == "%player_name%"
    assert shown("   ", "registration_date") == "%registration_date%"
    assert shown(None, "player_name") == "%player_name%"


def test_a_filled_field_shows_its_value():
    assert shown("Deatser", "player_name") == "Deatser"


def test_zero_is_a_real_answer_not_a_blank():
    # Nothing farmed yet is information; hiding it behind a placeholder would
    # make a fresh install look broken.
    assert shown(0, "games_played") == "0"


# ── File handling ───────────────────────────────────────────────────────────

def test_defaults_when_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stats = StatsManager()
    assert stats.data.player.player_name == ""
    assert stats.data.ava_dancers.games_played == 0
    assert stats.data.ava_dancers.gold_won == 0
    assert stats.data.ava_dancers.silver_won == 0


def test_partial_file_keeps_defaults_for_the_rest(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stats.json").write_text(
        json.dumps({"player": {"player_name": "Deatser"}}), encoding="utf-8")

    stats = StatsManager()

    assert stats.data.player.player_name == "Deatser"
    assert stats.data.player.registration_date == ""
    assert stats.data.ava_dancers.games_played == 0


def test_corrupted_file_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "stats.json").write_text("not json {{{", encoding="utf-8")

    assert StatsManager().data == AppStats()


def test_save_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stats = StatsManager()
    stats.data.player.player_name = "Deatser"
    stats.data.ava_dancers.gold_won = 125
    stats.save()

    assert StatsManager().data == stats.data


def test_the_shipped_file_has_every_field(tmp_path, monkeypatch):
    # The real stats.json, so a field added in code but forgotten in the file
    # is caught here rather than showing up as a silent placeholder.
    raw = json.loads(StatsManager.STATS_FILE.read_text(encoding="utf-8"))
    assert set(raw) == {"player", "ava_dancers", "gardener", "janitor"}
    assert set(raw["player"]) == {"player_id", "player_name",
                                  "registration_date"}
    assert set(raw["ava_dancers"]) == {"games_played", "gold_won", "silver_won"}
    assert set(raw["gardener"]) == {"shifts_finished", "clean_next_time",
                                    "clean_was_time"}
    assert set(raw["janitor"]) == {"shifts_finished", "clean_next_time",
                                   "clean_was_time"}


# ── Recording ───────────────────────────────────────────────────────────────

def test_a_recorded_run_counts_and_persists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stats = StatsManager()

    stats.record_ava_dancers_run(gold=30)
    stats.record_ava_dancers_run(silver=2250)

    reloaded = StatsManager().data.ava_dancers
    assert reloaded.games_played == 2
    assert reloaded.gold_won == 30
    assert reloaded.silver_won == 2250


def test_a_negative_payout_cannot_eat_the_total(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    stats = StatsManager()
    stats.data.ava_dancers.gold_won = 100

    stats.record_ava_dancers_run(gold=-50)

    assert stats.data.ava_dancers.gold_won == 100
