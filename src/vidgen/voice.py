"""Neural voiceover through Microsoft Edge's text-to-speech."""

import os

VOICE_MAP = {
    "Neural Male": "en-US-GuyNeural",
    "Neural Female": "en-US-AriaNeural",
    "Happy/Upbeat (Female)": "en-US-JennyNeural",
    "Deep/Narrator (Male)": "en-US-ChristopherNeural",
}


def generate_voiceover(text, filename, log_func, voice_choice):
    voice_code = VOICE_MAP.get(voice_choice, "en-US-GuyNeural")
    log_func(f"🗣️ Generating {voice_choice} Voice: '{text}'")

    # In-process, not "sys.executable -m edge_tts": in a frozen build
    # sys.executable is AIVideoStudio.exe, which has no -m.  Communicate()'s
    # rate/pitch are the same knobs the old --rate/--pitch flags drove.
    # Imported here rather than at module scope because edge_tts pulls in
    # aiohttp, and the window should appear instantly.
    import asyncio
    import edge_tts

    try:
        speaker = edge_tts.Communicate(text, voice_code, rate="+10%", pitch="+5Hz")
        # Safe: this only ever runs on the render worker thread, which has no
        # event loop of its own.
        asyncio.run(speaker.save(filename))
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        raise Exception(f"Voice generation failed! ({exc})") from exc

    if not os.path.exists(filename) or os.path.getsize(filename) == 0:
        raise Exception(
            "Voice generation failed! No audio came back from the voice service "
            "- check your internet connection."
        )
    return filename
