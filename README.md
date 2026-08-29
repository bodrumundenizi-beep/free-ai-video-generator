🎥 AutoVideo-AI
An automated Python-based AI Video Studio that turns text scripts into high-quality, vertical 9:16 short-form videos. Perfect for TikTok, YouTube Shorts, and Instagram Reels.
🚀 Features
Automated Stock Footage: Automatically pulls high-quality clips from Pexels based on your script.
AI Voiceovers: Uses Microsoft Edge-TTS for realistic, high-quality male and female voices.
Smart Captions: Automatically generates and burns synchronized subtitles onto your video using OpenAI Whisper.
Vertical-First: Automatically crops and resizes stock footage to 1080x1920 (9:16) for social media.
GUI Interface: Built-in dashboard for easy script management and rendering.
🛠 Prerequisites
You will need to install the following on your system:
Python 3.10+ (Ensure "Add Python to PATH" is checked during installation).
ImageMagick: REQUIRED for text generation. Note: During installation, you MUST check the box "Install legacy utilities (e.g., convert)".
FFmpeg: Required for audio/video processing.
📦 Quick Setup
Clone the repository:
code
Bash
git clone https://github.com/[YOUR-USERNAME]/[YOUR-REPO-NAME].git
cd [YOUR-REPO-NAME]
Install dependencies:
code
Bash
pip install -r requirements.txt
Get your Pexels API Key:
Sign up for a free account at Pexels API.
Paste your API key into the app interface when prompted.
Run the application:
code
Bash
python vidgen2.py
📝 How to use
Format your script in the application text box as follows:
code
Text
Visual: slow broken computer
Voice: Is your PC booting incredibly slow?

Visual: frustrated man
Voice: Don't smash your monitor just yet!
💡 Tech Stack
MoviePy: For video editing and rendering.
OpenAI Whisper: For speech-to-text and captioning.
Edge-TTS: For natural-sounding AI voices.
Tkinter: For the graphical user interface.
Pexels API: For automated stock footage fetching.
