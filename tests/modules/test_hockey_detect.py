# tests/modules/test_hockey_detect.py
"""Finding defenders by the one thing every avatar shares — a red helmet."""
import cv2
import numpy as np

from app.core.config import HockeyConfig
from app.core.template_match import TEMPLATES_DIR
from modules.hockey.detect import HELMET_TEMPLATES, HelmetDetector
from modules.hockey.rink_area import from_config

RED = (0, 0, 255)   # BGR, straight into the low hue band

# Rink at (1000, 500), 200x200. Row 0 covers frame rows 20..80, row 1
# covers 120..180 — far enough apart that nothing leaks between them.
_ROWS = [{"y": 550, "height": 60}, {"y": 650, "height": 60}]


def _config(**overrides) -> HockeyConfig:
    cfg = HockeyConfig()
    cfg.rink_left, cfg.rink_top = 1000, 500
    cfg.rink_width, cfg.rink_height = 200, 200
    cfg.lanes = list(_ROWS)
    # Off unless a test is about it: a painted rectangle looks nothing like
    # a helmet photo, so leaving the gate on would make every colour and
    # shape test really a test of the photo score.
    cfg.player_match_min = 0.0
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _detector(cfg):
    return HelmetDetector(from_config(cfg), cfg)


def _frame(cfg) -> np.ndarray:
    return np.zeros((cfg.rink_height, cfg.rink_width, 3), np.uint8)


def _paint(frame, left, top, width, height):
    frame[top:top + height, left:left + width] = RED


def _one(found: dict, row: int):
    """The single defender a row was expected to hold, or None. Rows report
    a list — several defenders can share one — so tests that painted exactly
    one say so here rather than indexing blindly."""
    hits = found[row]
    assert len(hits) <= 1, hits
    return hits[0] if hits else None


def test_a_helmet_is_found_at_its_own_centre():
    cfg = _config()
    frame = _frame(cfg)
    _paint(frame, left=60, top=40, width=20, height=20)

    found = _detector(cfg).scan(frame)

    assert found[1] == []                        # nothing painted in row 1
    hit = _one(found, 0)
    assert abs(hit.x - (1000 + 70)) <= 2         # frame x 60..79 → centre 70
    assert abs(hit.y - (500 + 50)) <= 2
    assert hit.area == 400


def test_an_empty_row_is_reported_as_empty_not_as_an_error():
    cfg = _config()

    assert _detector(cfg).scan(_frame(cfg)) == {0: [], 1: []}


def test_a_speck_too_small_to_be_a_helmet_is_ignored():
    cfg = _config()
    frame = _frame(cfg)
    _paint(frame, left=60, top=40, width=10, height=10)   # 100px, min is 150

    assert _detector(cfg).scan(frame)[0] == []


def test_a_red_line_across_a_row_is_not_a_helmet():
    """Area alone would accept it — a 200x6 line is 1200px, right in the
    middle of the range. Shape is what tells the two apart."""
    cfg = _config()
    frame = _frame(cfg)
    _paint(frame, left=0, top=44, width=200, height=6)

    assert _detector(cfg).scan(frame)[0] == []


def test_several_defenders_in_one_row_are_all_reported():
    """A row can hold more than one (2026-08-09), so it is a list, not a
    winner — reporting only the best would hide a defender a shot has to
    clear."""
    cfg = _config()
    frame = _frame(cfg)
    _paint(frame, left=10, top=30, width=20, height=20)
    _paint(frame, left=80, top=30, width=20, height=20)
    _paint(frame, left=150, top=30, width=20, height=20)

    hits = _detector(cfg).scan(frame)[0]

    assert [hit.x - 1000 for hit in hits] == [20, 90, 160]   # left to right


def test_each_row_is_searched_on_its_own():
    cfg = _config()
    frame = _frame(cfg)
    _paint(frame, left=20, top=40, width=20, height=20)
    _paint(frame, left=150, top=140, width=20, height=20)

    found = _detector(cfg).scan(frame)

    assert abs(_one(found, 0).x - (1000 + 30)) <= 2
    assert abs(_one(found, 1).x - (1000 + 160)) <= 2


def test_a_row_calibrated_off_the_rink_is_skipped_not_crashed():
    cfg = _config(lanes=[{"y": 100, "height": 60}])

    assert _detector(cfg).scan(_frame(cfg)) == {0: []}


def test_scan_all_finds_helmets_before_any_row_exists():
    cfg = _config(lanes=[])
    frame = _frame(cfg)
    _paint(frame, left=20, top=40, width=20, height=20)
    _paint(frame, left=150, top=140, width=20, height=20)

    hits = _detector(cfg).scan_all(frame)

    assert sorted(hit.y for hit in hits) == [550, 650]


# ── Static marking mask ──────────────────────────────────────────────────

def _run_learning(detector, cfg, frames=80):
    """Feed the detector a moving helmet over a fixed red line, which is the
    situation the mask exists for."""
    for i in range(frames):
        frame = _frame(cfg)
        _paint(frame, left=0, top=44, width=200, height=6)        # the line
        _paint(frame, left=10 + i, top=30, width=30, height=30)   # moving
        detector.scan(frame)


def test_a_helmet_crossing_a_line_is_lost_without_the_mask():
    cfg = _config()
    frame = _frame(cfg)
    _paint(frame, left=0, top=44, width=200, height=6)
    _paint(frame, left=60, top=30, width=30, height=30)   # sits on the line

    # Merged into one 200-wide blob, which is no helmet shape at all.
    assert _detector(cfg).scan(frame)[0] == []


def test_the_mask_keeps_a_helmet_crossing_a_line():
    cfg = _config(static_mask=True)
    detector = _detector(cfg)
    _run_learning(detector, cfg)

    frame = _frame(cfg)
    _paint(frame, left=0, top=44, width=200, height=6)
    _paint(frame, left=60, top=30, width=30, height=30)
    hits = detector.scan(frame)[0]

    assert detector.static_ready()
    assert hits
    # frame x 60..89 → centre 75; the line splits the blob in two, and both
    # halves share that centre.
    assert all(abs(hit.x - (1000 + 75)) <= 2 for hit in hits)


def test_the_mask_is_not_ready_until_it_has_seen_enough():
    cfg = _config(static_mask=True)
    detector = _detector(cfg)

    _run_learning(detector, cfg, frames=10)

    assert not detector.static_ready()
    assert detector.static_progress() == (10, 80)


def test_a_detector_without_the_mask_is_ready_immediately():
    assert _detector(_config()).static_ready()


# ── Scoring a candidate against the helmet photos ────────────────────────

def _helmet_photo():
    """The real helmet crop, in colour. Read the same way the detector's own
    grayscale copy is, so pasting it in scores against itself."""
    return cv2.imread(str(TEMPLATES_DIR / HELMET_TEMPLATES[0]),
                      cv2.IMREAD_COLOR)


def _config_with_photo_room(**overrides) -> HockeyConfig:
    """A rink big enough for a whole helmet, with one row over it."""
    cfg = _config(**overrides)
    cfg.rink_width, cfg.rink_height = 400, 400
    cfg.lanes = [{"y": 580, "height": 120}]   # frame rows 20..140
    return cfg


def _with_photo(factor: float = 1.0, left: int = 60, top: int = 30):
    photo = _helmet_photo()
    if factor != 1.0:
        photo = cv2.resize(photo, (int(photo.shape[1] * factor),
                                   int(photo.shape[0] * factor)))
    frame = np.zeros((400, 400, 3), np.uint8)
    frame[top:top + photo.shape[0], left:left + photo.shape[1]] = photo
    return frame


def test_a_real_helmet_photo_scores_high():
    cfg = _config_with_photo_room(player_match_min=0.0)

    hit = _one(_detector(cfg).scan(_with_photo()), 0)

    assert hit is not None
    assert hit.match > 0.8


def test_a_helmet_scores_high_at_a_size_no_template_was_cropped_at():
    """The rink is drawn in perspective — boards measured 439px wide on the
    top row and 635 on the bottom — so the far rows show a noticeably
    smaller sprite than any template. Matching at one fixed size scored them
    so low they never passed the gate at all (row 5 seen in 0% of frames,
    2026-08-09)."""
    for factor in (0.6, 1.4):
        cfg = _config_with_photo_room(player_match_min=0.0)

        hit = _one(_detector(cfg).scan(_with_photo(factor)), 0)

        assert hit is not None, factor
        assert hit.match > 0.7, (factor, hit.match)


def test_a_plain_red_square_does_not_look_like_a_helmet():
    cfg = _config_with_photo_room(player_match_min=0.0)
    frame = np.zeros((400, 400, 3), np.uint8)
    frame[40:80, 60:100] = RED

    hit = _one(_detector(cfg).scan(frame), 0)

    assert hit is not None       # found by colour...
    assert hit.match < 0.5       # ...but nothing like a helmet


def test_the_photo_gate_drops_red_that_is_not_a_helmet():
    cfg = _config_with_photo_room(player_match_min=0.6)
    frame = np.zeros((400, 400, 3), np.uint8)
    frame[40:80, 60:100] = RED

    assert _detector(cfg).scan(frame)[0] == []


def test_the_photo_gate_keeps_a_real_helmet():
    cfg = _config_with_photo_room(player_match_min=0.6)

    assert _detector(cfg).scan(_with_photo())[0]


def _scuffed_photo(sigma: float = 25.0):
    """The sprite as the game actually hands it over: the same helmet, a
    little different every frame. Enough to move the photo score around by
    a fifth, which is what a fixed threshold turns into a flicker."""
    photo = _helmet_photo()
    frame = _with_photo().astype(np.int16)
    box = frame[30:30 + photo.shape[0], 60:60 + photo.shape[1]]
    box += np.random.default_rng(3).normal(0, sigma, box.shape).astype(np.int16)
    return np.clip(frame, 0, 255).astype(np.uint8)


def test_a_helmet_the_model_is_waiting_for_survives_a_weak_score():
    """A real defender's photo score swings between roughly 0.63 and 0.89
    frame to frame, so a threshold anywhere in that band makes the same
    defender appear and vanish at random — which starves the track that was
    following it. Where a track already expects somebody, weaker evidence is
    enough (2026-08-10)."""
    frame = _scuffed_photo()
    cfg = _config_with_photo_room(player_match_min=0.9)

    without = _detector(cfg).scan(frame)
    assert without[0] == [], "the scuffed sprite was meant to fall through"

    seen = _detector(cfg).scan_all_debug(frame)
    found = _detector(cfg).scan(frame, {0: [seen[0].x]})

    assert found[0], "a helmet the model was waiting for was thrown away"


def test_but_not_anything_red_that_happens_to_be_expected():
    """The relief is a lower bar, not an open door: a track pointing at a
    patch of scenery must not turn it into a defender and feed itself."""
    cfg = _config_with_photo_room(player_match_min=0.6)
    frame = np.zeros((400, 400, 3), np.uint8)
    frame[40:80, 60:100] = RED

    _found, candidates = _detector(cfg).scan_debug(frame)
    assert candidates and candidates[0].match < 0.4

    found = _detector(cfg).scan(frame, {0: [candidates[0].x]})

    assert found[0] == []


def test_a_helmet_that_has_not_moved_in_a_while_is_taken_on_trust():
    """A row with two standing defenders reported one of them, in every
    frame, for a whole run — the second player's avatar simply scored under
    the gate (2026-08-10). A helmet-shaped blob that has been in the same
    place for a second and a half cannot be the puck, cannot be an animation
    frame, and cannot be anywhere else next time."""
    cfg = _config_with_photo_room(player_match_min=0.9)
    detector = _detector(cfg)
    frame = _scuffed_photo()

    assert detector.scan(frame)[0] == []       # under the gate at first

    for _ in range(60):
        found = detector.scan(frame)

    assert found[0], "a defender that never moved was never accepted"


def test_but_red_scenery_sitting_still_is_still_not_a_defender():
    """The relief is against the gate being too strict for an avatar, not
    against it having a job."""
    cfg = _config_with_photo_room(player_match_min=0.6)
    detector = _detector(cfg)
    frame = np.zeros((400, 400, 3), np.uint8)
    frame[40:80, 60:100] = RED

    for _ in range(60):
        found = detector.scan(frame)

    assert found[0] == []


# ── A defender's own jersey is not a defender ────────────────────────────

def _stacked_config(**overrides) -> HockeyConfig:
    """Two rows 50px apart with a body outline that reaches from one into
    the other — which is how the real rink is calibrated."""
    cfg = _config(**overrides)
    cfg.rink_width, cfg.rink_height = 300, 300
    cfg.lanes = [{"y": 550, "height": 44, "body_w": 50, "body_h": 130,
                  "body_dy": -20},
                 {"y": 600, "height": 50, "body_w": 50, "body_h": 130,
                  "body_dy": -20}]
    return cfg


def test_a_jersey_below_a_helmet_is_not_a_defender_of_its_own():
    """A helmet is the topmost red of its sprite. Measured on the real rink:
    a helmet at (1373, 601) in row 2 and its chest at (1366, 656), 7px
    across and 55px down — which lands in row 3's strip and was reported as
    a defender standing in an empty row for a whole run (2026-08-10).

    Overlap suppression cannot catch this: the chest sits *below* the
    helmet, not on it, so the two boxes never touch.
    """
    cfg = _stacked_config()
    frame = np.zeros((300, 300, 3), np.uint8)
    _paint(frame, left=60, top=38, width=26, height=24)    # helmet, row 1
    # Its chest: inside row 2's strip, but well off row 2's own line, the
    # way the measured one sat 20px under row 3's.
    _paint(frame, left=62, top=104, width=24, height=20)

    found = _detector(cfg).scan(frame)

    assert len(found[0]) == 1
    assert found[1] == []


def test_and_a_real_defender_in_the_row_below_still_is():
    """The rule is narrow on purpose — a third of a body's width — because
    two real defenders in neighbouring rows do line up now and then."""
    cfg = _stacked_config()
    frame = np.zeros((300, 300, 3), np.uint8)
    _paint(frame, left=60, top=38, width=26, height=24)     # row 1
    _paint(frame, left=160, top=88, width=26, height=24)    # row 2, clear

    found = _detector(cfg).scan(frame)

    assert len(found[0]) == 1
    assert len(found[1]) == 1


def test_even_directly_underneath_when_he_is_on_his_row_s_line():
    """A helmet holds its height — measured across a run, the rows read
    572–574, 601–605, 680–681, 738–740 — and a jersey lands wherever the
    sprite puts it. Without that test the rule fires the other way round and
    takes out a standing defender every time a patrol passes above him,
    which is exactly where a shot most needs to know better (2026-08-10).
    """
    cfg = _stacked_config()
    frame = np.zeros((300, 300, 3), np.uint8)
    _paint(frame, left=60, top=38, width=26, height=24)     # row 1
    _paint(frame, left=62, top=88, width=26, height=24)     # row 2, beneath

    found = _detector(cfg).scan(frame)

    assert len(found[0]) == 1
    assert len(found[1]) == 1


def test_and_sitting_still_does_not_let_a_jersey_back_in():
    """Standing still is exactly what a jersey does, so the steady rule has
    to be applied before this one and not after."""
    cfg = _stacked_config(player_match_min=0.9)
    frame = np.zeros((300, 300, 3), np.uint8)
    _paint(frame, left=60, top=38, width=26, height=24)
    _paint(frame, left=62, top=92, width=24, height=20)
    detector = _detector(cfg)

    for _ in range(60):
        found = detector.scan(frame)

    assert found[1] == []


def test_a_helmet_expected_somewhere_else_gets_no_help():
    cfg = _config_with_photo_room(player_match_min=0.9)

    found = _detector(cfg).scan(_scuffed_photo(), {0: [9999.0]})

    assert found[0] == []


def test_a_rejected_candidate_is_still_reported_for_the_snapshot():
    """Seeing what was thrown away, and how narrowly, is what the photo
    threshold gets tuned against."""
    cfg = _config_with_photo_room(player_match_min=0.6)
    frame = np.zeros((400, 400, 3), np.uint8)
    frame[40:80, 60:100] = RED

    found, candidates = _detector(cfg).scan_debug(frame)

    assert found[0] == []
    assert len(candidates) == 1
    assert candidates[0].accepted is False


# ── Overlapping rows ─────────────────────────────────────────────────────
# Strips have to overlap: the defenders themselves overlap, so a strip drawn
# tightly enough to clear its neighbours would clip the helmets it exists to
# hold. Each helmet still has to end up in exactly one row.

def _overlapping_config(**overrides) -> HockeyConfig:
    """Rows 40px apart with 100px strips — the shape marked live on
    2026-08-09, where every strip covered two neighbours."""
    cfg = _config(**overrides)
    cfg.rink_width, cfg.rink_height = 200, 300
    cfg.lanes = [{"y": 560, "height": 100},    # frame rows 10..110
                 {"y": 600, "height": 100},    # frame rows 50..150
                 {"y": 640, "height": 100}]    # frame rows 90..190
    return cfg


def test_one_helmet_belongs_to_exactly_one_row():
    cfg = _overlapping_config()
    frame = np.zeros((300, 200, 3), np.uint8)
    _paint(frame, left=60, top=90, width=20, height=20)   # centre y 600

    found = _detector(cfg).scan(frame)

    assert [index for index, hits in found.items() if hits] == [1]


def test_a_helmet_goes_to_the_row_whose_centre_is_nearest():
    cfg = _overlapping_config()
    frame = np.zeros((300, 200, 3), np.uint8)
    _paint(frame, left=60, top=125, width=20, height=20)  # centre y 635

    found = _detector(cfg).scan(frame)

    assert found[1] == []
    assert found[2]                  # row 3's centre (640) is 5px away


def test_every_row_still_gets_its_own_defender():
    cfg = _overlapping_config()
    frame = np.zeros((300, 200, 3), np.uint8)
    _paint(frame, left=10,  top=50,  width=20, height=20)   # y 560, row 1
    _paint(frame, left=80,  top=90,  width=20, height=20)   # y 600, row 2
    _paint(frame, left=150, top=130, width=20, height=20)   # y 640, row 3

    found = _detector(cfg).scan(frame)

    assert [_one(found, i).x - 1000 for i in range(3)] == [20, 90, 160]


def test_a_helmet_outside_every_strip_belongs_to_no_row():
    cfg = _overlapping_config()
    frame = np.zeros((300, 200, 3), np.uint8)
    _paint(frame, left=60, top=250, width=20, height=20)   # below every row

    found, candidates = _detector(cfg).scan_debug(frame)

    assert all(hits == [] for hits in found.values())
    assert candidates == []          # blanked out before components ran
