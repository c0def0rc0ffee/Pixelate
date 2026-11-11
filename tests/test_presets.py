"""
<summary>Tests for every built-in preset and the safety and orientation helpers around them.</summary>
<remarks>Each preset is checked for shape, loop length, flash safety (plain and warm tinted) and a
clean GIF round trip. Sleepy presets get the extra dim, red and slow checks that the app relies
on when it drops the brightness for them.</remarks>
"""

import re

import pytest
from PIL import Image

from pixelate.devices import SIZE
from pixelate.presets import (CATEGORIES, LONG_STORY_FRAMES, MAX_FLASHING_AREA, ORIENTATIONS, PRESETS, flashing_fraction,
                              rotate_frames, to_gif, warm_tint)


def solid(v):
    """
    <summary>A 32x32 frame of one grey level.</summary>
    """
    return Image.new("RGB", (SIZE, SIZE), (v, v, v))


def test_detector_catches_strobe():
    """
    <summary>Four full panel flashes a second is reported as all pixels flashing.</summary>
    """
    strobe = [solid(255), solid(0)] * 4  # 4 flashes a second at 8 fps
    assert flashing_fraction(strobe, fps=8) == 1.0


def test_detector_allows_slow_blink():
    """
    <summary>One blink every two seconds is not flashing.</summary>
    """
    blink = [solid(255)] * 8 + [solid(0)] * 8  # one blink every 2 s
    assert flashing_fraction(blink, fps=8) == 0.0


def test_detector_checks_the_loop_seam():
    """
    <summary>Flashes that happen only across the wrap from last frame to first are still caught.</summary>
    """
    # Each frame alternates only across the wrap-around, but at 30 fps that's a strobe.
    assert flashing_fraction([solid(255), solid(0)], fps=30) == 1.0


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.key)
def test_preset_is_well_formed(preset):
    """
    <summary>Every preset has 32x32 RGB frames, a sensible frame count and fps, and a calm loop length.</summary>
    """
    frames = preset.frames()
    assert 1 <= len(frames) <= preset.max_frames <= LONG_STORY_FRAMES
    assert all(f.size == (SIZE, SIZE) and f.mode == "RGB" for f in frames)
    assert 1 <= preset.fps <= 10
    if len(frames) > 2:  # a static preset is just a couple of identical frames
        assert 4 <= preset.seconds <= 30, "loops should be long enough to feel calm"


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.key)
def test_preset_does_not_flash(preset):
    """
    <summary>Every preset passes the flash check, with and without the warm tint.</summary>
    """
    assert flashing_fraction(preset.frames(), preset.fps) <= MAX_FLASHING_AREA
    assert flashing_fraction(warm_tint(preset.frames()), preset.fps) <= MAX_FLASHING_AREA


@pytest.mark.parametrize("preset", [p for p in PRESETS if p.category == "Soothing"], ids=lambda p: p.key)
def test_soothing_presets_are_dim(preset):
    """
    <summary>Soothing presets stay dark on average and suggest a low brightness.</summary>
    """
    # Small bright highlights are fine; the panel as a whole should stay dark.
    from PIL import ImageStat

    brightest = max(sum(ImageStat.Stat(f.convert("L")).mean) for f in preset.frames())
    assert brightest <= 64, f"average level {brightest:.0f}/255"
    assert preset.brightness <= 30


@pytest.mark.parametrize("preset", PRESETS, ids=lambda p: p.key)
def test_gif_round_trips(preset):
    """
    <summary>Every preset encodes to a looping 32x32 GIF under 100 KB that Pillow can read.</summary>
    """
    import io

    data = to_gif(preset.frames(), preset.fps)
    assert len(data) < 100 * 1024
    with Image.open(io.BytesIO(data)) as im:
        assert im.size == (SIZE, SIZE)
        assert im.info["loop"] == 0


@pytest.mark.parametrize("preset", [p for p in PRESETS if p.sleepy], ids=lambda p: p.key)
def test_sleepy_presets_are_dim_red_and_slow(preset):
    """
    <summary>Sleepy presets are dim, red or amber with almost no blue, low brightness and slow.</summary>
    """
    from PIL import ImageStat

    for f in preset.frames():
        r, g, b = ImageStat.Stat(f).mean
        assert r + g + b <= 120, "too bright"
        assert b <= r * 0.25 and g <= r * 0.7, "should be red/amber, not blue"
    assert preset.brightness <= 15
    assert preset.seconds >= 8 or len(preset.frames()) <= 2


def _dot_top_left():
    """
    <summary>A black frame with one red pixel in the top left corner.</summary>
    """
    img = solid(0)
    img.putpixel((0, 0), (255, 0, 0))
    return img


@pytest.mark.parametrize("turned, where", [(0, (0, 0)), (90, (0, SIZE - 1)), (180, (SIZE - 1, SIZE - 1)),
                                           (270, (SIZE - 1, 0))])
def test_rotate_frames_undoes_the_panel_turn(turned, where):
    """
    <summary>The top left pixel lands in the right corner for each mounting angle, and nothing else lights.</summary>
    """
    out = rotate_frames([_dot_top_left()], turned)[0]
    assert out.getpixel(where) == (255, 0, 0)
    assert sum(out.tobytes()) == 255  # the one red pixel is all that is lit


def test_rotate_frames_rejects_odd_angles():
    """
    <summary>Angles other than the four right angles are refused.</summary>
    """
    with pytest.raises(ValueError):
        rotate_frames([solid(0)], 45)


def test_orientations_are_the_four_right_angles():
    """
    <summary>The mounting choices offered cover 0, 90, 180 and 270 degrees once each.</summary>
    """
    assert sorted(d for _, d in ORIENTATIONS) == [0, 90, 180, 270]


def test_preset_keys_are_unique():
    """<summary>Keys name the frame cache, the exported GIF and the saved on-connect setting.</summary>"""
    keys = [p.key for p in PRESETS]
    assert len(keys) == len(set(keys)), "duplicate preset key"
    assert all(re.fullmatch(r"[a-z0-9]+", k) for k in keys), "keys are plain lower-case words"


def test_every_category_is_known():
    """<summary>No preset sits in a category the GUI's drop-down does not list.</summary>"""
    assert set(p.category for p in PRESETS) == set(CATEGORIES)
