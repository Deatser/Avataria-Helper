# tests/modules/test_leave_watch.py
from app.core.template_match import FINISH_GOLD, FINISH_SILVER, MATCH_THRESHOLD
from modules.ava_dancers.leave_watch import LeaveWatch


# ── Trigger ─────────────────────────────────────────────────────────────────
# _check_target is exercised directly: it is the whole decision, and driving
# it through a running QThread would need the screen to show a reward line.

class _FakeMatch:
    def __init__(self, key, score):
        self.key, self.score, self.label = key, score, key


def _fired(target, matches):
    watch = LeaveWatch(target)
    seen = []
    watch.leave_ready.connect(lambda label, score: seen.append((label, score)))
    watch._check_target(matches)
    return seen


def test_the_chosen_currency_fires_at_the_threshold():
    assert _fired(FINISH_GOLD, [_FakeMatch(FINISH_GOLD, MATCH_THRESHOLD)])


def test_below_the_threshold_nothing_fires():
    assert not _fired(FINISH_GOLD,
                      [_FakeMatch(FINISH_GOLD, MATCH_THRESHOLD - 0.01)])


def test_the_other_currency_is_ignored_however_well_it_matches():
    assert not _fired(FINISH_GOLD, [_FakeMatch(FINISH_SILVER, 1.0)])
    assert not _fired(FINISH_SILVER, [_FakeMatch(FINISH_GOLD, 1.0)])


def test_it_fires_once_per_run():
    watch = LeaveWatch(FINISH_GOLD)
    seen = []
    watch.leave_ready.connect(lambda label, score: seen.append(score))
    matches = [_FakeMatch(FINISH_GOLD, 1.0)]
    watch._check_target(matches)
    watch._check_target(matches)
    assert len(seen) == 1


def test_switching_target_re_arms_the_trigger():
    watch = LeaveWatch(FINISH_GOLD)
    seen = []
    watch.leave_ready.connect(lambda label, score: seen.append(score))
    watch._check_target([_FakeMatch(FINISH_GOLD, 1.0)])
    watch.set_target(FINISH_SILVER)
    watch._check_target([_FakeMatch(FINISH_SILVER, 1.0)])
    assert len(seen) == 2
