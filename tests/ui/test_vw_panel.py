# tests/ui/test_vw_panel.py
from app.ui.widgets.vw_panel import VwPanel


# ── Crop offset ──────────────────────────────────────────────────────────────
# _cropped_offset is a plain staticmethod — exercised directly, no QWidget or
# event loop needed for the arithmetic itself.

def test_centred_focus_matches_the_old_evenly_split_crop():
    # 1600px source scaled into a 400px panel: 1200px of overflow, split
    # evenly is -600.
    assert VwPanel._cropped_offset(400, 1600, 0.0) == -600


def test_full_positive_focus_flushes_the_trailing_edge():
    # Right edge of the scaled media lands exactly on the panel's own edge.
    assert VwPanel._cropped_offset(400, 1600, 1.0) == 400 - 1600


def test_full_negative_focus_flushes_the_leading_edge():
    assert VwPanel._cropped_offset(400, 1600, -1.0) == 0


def test_partial_focus_lands_between_the_two_extremes():
    # Positive focus slides the crop towards the trailing edge — the drawn
    # offset moves further negative, from centred towards the focus=1.0 case.
    centred      = VwPanel._cropped_offset(400, 1600, 0.0)
    full_forward = VwPanel._cropped_offset(400, 1600, 1.0)
    biased       = VwPanel._cropped_offset(400, 1600, 0.5)
    assert full_forward < biased < centred


def test_focus_outside_the_valid_range_is_clamped():
    assert VwPanel._cropped_offset(400, 1600, 5.0) == \
        VwPanel._cropped_offset(400, 1600, 1.0)
    assert VwPanel._cropped_offset(400, 1600, -5.0) == \
        VwPanel._cropped_offset(400, 1600, -1.0)


def test_an_axis_that_does_not_overflow_stays_centred_regardless_of_focus():
    # The scaled media is smaller than the panel on this axis — nothing to
    # crop, so focus has nothing to bias.
    assert VwPanel._cropped_offset(400, 300, 1.0) == 50
    assert VwPanel._cropped_offset(400, 300, -1.0) == 50
