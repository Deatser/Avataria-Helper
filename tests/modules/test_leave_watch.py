# tests/modules/test_leave_watch.py
from app.core.template_match import FINISH_GOLD, FINISH_SILVER
from modules.ava_dancers.leave_watch import MATCH_THRESHOLD, LeaveWatch


# ── Trigger ─────────────────────────────────────────────────────────────────
# _check_score is exercised directly: it is the whole decision, and driving
# it through a running QThread would need the screen to show the line.

def _fired(target, scores):
    watch = LeaveWatch(0, target)
    seen = []
    watch.leave_ready.connect(lambda label, score: seen.append((label, score)))
    for score in scores:
        watch._check_score(score)
    return seen


def test_fires_at_the_threshold():
    assert _fired(FINISH_GOLD, [MATCH_THRESHOLD])


def test_below_the_threshold_nothing_fires():
    assert not _fired(FINISH_GOLD, [MATCH_THRESHOLD - 0.01])


def test_it_fires_once_per_run():
    seen = _fired(FINISH_GOLD, [1.0, 1.0, 1.0])
    assert len(seen) == 1


def test_switching_target_re_arms_the_trigger():
    watch = LeaveWatch(0, FINISH_GOLD)
    seen = []
    watch.leave_ready.connect(lambda label, score: seen.append(score))
    watch._check_score(1.0)   # fires on gold
    watch.set_target(FINISH_SILVER)
    watch._check_score(1.0)   # fires again on silver
    assert len(seen) == 2
