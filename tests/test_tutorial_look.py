"""A tutorial's look from the recorded app (5.4, plan F)."""
import os

from PIL import Image, ImageDraw

from vidgen.tutorial import look

REF = os.path.join(os.path.dirname(__file__), "fixtures", "tutorial_ref")
DOCS = os.path.join(os.path.dirname(__file__), "..", "docs")


def test_powerpoint_gets_its_share_button_orange_and_stays_dark():
    got = look.from_frame(Image.open(os.path.join(REF, "s1.png")))
    assert got["accent"] == "#CC6C3C" and got["theme"] == "dark"   # reference: #E8763A


def test_ai_video_studio_gets_its_blue():
    assert look.from_frame(Image.open(os.path.join(DOCS, "create.png")))["accent"] == "#6CCCFC"


def test_a_light_app_is_light_and_text_on_the_accent_reads():
    picture = Image.new("RGB", (800, 500), (245, 245, 245))
    ImageDraw.Draw(picture).rectangle((20, 10, 220, 60), fill=(20, 90, 200))   # a blue button
    got = look.from_frame(picture)
    assert got["theme"] == "light" and got["accent"] and got["on_accent"] == "#FFFFFF"


def test_no_strong_colour_keeps_the_house_accent():
    got = look.from_frame(Image.new("RGB", (400, 300), (30, 30, 30)))
    assert "accent" not in got and got["theme"] == "dark"
