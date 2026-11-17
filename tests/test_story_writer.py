"""5.5 picture stories: the Smart writer's story answer, read into scenes, a hero and a look."""
import json

import pytest

from vidgen import draft, imagegen, writer
from vidgen.script import parse_script
from vidgen.writer import WriterError
from test_writer import FakeModel, session, no_real_files  # noqa: F401 (fixture)

DOES = ["walks on", "runs off", "hides away", "calls out"]   # neighbours never share 4 words
HERO = "man in his thirties, short black hair, tan skin, navy shirt, grey trousers"


@pytest.fixture(autouse=True)
def one_plot(monkeypatch):
    """One plot per story here (fake plots would ask for the best of 3); tested on its own."""
    monkeypatch.setattr(writer, "PLOTS", 1)


def story(scenes, hero=HERO, look="Painted history"):
    return json.dumps({"hero": hero, "look": look, "scenes": scenes})


def scene(n, hero=True):
    return {"voice": f"Line {n}: {'abcdefghijklmn'[n]} happens {n} times {n * 3} {n * 7}.",
            "picture": (f"The HERO at place {n}, scared face, storm behind" if hero
                        else f"an empty road number {n} at night")}


def test_story_answer_becomes_scenes_hero_and_look():
    scenes, hero, look = writer.parse_story(story([scene(1), scene(2, False), scene(3)]))
    assert hero == HERO and look == "Painted history"
    assert [s.picture for s in scenes] == ["{hero} at place 1, scared face, storm behind",
                                           "an empty road number 2 at night",
                                           "{hero} at place 3, scared face, storm behind"]
    assert all(s.visual and s.voice for s in scenes)


def test_a_look_outside_the_story_looks_falls_back_and_missing_pictures_use_the_line():
    answer = story([{"voice": "Only a line here, with no picture at all."}, scene(2)],
                   look="Realistic photo")
    scenes, _hero, look = writer.parse_story(answer)
    assert look == writer.STORY_LOOKS[0]
    assert scenes[0].picture == "Only a line here, with no picture at all."


@pytest.mark.parametrize("answer", ["", "not json", json.dumps({"hero": "x"}),
                                    json.dumps({"scenes": "no"}), story([]),
                                    story([{"voice": ""}])])
def test_a_broken_story_is_a_writer_error(answer):
    with pytest.raises(WriterError):
        writer.parse_story(answer)


def test_every_story_look_is_an_app_look_and_the_schema_fixes_the_scene_count():
    assert set(writer.STORY_LOOKS) <= set(imagegen.LOOKS)
    schema = writer.story_schema(11)["properties"]
    assert schema["scenes"]["minItems"] == schema["scenes"]["maxItems"] == 11
    assert schema["topic"]["enum"] == list(writer.STORY_TOPICS)
    assert set(writer.STORY_TOPICS.values()) == set(writer.STORY_LOOKS)
    prompt = writer.story_prompt(draft.Audience(), 150, 11)
    assert "HERO" in prompt and "exactly 11 scenes" in prompt


def test_topic_picks_the_look_and_who_puts_the_hero_in():
    answer = json.dumps({"topic": "school", "hero": HERO, "scenes": [
        {"voice": "She found the note in her locker.", "who": "main character",
         "picture": "A teenage girl holding a crumpled note in a school hallway"},
        {"voice": "Her phone lit up again.", "who": "main character",
         "picture": "trembling hands holding a phone, dark bedroom"},
        {"voice": "Across town, her friend laughed.", "who": "other people",
         "picture": "a girl laughing with friends at a cafe"}]})
    scenes, _hero, look = writer.parse_story(answer)
    assert look == "Anime film"
    assert [s.picture for s in scenes] == [
        "{hero} holding a crumpled note in a school hallway",
        "{hero}, trembling hands holding a phone, dark bedroom",
        "a girl laughing with friends at a cafe"]
    scary = json.dumps({"topic": "scary", "hero": HERO, "scenes": [scene(1)]})
    assert writer.parse_story(scary)[2] == "Dark comic"


def test_picture_story_template_writes_a_drawn_story(monkeypatch):
    template = draft.TEMPLATES["Picture story"]
    assert template.story and template.length == "60 s"
    answer = story([scene(i, i % 4 != 0) for i in range(1, 12)])
    plot = json.dumps({"events": ["a happens", "b happens", "c happens", "d happens",
                                  "k happens"]})
    model = FakeModel([plot, answer, answer])   # short fake lines: asked twice
    with session(model) as s:
        result = s.write("a story about a man who gives away his savings at the airport",
                         template.audience, "60 s", ai=draft.AI_ALL, story=True)
    assert result.hero == HERO and result.look == "Painted history"
    assert "picture story" in result.source
    shown = parse_script(result.script)
    assert all(sc.is_ai for sc in shown)
    assert sum("{hero}" in sc.ai_prompt for sc in shown) >= 0.7 * len(shown)
    sent = model.bodies[1]
    assert "HERO" in sent["messages"][0]["content"]


def test_the_plot_comes_first_and_skipped_events_are_found():
    events = writer.parse_plot(json.dumps({"events": [
        "A car follows her at midnight", "She gets off the bus", "She calls 911",
        "A man grabs her", "The police find the car"]}))
    assert len(events) == 5 and writer.parse_plot("broken") == []
    lines = [draft.DraftScene("x", v, i) for i, v in enumerate([
        "Headlights follow her down the empty midnight road.",
        "She gets off the bus and the car stops too.",
        "A man grabs her arm from the dark.",
        "Blue lights: the police find the car at dawn."])]
    assert writer.missing_events(events, lines) == ["She calls 911"]
    assert "A reveal alone is not an ending" in writer.plot_prompt()
    assert "Give nobody a name" in writer.plot_prompt()


def test_a_skipped_event_asks_once_more_and_the_ending_is_never_cut():
    plot = json.dumps({"events": ["alpha", "bravo", "charlie",
                                  "delta", "zulu ending"]})
    first = story([{"voice": f"Nothing much happens in this scene number {n} at all.",
                    "picture": "HERO"} for n in range(11)])
    lines = [f"Then alpha bravo charlie delta occurs again in part {n} of the story now."
             for n in range(10)] + ["And finally the zulu ending closes the whole story."]
    second = story([{"voice": v, "picture": "HERO"} for v in lines])
    model = FakeModel([plot, first, second])
    with session(model) as s:
        result = s.write("an idea", draft.TEMPLATES["Picture story"].audience, "15 s",
                         ai=draft.AI_ALL, story=True)
    assert len(model.bodies) == 3   # plot, story, story again
    assert result.scenes[-1].voice == lines[-1]   # cut to 15 s, the ending kept


def test_repeats_and_the_real_life_switch():
    said = [draft.DraftScene("x", v, i) for i, v in enumerate([
        "She opens it. The note is still there.", "She smiles. The note is still there."])]
    assert writer.repeats(said)
    assert not writer.repeats([draft.DraftScene("x", "She looks at the car again.", 0),
                               draft.DraftScene("x", "Then she looks at the road.", 1)])
    assert "real life" in writer.story_prompt(draft.Audience(), 150, 11, True)
    assert "even the impossible" in writer.story_prompt(draft.Audience(), 150, 11, False)
    assert "no magic" in writer.plot_prompt(True) and "impossible" in writer.plot_prompt(False)


def test_a_story_far_too_short_is_written_again():
    plot = json.dumps({"events": ["a happens", "b happens", "c happens", "d happens",
                                  "k happens"]})
    short = story([scene(1), scene(2)])
    full = story([scene(i) for i in range(1, 12)])
    model = FakeModel([plot, short, full])
    with session(model) as s:
        result = s.write("an idea", draft.TEMPLATES["Picture story"].audience, "30 s",
                         ai=draft.AI_ALL, story=writer.REAL_LIFE)
    assert len(model.bodies) == 3 and len(result.scenes) > 2


def test_a_two_minute_story_is_one_answer_one_scene_per_event():
    plot = json.dumps({"events": [f"event {n} happens" for n in range(20)]})
    whole = story([{"voice": f"Line {n}: then the woman {DOES[n % 4]} at stop "
                             f"{n * 7}.", "who": "main character",
                    "picture": "HERO walks"} for n in range(20)])
    model = FakeModel([plot, whole])
    with session(model) as s:
        result = s.write("an idea", draft.TEMPLATES["Picture story"].audience, "2 min",
                         ai=draft.AI_ALL, story=writer.REAL_LIFE)
    assert len(model.bodies) == 2   # the plot, then the whole story in one answer
    plan, write = model.bodies[0], model.bodies[1]
    assert "20 events in order, 4 for each of" in plan["messages"][0]["content"]
    assert "outline" not in plan["messages"][0]["content"]   # it copied it (1b.2)
    assert plan["response_format"]["json_schema"]["schema"]["properties"]["events"][
        "minItems"] == 20
    assert write["response_format"]["json_schema"]["schema"]["properties"]["scenes"][
        "minItems"] == 20
    asked = write["messages"][-1]["content"]
    assert "Scene 1: event 0 happens" in asked and "Scene 20: event 19 happens" in asked
    assert len(result.scenes) == 20 and result.hero == HERO and result.length == "2 min"


def test_a_long_plot_that_is_a_talk_loop_is_planned_once_more(monkeypatch):
    monkeypatch.setattr(writer, "PLOTS", 3)
    talky = json.dumps({"events": [
        f"The driver says thing number {n} to her." for n in range(20)]})
    good = json.dumps({"events": [
        f"She runs past {n} houses." for n in range(16)] + [
        "She runs to the shop.", "The police come in.", "They arrest the driver.",
        "She goes home safe."]})
    whole = story([{"voice": f"Line {n}: then the woman {DOES[n % 4]} at stop "
                             f"{n * 7}.", "who": "main character",
                    "picture": "HERO walks"} for n in range(20)])
    model = FakeModel([talky, good, whole])
    with session(model) as s:
        s.write("an idea", draft.TEMPLATES["Picture story"].audience, "2 min",
                ai=draft.AI_ALL, story=writer.REAL_LIFE)
        assert s.plot[-2] == "They arrest the driver."
    assert len(model.bodies) == 3   # plot, plot again, story
    assert writer.talking(json.loads(talky)["events"]) == 20
    assert writer.talking(json.loads(good)["events"]) == 0


def test_a_choppy_story_is_written_once_more():
    def lines(voices):
        return [draft.DraftScene("x", v, i) for i, v in enumerate(voices)]
    telegram = lines(["Boy walks into the park. Dog runs off.", "Boy asks clerk. Clerk "
                      "shakes head.", "Boy sits on bench. He waits.", "The boy finds the dog."])
    normal = lines(["The boy walks into the park, and then his dog runs off.",
                    "Rex runs to the boy, and the boy hugs Rex tightly."])
    assert writer.choppy(telegram) and not writer.choppy(normal)   # a name is not "Boy"
    plot = json.dumps({"events": ["a happens", "b happens", "c happens", "d happens",
                                  "k happens"]})
    chop = story([{"voice": f"Boy walks. Dog runs. Boy looks at place number {n} now.",
                   "picture": "HERO"} for n in range(11)])
    full = story([scene(i) for i in range(1, 12)])
    model = FakeModel([plot, chop, full, full])
    with session(model) as s:
        result = s.write("an idea", draft.TEMPLATES["Picture story"].audience, "30 s",
                         ai=draft.AI_ALL, story=writer.REAL_LIFE)
    assert not writer.choppy(result.scenes)


def test_the_scene_map_gives_the_ending_the_last_scenes():
    assert writer.scene_map(["a", "b", "c", "d", "e"], 11).splitlines() == [
        "Scenes 1-3: a", "Scenes 4-5: b", "Scenes 6-7: c", "Scenes 8-9: d", "Scenes 10-11: e"]
    assert writer.scene_map(["a", "b"], 3).splitlines() == ["Scenes 1-2: a", "Scene 3: b"]


def test_events_are_found_by_their_own_words():
    events = ["She walks to the bus stop", "She walks into the bakery", "The baker gives her bread"]
    lines = [draft.DraftScene("x", v, i) for i, v in enumerate([
        "The girl walks home slowly.", "She waits at the stop for the bus.",
        "Then she opens the bakery door.", "The baker smiles and gives her warm bread."])]
    assert writer.event_spots(events, lines) == [1, 2, 3]   # "walks" alone matches nothing


def test_no_example_roles_leak_into_stories_and_the_mood_is_kept():
    for prompt in (writer.story_prompt(draft.Audience(), 150, 11), writer.plot_prompt()):
        assert "the officer" not in prompt and "the driver" not in prompt
        assert "police" not in prompt.replace("no police", "")
        assert "kind idea stays kind" in prompt and "never the one punished" in prompt
        assert "the danger is real" in prompt


def test_every_story_asks_for_plain_english():
    assert "Plain English" in writer.story_prompt(draft.Audience(), 150, 11)
    assert "Plain English" in writer.plot_prompt()


def test_the_best_of_three_plots_is_kept(monkeypatch):
    monkeypatch.setattr(writer, "PLOTS", 3)
    open_end = json.dumps({"events": ["She leaves the bus.", "A car follows her.",
                                      "She walks faster.", "The car comes closer.",
                                      "She looks back again."]})
    loop = json.dumps({"events": ["She says hello.", "He says hi.", "She says why.",
                                  "He says because.", "She says fine, she is safe."]})
    good = json.dumps({"events": ["She leaves the bus.", "A car follows her.",
                                  "She runs into a shop.", "She calls the police.",
                                  "The police arrest the driver and she is safe."]})
    whole = story([scene(i) for i in range(1, 12)])
    model = FakeModel([open_end, loop, good, whole, whole])
    with session(model) as s:
        s.write("a car follows her at night", draft.TEMPLATES["Picture story"].audience,
                "60 s", ai=draft.AI_ALL, story=writer.REAL_LIFE)
        assert s.plot == json.loads(good)["events"]
    assert writer.plot_trouble(json.loads(open_end)["events"], "x", 5) > 0
    assert writer.plot_trouble(json.loads(good)["events"], "x", 5) == 0


def test_a_true_story_is_kept_to_60_s_and_its_facts_cut_to_fit(monkeypatch):
    long_facts = "".join(f"From Wikipedia line {n}: the walls held for weeks.\n"
                         for n in range(300))
    monkeypatch.setattr(writer.webfacts, "facts", lambda idea: (long_facts, ["Fall of X"]))
    plot = json.dumps({"events": [f"event {n} happens in the siege" for n in range(5)]})
    model = FakeModel([plot] + [story([scene(i) for i in range(1, 12)])] * 2)   # short: twice
    with session(model) as s:
        result = s.write("the conquest of a city in 1453", draft.TEMPLATES["Picture story"]
                         .audience, "2 min", ai=draft.AI_ALL, web=True, story=writer.REAL_LIFE)
    assert result.length == "60 s" and "kept to 60 s" in result.source
    assert "5 events in order" in model.bodies[0]["messages"][0]["content"]
    asked = model.bodies[1]["messages"][-1]["content"]
    assert "line 0:" in asked and "line 299:" not in asked   # the opening kept, the rest cut
    assert "Fall of X" in result.source


def ending_answer(lines):
    return json.dumps({"scenes": [{"narration": v, "who": "main character",
                                   "picture": "HERO at the door at night"} for v in lines]})


def test_my_own_story_puts_in_what_must_happen_and_rewrites_only_the_last_4_scenes():
    answer = story([scene(i, i % 4 != 0) for i in range(1, 12)])
    plot = json.dumps({"events": ["a happens", "b happens", "c happens", "d happens",
                                  "k happens"]})
    weak = ending_answer([f"The driver says, 'Wait {n}.'" for n in range(4)])   # scores < 0
    good = ending_answer(["17. 17. Her friend steps out of the car and waves at her.",
                          "Her friend came to bring back the phone she left on the bus.",
                          "She laughs because the driver was her friend all along.",
                          "They walk inside together, and she finally feels safe and then"])
    model = FakeModel([plot, answer, answer, "not json", weak, good])
    with session(model) as s:
        result = s.write("a woman gets off the bus at midnight", draft.TEMPLATES[
            "Picture story"].audience, "60 s", ai=draft.AI_ALL, story=True,
            must="a high speed car chase", ending="the driver was her friend bringing back "
                                                  "her phone")
    assert "car chase" in model.bodies[0]["messages"][-1]["content"]   # the plot hears it
    assert len(model.bodies) == 6   # plot, story twice (short), 3 endings
    voices = [sc.voice for sc in result.scenes]
    assert voices[:7] == [writer.parse_story(answer)[0][n].voice for n in range(7)]
    assert voices[7] == "Her friend steps out of the car and waves at her."   # numbers gone
    assert voices[-1] == "They walk inside together, and she finally feels safe and then."
    assert all("{hero}" in sc.picture for sc in result.scenes[7:])
    assert [sc.sentence for sc in result.scenes] == list(range(11))


def test_without_my_own_story_nothing_more_is_asked_and_a_missing_must_is_trouble():
    plot = ["a man shops", "a man pays", "a man goes home", "a man cooks", "the end is done"]
    without = writer.plot_trouble(plot, "a man shops", 5)
    assert writer.plot_trouble(plot, "a man shops" + writer.MUST + "a robbery happens", 5)         == without + 2   # the must-happen step is missing from the plot
    assert writer.plot_trouble(plot, "a man shops" + writer.MUST + "he cooks dinner", 5)         == without
    assert writer.whole_sentences("She runs. Then the car") == "She runs."
    friend = "the driver was her friend bringing back her phone"
    quotes = [f"The driver says, 'I have your phone {n}.'" for n in range(4)]
    told = ["Her friend steps out of the car.", "She came to bring back the phone.",
            "The driver says, 'You left it on the bus.'", "They laugh and go inside."]
    assert writer.ending_score(told, friend) > writer.ending_score(quotes, friend)


def test_the_users_ending_is_used_even_when_every_try_talks_too_much():
    old = [draft.DraftScene("x", f"Old line {n} of the story.", n, "HERO") for n in range(8)]
    model = FakeModel([ending_answer([f"The driver says, 'Hi {n}.'" for n in range(4)])] * 3)
    with session(model) as s:
        new = s._own_ending("an idea", old, HERO, "her friend")
    assert new[:4] == old[:4] and new[-1].voice == "The driver says, 'Hi 3.'"


def test_cutting_a_story_that_is_too_long_never_cuts_the_users_ending():
    answer = story([scene(i) for i in range(1, 12)])
    plot = json.dumps({"events": ["a happens", "b happens", "c happens", "d happens",
                                  "k happens"]})
    told = [f"Her friend brings back the phone she left, part {n} of the ending."
            for n in range(4)]
    model = FakeModel([plot, answer, answer] + [ending_answer(told)] * 3)
    with session(model) as s:
        result = s.write("an idea", draft.TEMPLATES["Picture story"].audience, "15 s",
                         ai=draft.AI_ALL, story=True, ending="her friend brings back the phone")
    assert [sc.voice for sc in result.scenes[-4:]] == told
    assert len(result.scenes) < 11   # the cut came out of the rest of the story


def test_an_ending_line_that_repeats_the_story_is_dropped():
    old = [draft.DraftScene("x", f"Old line {n} of the story.", n, "HERO") for n in range(8)]
    again = ending_answer(["Old line 2 of the story.", "Her friend brings back the phone.",
                           "She laughs at last.", "They go home together."])
    model = FakeModel([again] * 3)
    with session(model) as s:
        new = s._own_ending("an idea", old, HERO, "her friend brings back the phone")
    assert [sc.voice for sc in new[4:]] == ["Her friend brings back the phone.",
                                            "She laughs at last.", "They go home together."]


def test_a_short_line_gets_3_different_options_and_the_boxes_merge():
    assert writer.too_short("a high-speed car chase") and not writer.too_short("")
    assert not writer.too_short("the police car chases the strange car at high speed "
                                "through the city streets")
    answer = json.dumps({"main": "She steals a car and chases the strange car across town.",
                         "to_main": "The strange car chases her at high speed through town.",
                         "others": "The strange car chases her at high speed through the town."})
    model = FakeModel([answer])
    with session(model) as s:
        options = s.readings("a woman gets off the bus", "Must happen", "a car chase")
    assert options == ["She steals a car and chases the strange car across town.",
                       "The strange car chases her at high speed through town."]   # copy gone
    assert "a car chase" in model.bodies[0]["messages"][-1]["content"]
    with session(FakeModel(["not json"])) as s:
        assert s.readings("idea", "Ending", "her friend") == []   # the boxes still help
    merged = writer.merge_detail("the police car", " the strange car ", "chases it fast.")
    assert merged == ("Who: the police car. To whom or what: the strange car. "
                      "How and why: chases it fast.")
    assert not writer.too_short(merged) and writer.merge_detail("", "", "") == ""


def test_on_a_tie_the_plot_where_more_happens_wins(monkeypatch):
    monkeypatch.setattr(writer, "PLOTS", 3)
    calm = json.dumps({"events": ["She leaves the bus.", "A car follows her.",
                                  "She walks home.", "She looks at the car.",
                                  "The car goes away and she is safe."]})
    busy = json.dumps({"events": ["She leaves the bus.", "A car follows her and chases her.",
                                  "She runs and hides in a shop.", "She calls the police.",
                                  "The police arrest the driver and she is safe."]})
    whole = story([scene(i) for i in range(1, 12)])
    model = FakeModel([calm, busy, whole, whole])
    with session(model) as s:
        s.write("a car follows her at night", draft.TEMPLATES["Picture story"].audience,
                "60 s", ai=draft.AI_ALL, story=writer.REAL_LIFE)
        assert s.plot == json.loads(busy)["events"]   # busy stopped the search: 4 of 5 act
    assert len(model.bodies) == 4   # calm (1 of 5 acts) asked for another plot
    assert writer.plot_trouble(json.loads(calm)["events"], "x", 5) == \
        writer.plot_trouble(json.loads(busy)["events"], "x", 5) == 0
    assert writer.actions(json.loads(calm)["events"]) < writer.actions(json.loads(busy)["events"])


def test_a_line_repeating_the_one_before_is_dropped():
    lines = ["She stops at the elevator and looks around, no one else nearby.",
             "She sees no one else nearby, only the elevator's quiet hum.",
             "She takes the elevator to her floor.",
             "She looks at the door. Then she looks at the window."]
    scenes = [draft.DraftScene("x", v, n) for n, v in enumerate(lines)]
    assert writer.near_repeats(scenes) == {1}
    far = [scenes[0], scenes[2], scenes[1]]   # not next to each other: kept
    assert writer.near_repeats(far) == set()
    answer = story([{"voice": v, "picture": "HERO"} for v in [lines[0], lines[1]] + [
        f"Line {n}: then something new happens at place {n} on road {n * 7}."
        for n in range(9)]])
    plot = json.dumps({"events": ["a happens", "b happens", "c happens", "d happens",
                                  "k happens"]})
    with session(FakeModel([plot, answer, answer])) as s:
        result = s.write("an idea", draft.TEMPLATES["Picture story"].audience, "60 s",
                         ai=draft.AI_ALL, story=True)
    voices = [sc.voice for sc in result.scenes]
    assert lines[0] in voices and lines[1] not in voices
    busy = [draft.DraftScene("x", "The boy lets the dog run into the green park.", 0),
            draft.DraftScene("x", "The dog runs into the green park and hides in a bush.", 1)]
    assert writer.near_repeats(busy) == {1} and writer.actions([busy[1].voice])   # kept


def test_a_picture_inside_a_vehicle_says_the_place_and_pose_first():
    answer = json.dumps({"hero": HERO, "scenes": [
        {"voice": "She jumps into the taxi.", "who": "main character",
         "picture": "HERO in taxi, strange car chasing, both moving fast"},
        {"voice": "He drives home fast.", "who": "main character",
         "picture": "HERO driving an old car, rain on the glass"},
        {"voice": "She walks past a taxi.", "who": "main character",
         "picture": "HERO walking past a taxi at night"}]})
    pictures = [s.picture for s in writer.parse_story(answer)[0]]
    assert pictures == [
        "inside the taxi, back seat, seen through the side window: {hero} sitting, "
        "strange car chasing, both moving fast",
        "inside the old car, at the wheel, seen through the side window: {hero} driving, "
        "rain on the glass",
        "{hero} walking past a taxi at night"]   # not inside: as it was
    assert writer.inside_first("{hero} in carpark at night") == "{hero} in carpark at night"


def test_once_in_a_taxi_she_stays_inside_until_she_gets_out():
    lines = [("She steps into the taxi and shuts the door.", "HERO entering the taxi"),
             ("The taxi swerves around a truck.", "HERO leaning forward, taxi cornering"),
             ("She yells, 'We need to get out of here!'", "HERO shouting, driver turning"),
             ("She jumps out and runs to the police.", "HERO running, police station ahead"),
             ("She waits at the station.", "HERO sitting on a bench")]
    answer = json.dumps({"hero": HERO, "scenes": [
        {"voice": v, "who": "main character", "picture": p} for v, p in lines]})
    pictures = [s.picture for s in writer.parse_story(answer)[0]]
    inside = "inside the taxi, back seat, seen through the side window: "
    assert pictures == ["{hero} entering the taxi",
                        inside + "{hero} leaning forward, taxi cornering",
                        inside + "{hero} shouting, driver turning",
                        "{hero} running, police station ahead",
                        "{hero} sitting on a bench"]


def test_an_ending_whose_pictures_copy_the_words_loses_and_both_use_one_picture_rule():
    old = [draft.DraftScene("x", f"Old line {n} of the story.", n, "HERO") for n in range(8)]
    lines = ["Her brother steps out of the car.", "He came to warn her about the thief.",
             "The police catch the thief at the corner.", "She hugs her brother in the rain."]
    copies = json.dumps({"scenes": [{"narration": v, "who": "other people", "picture": v}
                                    for v in lines]})
    drawn = json.dumps({"scenes": [{"narration": v, "who": "other people",
                                    "picture": f"a wet street at night, police lights, scene {n}"}
                                   for n, v in enumerate(lines)]})
    with session(FakeModel([copies, drawn, copies])) as s:
        new = s._own_ending("an idea", old, HERO, "her brother came to warn her")
    assert new[4].picture == "a wet street at night, police lights, scene 0"
    assert writer.ENDING_RULE in writer.ending_prompt()
    assert writer.PICTURE_RULE in writer.ending_prompt() and \
        writer.PICTURE_RULE in writer.story_prompt(draft.Audience(), 150, 11)
