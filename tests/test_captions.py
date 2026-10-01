import numpy as np
import pytest

from vidgen import captions
from vidgen.captions import (
    COLORS, HOLD, CaptionOptions, Highlight, band_height, cue_states, font_pixels, group_words,
    render_state, to_srt,
)


def words_at(*specs):
    """(start, duration, text) tuples from 'text@start+duration' shorthand."""
    out = []
    for spec in specs:
        text, rest = spec.split("@")
        start, duration = rest.split("+")
        out.append((float(start), float(duration), text))
    return out


FLOWING = words_at("your@0.0+0.3", "PC@0.3+0.3", "is@0.6+0.2", "hoarding@0.8+0.5",
                   "junk@1.3+0.4")


# --- grouping --------------------------------------------------------------------

def test_cues_hold_at_most_three_words():
    cues = group_words(FLOWING)
    assert [c.text for c in cues] == ["your PC is", "hoarding junk"]


def test_cues_respect_the_character_limit():
    cues = group_words(words_at("extraordinarily@0+0.5", "complicated@0.5+0.5", "now@1+0.2"))
    assert [c.text for c in cues] == ["extraordinarily", "complicated now"]
    assert all(len(c.text) <= 18 for c in cues[1:])


def test_a_phrase_is_split_evenly_not_leaving_one_word_stranded():
    cues = group_words(words_at("here@0+0.2", "is@0.2+0.2", "the@0.4+0.2", "fix@0.6+0.3"))
    assert [c.text for c in cues] == ["here is", "the fix"]
    seven = [(i * 0.2, 0.2, "ab") for i in range(7)]
    assert [len(c.words) for c in group_words(seven)] == [3, 2, 2]


def test_a_pause_ends_the_cue():
    cues = group_words(words_at("stop@0+0.3", "here@0.3+0.3", "then@1.2+0.3"))
    assert [c.text for c in cues] == ["stop here", "then"]


def test_one_word_style_gives_single_word_cues():
    cues, _states = captions.plan(FLOWING, CaptionOptions(style="One word"))
    assert all(len(c.words) == 1 for c in cues) and len(cues) == 5


def test_cue_never_runs_into_the_next_one():
    cues = group_words(FLOWING)
    assert cues[0].end <= cues[1].start
    assert cues[-1].end == pytest.approx(1.7 + HOLD)


def test_blank_words_are_ignored_and_nothing_is_empty():
    cues = group_words([(0.0, 0.2, " "), (0.2, 0.2, ""), (0.4, 0.2, "hi")])
    assert [c.text for c in cues] == ["hi"]
    assert group_words([]) == []


# --- states ----------------------------------------------------------------------

def test_states_are_contiguous_inside_a_cue_and_advance():
    cues = group_words(words_at("a@0+0.2", "b@0.3+0.2", "c@0.6+0.2"))
    states = cue_states(cues)
    assert [s.active for s in states] == [0, 1, 2]
    assert states[0].end == states[1].start and states[1].end == states[2].start
    assert states[-1].end == cues[0].end


# --- srt -------------------------------------------------------------------------

def test_srt_format():
    srt = to_srt(group_words(words_at("hello@0+0.4", "world@0.4+0.4", "again@3661.5+0.5")))
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,050\nhello world\n")
    assert "\n2\n01:01:01,500 --> 01:01:02,250\nagain\n" in srt


# --- options ---------------------------------------------------------------------

def test_unknown_options_fall_back_to_defaults():
    options = CaptionOptions.from_cfg({"caption_style": "Wobbly", "caption_color": "Pink"})
    assert options == CaptionOptions(color="Pink")


def test_font_is_larger_in_portrait_and_scales_with_size():
    portrait = font_pixels(1080, 1920, CaptionOptions())
    assert portrait > font_pixels(1920, 1080, CaptionOptions()) * 1080 / 1920
    assert font_pixels(1080, 1920, CaptionOptions(size="Large")) > portrait
    assert font_pixels(1080, 1920, CaptionOptions(style="One word")) > portrait


# --- drawing ---------------------------------------------------------------------

def rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def opaque_colours(image):
    pixels = np.asarray(image)
    return {tuple(c) for c in pixels[pixels[:, :, 3] == 255][:, :3]}


def test_every_highlight_is_a_complete_recipe():
    assert set(COLORS) == {"Yellow", "Green", "Cyan", "Pink", "White", "Black"}
    for highlight in COLORS.values():
        assert isinstance(highlight, Highlight)
        assert all(len(c) == 7 and c.startswith("#") for c in highlight)
        # The spoken word must differ from its neighbours somehow.
        assert (highlight.fill, highlight.stroke) != (highlight.others, "#000000")


def test_black_highlight_inverts_the_spoken_word():
    lit = opaque_colours(render_state(["A", "B"], 0, 400, 60, COLORS["Black"]))
    # A one-word line has no neighbours: black fill, white outline, nothing else.
    alone = np.asarray(render_state(["A"], 0, 400, 60, COLORS["Black"]))
    row = alone[alone.shape[0] // 2]
    solid = row[row[:, 3] == 255][:, :3]
    assert (0, 0, 0) in lit and (255, 255, 255) in lit
    assert tuple(solid[0]) == (255, 255, 255)  # the outline is what you meet first
    assert (solid == (0, 0, 0)).all(axis=1).any()


def main_fill(image, left_half):
    """The most common fully opaque, non-black colour in one half of ``image``."""
    pixels = np.asarray(image)
    half = pixels[:, :pixels.shape[1] // 2] if left_half else pixels[:, pixels.shape[1] // 2:]
    solid = half[(half[:, :, 3] == 255) & (half[:, :, :3].sum(axis=2) > 0)][:, :3]
    colours, counts = np.unique(solid, axis=0, return_counts=True)
    return tuple(int(v) for v in colours[counts.argmax()])


def test_white_highlight_dims_the_other_words():
    # Two equal words, centred: one lands in each half of the image.
    image = render_state(["MM", "MM"], 0, 400, 60, COLORS["White"])
    assert main_fill(image, left_half=True) == (255, 255, 255)
    assert main_fill(image, left_half=False) == rgb(COLORS["White"].others)
    swapped = render_state(["MM", "MM"], 1, 400, 60, COLORS["White"])
    assert main_fill(swapped, left_half=True) == rgb(COLORS["White"].others)


def test_render_state_is_frame_wide_with_the_active_word_coloured():
    image = render_state(["your", "PC", "is"], 1, 720, 54, COLORS["Yellow"])
    assert image.mode == "RGBA" and image.size == (720, band_height(54))
    pixels = np.asarray(image)
    opaque = pixels[pixels[:, :, 3] == 255][:, :3]
    assert (opaque == rgb(COLORS["Yellow"].fill)).all(axis=1).any()
    assert (opaque == (255, 255, 255)).all(axis=1).any()


def test_plain_style_has_no_highlight():
    pixels = np.asarray(render_state(["your", "PC"], 0, 720, 54, None))
    opaque = pixels[pixels[:, :, 3] == 255][:, :3]
    assert not (opaque == rgb(COLORS["Yellow"].fill)).all(axis=1).any()


def test_long_text_is_shrunk_to_stay_inside_the_frame():
    image = render_state(["INCOMPREHENSIBILITIES", "EVERYWHERE"], 0, 400, 80, None)
    alpha = np.asarray(image)[:, :, 3]
    columns = np.flatnonzero(alpha.any(axis=0))
    assert columns[0] > 0 and columns[-1] < 399


# --- the layer -------------------------------------------------------------------

def test_layer_shows_text_during_a_cue_and_nothing_outside():
    pytest.importorskip("moviepy")
    _cues, states = captions.plan(words_at("hello@1.0+0.4", "world@1.4+0.4"), CaptionOptions())
    layer = captions.caption_layer(states, (720, 1280), CaptionOptions(), 5.0)
    assert layer.size[0] == 720 and layer.duration == 5.0
    assert layer.mask.get_frame(1.2).max() == pytest.approx(1.0)
    assert layer.mask.get_frame(0.5).max() == 0.0
    assert layer.mask.get_frame(4.0).max() == 0.0
    # "Lower" keeps the caption in the lower half, clear of the bottom edge.
    top = layer.pos(0)[1]
    assert 1280 * 0.5 < top < 1280 - layer.size[1]


def test_no_words_means_no_layer():
    assert captions.caption_layer([], (720, 1280), CaptionOptions(), 5.0) is None
