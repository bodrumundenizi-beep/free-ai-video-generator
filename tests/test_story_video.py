"""5.5 story videos: key words in One word captions, the pan, story music and Zoom lines."""
import json

import numpy as np

from vidgen import captions, draft, motion, writer
from vidgen.script import parse_script


def test_key_words_are_the_ones_that_carry_the_line():
    for word in ("SAVINGS", "years,", "1666", "Goliath", "airport."):
        assert captions.key_word(word), word
    for word in ("she", "the", "and", "before", "nothing", "", "..."):
        assert not captions.key_word(word), word


def test_one_word_captions_draw_key_words_bigger_in_colour_on_one_band():
    yellow = captions.COLORS["Yellow"]
    band = captions.band_height(round(60 * captions.KEY_SCALE))
    plain = captions.render_state(["she"], None, 540, 60, None, band)
    key = captions.render_state(["savings"], 0, 540, 75, yellow, band)
    assert plain.size == key.size == (540, band)
    colours = {tuple(px[:3]) for px in np.asarray(key).reshape(-1, 4) if px[3] == 255}
    assert (255, 225, 77) in colours   # the key word is yellow
    colours = {tuple(px[:3]) for px in np.asarray(plain).reshape(-1, 4) if px[3] == 255}
    assert (255, 225, 77) not in colours and (255, 255, 255) in colours


def test_pan_moves_across_the_picture_at_one_zoom():
    assert motion.zoom_range("pan", "image", None, False) == (motion.PAN_ZOOM,) * 2

    class Still:   # a picture whose left half is black and right half white
        size, duration = (90, 160), None

        def get_frame(self, _t):
            frame = np.zeros((160, 90, 3), dtype=np.uint8)
            frame[:, 45:] = 255
            return frame

        def with_duration(self, length):
            return self

    shot = motion.framed_shot(Still(), 2.0, (90, 160), (motion.PAN_ZOOM,) * 2, None, 1)
    start, end = shot.get_frame(0).mean(), shot.get_frame(2.0).mean()
    assert end > start   # it slid from the dark side to the light side


def test_story_scenes_get_their_movement_in_the_script():
    answer = json.dumps({"topic": "scary", "hero": "a man", "scenes": [
        {"voice": "He heard knocking from inside the empty store at night.",
         "who": "main character", "picture": "HERO frozen in an aisle"},
        {"voice": "The parking lot outside was empty and the rain kept falling.",
         "who": "no people", "picture": "an empty parking lot in the rain"}]})
    scenes, _hero, _look = writer.parse_story(answer)
    shown = parse_script(draft.to_script(scenes, False, draft.AI_ALL))
    assert [s.zoom for s in shown] == ["in", "pan"]


def test_story_music_follows_the_look():
    tracks = ["calm - A.mp3", "dramatic - B.mp3", "upbeat - C.mp3"]
    assert draft.pick_track(tracks, draft.STORY_MOODS["Dark comic"]).startswith("dramatic")
    assert draft.pick_track(tracks, draft.STORY_MOODS["Anime film"]).startswith("calm")
    assert draft.pick_track(tracks, draft.STORY_MOODS["TV cartoon"]).startswith("upbeat")
    assert draft.pick_track(tracks, "Story and facts").startswith("dramatic")   # as before
    assert draft.pick_track(tracks, "something else").startswith("calm")


def test_no_pause_where_the_voice_runs_straight_on():
    from vidgen import voice

    rate = 1000
    samples = np.full(1000, 0.5, dtype=np.float32)   # "It's okay, I'm" with no gap at the comma
    samples[700:750] = 0.0                           # a real gap later in the line
    (time, seconds), = voice.settle_pauses(samples, rate, [(0.3, 0.1), (0.75, 0.2)])
    assert 0.7 <= time < 0.75 and seconds == 0.2


def test_pauses_are_cut_at_the_quietest_moment_not_inside_a_soft_word():
    from vidgen import voice

    rate = 1000
    samples = np.full(1000, 0.5, dtype=np.float32)   # speech
    samples[400:450] = 0.0                           # the real gap between two words
    samples[450:500] = 0.05                          # a soft "h" starting the next word
    cut = voice.quietest(samples, rate, 490)         # the timing says the word starts at 0.49
    assert 400 <= cut < 450
    pauses = voice.settle_pauses(samples, rate, [(0.49, 0.1)])
    assert pauses == [(cut / rate, 0.1)]
    paced = voice.insert_pauses(samples, rate, pauses)
    assert len(paced) == 1100
    assert np.all(paced[cut:cut + 100] == 0)         # the pause sits in the gap
    assert np.allclose(paced[cut + 100:cut + 150][10:], samples[cut:cut + 50][10:])


def test_picture_quality_has_deniz_s_words_for_every_speed():
    from gui import base
    from vidgen import imagegen

    assert set(base.AI_SPEED_LABELS) == set(imagegen.SPEEDS)
    assert base.AI_SPEED_LABELS[imagegen.FAST] == "Fast: fast but low res"
    for speed, label in base.AI_SPEED_LABELS.items():   # Settings' hint says the same first
        assert base.AI_SPEED_HINTS[speed].lower().startswith(label.split(": ")[1])
