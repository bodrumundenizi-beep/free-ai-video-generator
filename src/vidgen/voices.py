"""The voice catalog: who can narrate, and how each persona is tuned.

Every entry is a free Microsoft Edge neural voice. The persona decides the
speed and pitch it is read at, how long its breath pauses are, and whether it
goes through the "studio" chain (pauses + mastering) or the original plain one.

The four original voices are kept, as "Classic": the same speakers as four of
the new entries, read the way 3.0 read them. Anyone who picked one before keeps
exactly the sound they chose. Their IDs are the old dropdown labels, which is
what older settings.json files contain, so those files need no migration.

Pure: no network or audio libraries, so the tests and the window can import it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    id: str  # stored in settings.json
    voice: str  # edge-tts ShortName
    label: str  # what the dropdown shows
    short: str  # what the Home card shows
    category: str
    rate: str
    pitch: str
    pause_scale: float = 1.0  # multiplies the breath-pause lengths
    enhanced: bool = True  # breath pauses + broadcast mastering


_HYPE = dict(category="Tech / Short-Form Hype", rate="+8%", pitch="+2Hz", pause_scale=0.8)
_DEEP = dict(category="Deep / Storyteller", rate="-2%", pitch="-2Hz", pause_scale=1.3)
_PRO = dict(category="Professional / Tutorial", rate="+3%", pitch="+0Hz", pause_scale=1.0)
_ACCENT = dict(category="Accents & Regional", rate="+2%", pitch="+0Hz", pause_scale=1.0)
_CLASSIC = dict(category="Classic (low quality)", rate="+10%", pitch="+5Hz", enhanced=False)

CATALOG: tuple[Persona, ...] = (
    Persona("en-US-GuyNeural", "en-US-GuyNeural",
            "Hype · Guy - US male, fast tech creator", "Guy · Hype", **_HYPE),
    Persona("en-US-JennyNeural", "en-US-JennyNeural",
            "Hype · Jenny - US female, upbeat TikTok hook", "Jenny · Hype", **_HYPE),
    Persona("en-US-SteffanNeural", "en-US-SteffanNeural",
            "Hype · Steffan - US male, crisp and punchy", "Steffan · Hype", **_HYPE),
    Persona("en-US-ChristopherNeural", "en-US-ChristopherNeural",
            "Deep · Christopher - US male, authoritative podcast", "Christopher · Deep", **_DEEP),
    Persona("en-US-EricNeural", "en-US-EricNeural",
            "Deep · Eric - US male, thoughtful narrator", "Eric · Deep", **_DEEP),
    Persona("en-US-RogerNeural", "en-US-RogerNeural",
            "Deep · Roger - US male, calm and mature", "Roger · Deep", **_DEEP),
    Persona("en-US-AriaNeural", "en-US-AriaNeural",
            "Pro · Aria - US female, clear and informative", "Aria · Pro", **_PRO),
    Persona("en-US-AndrewNeural", "en-US-AndrewNeural",
            "Pro · Andrew - US male, warm and friendly", "Andrew · Pro", **_PRO),
    Persona("en-US-AvaNeural", "en-US-AvaNeural",
            "Pro · Ava - US female, modern conversational", "Ava · Pro", **_PRO),
    Persona("en-GB-RyanNeural", "en-GB-RyanNeural",
            "Accent · Ryan - UK male, British documentary", "Ryan · UK", **_ACCENT),
    Persona("en-GB-SoniaNeural", "en-GB-SoniaNeural",
            "Accent · Sonia - UK female, polished British", "Sonia · UK", **_ACCENT),
    # en-AU-WilliamNeural has been retired from the service; this is the same
    # speaker's current model.
    Persona("en-AU-WilliamMultilingualNeural", "en-AU-WilliamMultilingualNeural",
            "Accent · William - Australian male, dynamic", "William · AU", **_ACCENT),
    Persona("Neural Male", "en-US-GuyNeural",
            "Classic · Neural Male (low quality)", "Neural Male (classic)", **_CLASSIC),
    Persona("Neural Female", "en-US-AriaNeural",
            "Classic · Neural Female (low quality)", "Neural Female (classic)", **_CLASSIC),
    Persona("Happy/Upbeat (Female)", "en-US-JennyNeural",
            "Classic · Happy/Upbeat (low quality)", "Upbeat (classic)", **_CLASSIC),
    Persona("Deep/Narrator (Male)", "en-US-ChristopherNeural",
            "Classic · Deep/Narrator (low quality)", "Narrator (classic)", **_CLASSIC),
)

DEFAULT_ID = "en-US-GuyNeural"
_BY_KEY = {p.id: p for p in CATALOG} | {p.label: p for p in CATALOG}


def find(value) -> Persona | None:
    """The persona for an ID or a dropdown label, or None."""
    return _BY_KEY.get(str(value or "").strip())


def persona(value) -> Persona:
    """Like find(), but falls back to the default voice."""
    return find(value) or _BY_KEY[DEFAULT_ID]


def labels() -> list[str]:
    """Dropdown entries, grouped by category, Classic last."""
    return [p.label for p in CATALOG]
