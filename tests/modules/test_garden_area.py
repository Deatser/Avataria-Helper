# tests/modules/test_garden_area.py
"""Finding the garden view itself, so the matcher can be pointed at it."""
import numpy as np

from app.core.template_match import load_template
from modules.gardener.garden_area import Area, GARDEN_TEMPLATE, locate


def _screen_with(picture, at, size=(1080, 1920)):
    screen = np.full(size, 40, np.uint8)
    h, w = picture.shape[:2]
    screen[at[1]:at[1] + h, at[0]:at[0] + w] = picture
    return screen


def test_the_garden_is_found_where_it_was_put():
    picture = load_template(GARDEN_TEMPLATE)

    area = locate(screen_gray=_screen_with(picture, (460, 140)))

    # Quarter-size search, so a few pixels either way
    assert abs(area.left - 460) <= 4 and abs(area.top - 140) <= 4
    assert area.score > 0.9
    assert (area.width, area.height) == (picture.shape[1], picture.shape[0])


def test_the_centre_is_the_middle_of_the_view():
    picture = load_template(GARDEN_TEMPLATE)
    h, w = picture.shape[:2]

    area = locate(screen_gray=_screen_with(picture, (200, 100)))

    x, y = area.centre
    assert abs(x - (200 + w // 2)) <= 4
    assert abs(y - (100 + h // 2)) <= 4


def test_a_screen_smaller_than_the_view_finds_nothing():
    assert locate(screen_gray=np.full((200, 200), 40, np.uint8)) is None


def test_a_missing_picture_is_not_an_error():
    assert locate("not_a_real_file.png") is None


def test_the_region_is_the_mss_shape_a_grab_takes():
    area = Area(score=0.9, left=780, top=312, width=1000, height=797)

    assert area.region == {"left": 780, "top": 312,
                           "width": 1000, "height": 797}
