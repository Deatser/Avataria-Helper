# tests/modules/test_game_over_watch.py
from modules.ava_dancers.game_over_watch import GameOverWatch, MATCH_THRESHOLD


# ── Trigger ─────────────────────────────────────────────────────────────────
# _check_score is exercised directly: it is the whole decision, and driving
# it through a running QThread would need the screen to show the banner.

def _fired(scores):
    watch = GameOverWatch(0)
    seen = []
    watch.game_over.connect(lambda score: seen.append(score))
    for score in scores:
        watch._check_score(score)
    return seen


def test_fires_at_the_threshold():
    assert _fired([MATCH_THRESHOLD])


def test_below_the_threshold_nothing_fires():
    assert not _fired([MATCH_THRESHOLD - 0.01])


def test_fires_once_per_run():
    assert len(_fired([1.0, 1.0, 1.0])) == 1
