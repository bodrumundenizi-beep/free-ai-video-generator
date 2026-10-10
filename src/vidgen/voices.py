"""The voice catalog: who can narrate, and how each persona is tuned.

Every entry names two speakers: ``local``, a speaker of the offline voice that
runs on this PC (localvoice.py) and is what the app uses, and ``voice``, the
Microsoft Edge online voice the persona began as, kept for anyone who turns
the online voices back on. The persona decides the speed it is read at, how
long its breath pauses are, and whether it goes through the "studio" chain
(pauses + mastering) or the original plain one.

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
    voice: str  # edge-tts ShortName, for the online voices
    label: str  # what the dropdown shows
    short: str  # what the Home card shows
    category: str
    rate: str
    pitch: str
    pause_scale: float = 1.0  # multiplies the breath-pause lengths
    enhanced: bool = True  # breath pauses + broadcast mastering
    local: str = ""  # the offline speaker; "a+b" is an even blend of two


_VIRAL = dict(category="Viral / Shorts", rate="+15%", pitch="+0Hz", pause_scale=0.7)
_HYPE = dict(category="Tech / Short-Form Hype", rate="+8%", pitch="+2Hz", pause_scale=0.8)
_DEEP = dict(category="Deep / Storyteller", rate="-2%", pitch="-2Hz", pause_scale=1.3)
_PRO = dict(category="Professional / Tutorial", rate="+3%", pitch="+0Hz", pause_scale=1.0)
_ACCENT = dict(category="Accents & Regional", rate="+2%", pitch="+0Hz", pause_scale=1.0)
_CLASSIC = dict(category="Classic (low quality)", rate="+10%", pitch="+5Hz", enhanced=False)

CATALOG: tuple[Persona, ...] = (
    Persona("viral-max", "en-US-GuyNeural",
            "Viral · Max - US male, fast scroll-stopper", "Max · Viral",
            local="am_puck+am_adam", **_VIRAL),
    Persona("viral-cole", "en-US-SteffanNeural",
            "Viral · Cole - US male, bold and direct", "Cole · Viral",
            local="am_echo", **_VIRAL),
    Persona("viral-zoe", "en-US-JennyNeural",
            "Viral · Zoe - US female, bright and quick", "Zoe · Viral",
            local="af_jessica+af_nova", **_VIRAL),
    Persona("viral-mia", "en-US-AvaNeural",
            "Viral · Mia - US female, smooth storytime", "Mia · Viral",
            local="af_sky+af_bella", **_VIRAL),
    Persona("en-US-GuyNeural", "en-US-GuyNeural",
            "Hype · Guy - US male, fast tech creator", "Guy · Hype",
            local="am_michael", **_HYPE),
    Persona("en-US-JennyNeural", "en-US-JennyNeural",
            "Hype · Jenny - US female, upbeat TikTok hook", "Jenny · Hype",
            local="af_bella", **_HYPE),
    Persona("en-US-SteffanNeural", "en-US-SteffanNeural",
            "Hype · Steffan - US male, crisp and punchy", "Steffan · Hype",
            local="am_fenrir", **_HYPE),
    Persona("en-US-ChristopherNeural", "en-US-ChristopherNeural",
            "Deep · Christopher - US male, authoritative podcast", "Christopher · Deep",
            local="am_onyx", **_DEEP),
    Persona("en-US-EricNeural", "en-US-EricNeural",
            "Deep · Eric - US male, thoughtful narrator", "Eric · Deep",
            local="am_eric", **_DEEP),
    Persona("en-US-RogerNeural", "en-US-RogerNeural",
            "Deep · Roger - US male, calm and mature", "Roger · Deep",
            local="am_onyx+am_michael", **_DEEP),
    Persona("en-US-AriaNeural", "en-US-AriaNeural",
            "Pro · Aria - US female, clear and informative", "Aria · Pro",
            local="af_heart", **_PRO),
    Persona("en-US-AndrewNeural", "en-US-AndrewNeural",
            "Pro · Andrew - US male, warm and friendly", "Andrew · Pro",
            local="am_liam", **_PRO),
    Persona("en-US-AvaNeural", "en-US-AvaNeural",
            "Pro · Ava - US female, modern conversational", "Ava · Pro",
            local="af_sarah", **_PRO),
    Persona("en-GB-RyanNeural", "en-GB-RyanNeural",
            "Accent · Ryan - UK male, British documentary", "Ryan · UK",
            local="bm_george", **_ACCENT),
    Persona("en-GB-SoniaNeural", "en-GB-SoniaNeural",
            "Accent · Sonia - UK female, polished British", "Sonia · UK",
            local="bf_emma", **_ACCENT),
    # Began as an Australian online voice; the offline voice has no Australian speaker.
    Persona("en-AU-WilliamMultilingualNeural", "en-AU-WilliamMultilingualNeural",
            "Accent · William - UK male, warm storyteller", "William · UK",
            local="bm_fable", **_ACCENT),
    Persona("Neural Male", "en-US-GuyNeural",
            "Classic · Neural Male (low quality)", "Neural Male (classic)",
            local="am_michael", **_CLASSIC),
    Persona("Neural Female", "en-US-AriaNeural",
            "Classic · Neural Female (low quality)", "Neural Female (classic)",
            local="af_heart", **_CLASSIC),
    Persona("Happy/Upbeat (Female)", "en-US-JennyNeural",
            "Classic · Happy/Upbeat (low quality)", "Upbeat (classic)",
            local="af_bella", **_CLASSIC),
    Persona("Deep/Narrator (Male)", "en-US-ChristopherNeural",
            "Classic · Deep/Narrator (low quality)", "Narrator (classic)",
            local="am_onyx", **_CLASSIC),
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
