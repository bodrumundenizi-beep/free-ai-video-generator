"""Output formats: aspect ratio + quality -> frame size, orientation, bitrate."""

RATIO_OPTIONS = ["9:16", "16:9"]
DRAFT = "Draft"   # small and quick, to check timing and layout; no new AI pictures
RESOLUTION_OPTIONS = ["1080p", "720p", DRAFT]

# Aspect ratio + quality -> output frame size.
RESOLUTION_MAP = {
    ("16:9", "1080p"): (1920, 1080),
    ("16:9", "720p"): (1280, 720),
    ("9:16", "1080p"): (1080, 1920),
    ("9:16", "720p"): (720, 1280),
    ("16:9", DRAFT): (640, 360),
    ("9:16", DRAFT): (360, 640),
}

# Bitrate scaled to the chosen quality.
BITRATE_MAP = {"1080p": "8000k", "720p": "5000k", DRAFT: "1200k"}


def resolve_target(aspect: str, resolution: str):
    """(aspect, quality) -> (width, height, orientation, bitrate)."""
    target_w, target_h = RESOLUTION_MAP.get(
        (aspect, resolution), RESOLUTION_MAP[("9:16", "1080p")]
    )
    orientation = "landscape" if aspect == "16:9" else "portrait"
    return target_w, target_h, orientation, BITRATE_MAP.get(resolution, "8000k")
