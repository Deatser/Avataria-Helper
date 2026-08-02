# tests/modules/test_trash.py
import cv2
import numpy as np
import pytest

from app.core.template_match import find_all, load_template
from modules.gardener.trash import (MATCH_THRESHOLD, TRASH_KINDS,
                                    accepted, count_by_kind, scan)


# ── Finding every copy, not just the best one ───────────────────────────────

def _scene_with(template, spots, size=(900, 1400)):
    """A noisy background with the template pasted in at each spot."""
    scene = np.full(size, 40, np.uint8)
    h, w = template.shape[:2]
    for x, y in spots:
        scene[y:y + h, x:x + w] = template
    return scene


def test_every_copy_is_found(app=None):
    template = load_template(TRASH_KINDS[0].filenames[0])
    spots = [(100, 100), (600, 300), (1100, 700)]

    hits = find_all(_scene_with(template, spots), template, MATCH_THRESHOLD)

    assert len(hits) == len(spots)
    assert sorted((x, y) for _s, x, y in hits) == sorted(spots)


def test_one_copy_is_reported_once(app=None):
    """Without suppression a single object scores on a whole plateau of
    pixels and would be counted dozens of times."""
    template = load_template(TRASH_KINDS[0].filenames[0])

    hits = find_all(_scene_with(template, [(400, 400)]), template,
                    MATCH_THRESHOLD)

    assert len(hits) == 1


def test_nothing_is_found_on_an_empty_scene():
    template = load_template(TRASH_KINDS[0].filenames[0])
    blank = np.full((600, 800), 40, np.uint8)

    assert find_all(blank, template, MATCH_THRESHOLD) == []


def test_the_limit_caps_a_threshold_set_too_low():
    template = load_template(TRASH_KINDS[0].filenames[0])
    scene = _scene_with(template, [(100, 100), (400, 100), (700, 100)])

    hits = find_all(scene, template, threshold=-1.0, limit=5)

    assert len(hits) == 5


def test_a_template_larger_than_the_scene_finds_nothing():
    template = load_template(TRASH_KINDS[0].filenames[0])
    assert find_all(np.full((10, 10), 40, np.uint8), template, 0.5) == []


# ── Scanning for the kinds the garden knows ─────────────────────────────────

def test_both_kinds_are_on_disk_and_named_for_the_log():
    assert [k.plural for k in TRASH_KINDS] == [
        "сухих кустов", "голубых кустов", "жёлтых кустов",
        "розовых кустов", "жуков", "бабочек"]
    for kind in TRASH_KINDS:
        for filename in kind.filenames:
            assert load_template(filename) is not None, filename


def test_a_scan_finds_each_kind_and_reports_where():
    by_key = {k.key: k for k in TRASH_KINDS}
    small = load_template(by_key['dry_bush'].filenames[0])
    blue  = load_template(by_key['blue_bush'].filenames[0])
    scene = np.full((900, 1400), 40, np.uint8)
    scene[100:100 + small.shape[0], 200:200 + small.shape[1]] = small
    scene[500:500 + small.shape[0], 900:900 + small.shape[1]] = small
    scene[300:300 + blue.shape[0], 600:600 + blue.shape[1]] = blue

    found = scan(screen_gray=scene)
    counts = count_by_kind(found)

    assert counts["dry_bush"] == 2
    assert counts["blue_bush"] == 1
    # n = m + k, over what actually cleared its bar: the other kinds report
    # near misses too, and those are listed but never counted.
    assert len(accepted(found)) == counts["dry_bush"] + counts["blue_bush"]
    # Reported at the middle of the thing, not its corner
    small_hit = next(f for f in found if f.kind.key == "dry_bush"
                      and abs(f.x - (200 + small.shape[1] // 2)) < 2)
    assert abs(small_hit.y - (100 + small.shape[0] // 2)) < 2


def test_a_near_miss_is_reported_but_not_counted():
    """The whole point of the near list: seen in the log, absent from the
    count, so the threshold can be argued about from real numbers."""
    small = load_template(TRASH_KINDS[0].filenames[0])
    scene = np.full((600, 800), 40, np.uint8)
    scene[100:100 + small.shape[0], 200:200 + small.shape[1]] = small

    # A bar nothing can clear turns a real find into a near miss
    found = scan(screen_gray=scene, threshold=1.01, near=0.5)

    assert found, "матч всё равно должен находиться"
    assert all(not f.accepted for f in found)
    assert count_by_kind(found)["dry_bush"] == 0
    assert accepted(found) == []


def test_finds_come_back_best_first():
    small = load_template(TRASH_KINDS[0].filenames[0])
    scene = np.full((600, 800), 40, np.uint8)
    scene[100:100 + small.shape[0], 200:200 + small.shape[1]] = small

    scores = [f.score for f in scan(screen_gray=scene, near=0.4)
              if f.kind.key == "dry_bush"]

    assert scores == sorted(scores, reverse=True)


def test_a_kind_can_have_several_pictures_of_itself():
    """A dry bush is drawn two ways; both are the same kind of litter."""
    by_key = {k.key: k for k in TRASH_KINDS}

    assert by_key["dry_bush"].filenames == (
        "gardener_-1.png", "gardener_0.png", "gardener_1.png",
        "gardener_2.png", "gardener_3.png")
    assert by_key["blue_bush"].filenames == ("gardener_4.png",)
    assert by_key["beetle"].filenames == ("gardener_7.png", "gardener_8.png")


def test_one_object_matching_two_variants_is_counted_once():
    """Otherwise a bush that looks like both is found twice and marked twice.

    Two variants of a like size, laid over each other: that is what one bush
    drawn two ways looks like to the matcher. Stacking every variant of the
    kind would not — they range from 39x45 to 95x63, and a pile of those is
    not one object by any reading.
    """
    first  = load_template("gardener_0.png")   # 49x39
    second = load_template("gardener_1.png")   # 49x35
    scene = np.full((600, 900), 40, np.uint8)
    for template in (first, second):
        h, w = template.shape[:2]
        scene[200:200 + h, 300:300 + w] = template

    found = [f for f in scan(screen_gray=scene, near=0.4)
             if f.kind.key == "dry_bush"]

    assert len(found) == 1


def test_every_kind_has_a_colour_of_its_own():
    """One colour per kind: only one group is highlighted at a time, so the
    colour is free to say which kind rather than which half of it."""
    colours = [k.colour for k in TRASH_KINDS]

    assert len(set(colours)) == len(TRASH_KINDS)


def test_a_kind_can_carry_its_own_threshold():
    """The blue bush matches clean bushes at 81%, so it needs a stricter bar
    than a kind whose real finds sit at 76%."""
    by_key = {k.key: k for k in TRASH_KINDS}

    assert by_key["blue_bush"].threshold == 0.85     # clean bushes hit 81%
    assert by_key["beetle"].threshold == 0.54        # small and rarely crisp
    assert by_key["dry_bush"].threshold == 0.65


def test_each_kind_is_judged_by_its_own_bar():
    by_key = {k.key: k for k in TRASH_KINDS}
    small = load_template(by_key['dry_bush'].filenames[0])
    blue  = load_template(by_key['blue_bush'].filenames[0])
    scene = np.full((900, 1400), 40, np.uint8)
    scene[100:100 + small.shape[0], 200:200 + small.shape[1]] = small
    # A washed-out blue bush: clearly there, but not up to its own 85%
    faded = (blue * 0.5 + np.random.default_rng(1).integers(
        0, 120, blue.shape) * 0.5).astype(np.uint8)
    scene[300:300 + faded.shape[0], 600:600 + faded.shape[1]] = faded

    counts = count_by_kind(scan(screen_gray=scene, near=0.3))

    assert counts["dry_bush"] == 1      # exact copy, over its 72%
    assert counts["blue_bush"] == 0       # blurred one does not reach 85%


def test_counts_cover_every_kind_even_at_zero():
    counts = count_by_kind([])
    assert set(counts) == {k.key for k in TRASH_KINDS}
    assert sum(counts.values()) == 0
