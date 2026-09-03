# AutoVideo-AI Creator

A professional-grade automated video generation tool that uses Python to turn written scripts into fully edited, high-quality videos.

##  Features
- **Automated Stock Footage:** Automatically fetches relevant video clips from Pexels API.
- **Neural AI Voices:** Uses Microsoft Edge-TTS for high-quality, natural-sounding male and female voices.
- **Smart Script Parser:** Automatically reads your script line-by-line using `Visual:` and `Voice:` tags.
- **GUI Dashboard:** Built-in Windows application to handle API keys, voice selection, and rendering status.
- **End-to-End Automation:** Fetches, generates, matches duration, and stitches everything into a final `.mp4`.

##  Requirements
1. **Python 3.10+** installed on your system.
2. **FFmpeg:** Ensure FFmpeg is installed and added to your System PATH (required for video processing).
------------------------------------------------------------------------------------------------
3. moviepy
4. requests
5. gTTS
6. edge-tts

To install these run these command ONE BY ONE on the python terminal or vs code terminal

pip install moviepy

pip install requests

pip install gTTS

pip install edge-tts

##  Quick Setup
1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/your-repo-name.git
   cd your-repo-name

## AI Script Generation Prompt

You can use ChatGPT, Claude, or Gemini to automatically write scripts formatted specifically for this app. Copy and paste the prompt template below into your AI of choice:

---

> **Prompt:**
> 
> "You are an expert short-form video scriptwriter for TikTok and YouTube Shorts.
> 
> I need a 45-second high-retention script about: **[INSERT TOPIC HERE]**
> 
> Strict Formatting Rules:
> 1. Output ONLY pairs of lines starting with `Visual:` and `Voice:`.
> 2. Do NOT include scene numbers, timestamps, markdown asterisks, or extra conversational text.
> 3. `Visual:` must contain 2 to 4 simple, searchable stock video keywords that can be found on Pexels (e.g., 'typing on laptop keyboard', 'neon lightning bolt', 'frustrated person at computer').
> 4. `Voice:` must contain punchy, engaging spoken dialogue (no emojis, no stage directions).
> 5. Create 6 to 8 scene pairs (roughly 100-120 words total).
> 
> Example output format:
> Visual: slow broken laptop
> Voice: Is your computer taking forever to start up?
> 
> Visual: hands typing on keyboard
> Voice: Here is a simple setting tweak to fix it in seconds."

---

### How to use:
1. Replace `[INSERT TOPIC HERE]` with your video idea (e.g., *'3 hidden iPhone camera features'* or *'How to clear cache on Windows'*).
2. Copy the AI's exact text output.
3. Paste it directly into the **AutoVideo-AI** script box and click **Render Full Video**!
