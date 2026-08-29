import os
import math
import requests
import threading
import subprocess
import sys
import tkinter as tk
import re
from tkinter import ttk, messagebox, scrolledtext

# MoviePy v2
from moviepy import VideoFileClip, AudioFileClip, concatenate_videoclips

# --- 1. CORE LOGIC ---
def download_stock_video(api_key, query, filename, log_func):
    log_func(f"🎬 [{query}] Searching Pexels for stock footage...")
    headers = {"Authorization": api_key}
    url = f"https://api.pexels.com/videos/search?query={query}&per_page=3"
    
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise Exception("Failed to connect to Pexels API. Check your API key.")
        
    data = response.json()
    if 'videos' in data and len(data['videos']) > 0:
        video_files = data['videos'][0]['video_files']
        video_url = video_files[0]['link'] 
        
        log_func(f"⬇️ [{query}] Downloading video...")
        r = requests.get(video_url, stream=True)
        with open(filename, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk: f.write(chunk)
        return filename
    else:
        raise Exception(f"No stock video found for query: '{query}'")


def generate_voiceover(text, filename, log_func, voice_choice):
    # edge-tts names: Guy is Male, Aria is Female
    voice_code = "en-US-GuyNeural" if voice_choice == "Male" else "en-US-AriaNeural"
    
    log_func(f"🗣️ Generating {voice_choice} Voice: '{text}'")
    
    cmd = [sys.executable, "-m", "edge_tts", "--voice", voice_code, "--text", text, "--write-media", filename]
    result = subprocess.run(cmd, capture_output=True)
    
    if result.returncode != 0:
        raise Exception("Voice generation failed! Make sure you ran 'pip install edge-tts'.")
    return filename


# --- 2. MAIN PROCESS (RUNS IN BACKGROUND THREAD) ---
def create_video_thread():
    try:
        btn_generate.config(state=tk.DISABLED)
        
        api_key = api_entry.get().strip()
        voice_choice = voice_var.get()
        script_text = script_box.get("1.0", tk.END).strip()
        
        if not api_key: raise Exception("API Key cannot be blank.")
        if not script_text: raise Exception("Script cannot be blank.")
        
        # --- NEW SCRIPT PARSING LOGIC ---
        script_scenes = []
        current_visual = None
        
        # Read the script line by line
        for line_num, line in enumerate(script_text.split("\n")):
            line = line.strip()
            
            # Skip empty lines (like pressing enter twice between scenes)
            if not line:
                continue
                
            # Check if the line starts with "Visual:" (case-insensitive, allows spaces)
            if re.match(r"(?i)^visual\s*:", line):
                # Cut out the "Visual:" part and save just the keyword
                current_visual = re.sub(r"(?i)^visual\s*:", "", line).strip()
                
            # Check if the line starts with "Voice:"
            elif re.match(r"(?i)^voice\s*:", line):
                if current_visual is None:
                    raise Exception(f"Error near Line {line_num + 1}: Found a 'Voice:' but missing the 'Visual:' before it!")
                
                # Cut out the "Voice:" part and save the speech
                current_voice = re.sub(r"(?i)^voice\s*:", "", line).strip()
                
                # We have a matching pair! Add them to the timeline and reset
                script_scenes.append({"visual": current_visual, "voice": current_voice})
                current_visual = None 
            else:
                # If they wrote text without 'Visual:' or 'Voice:'
                raise Exception(f"Error on Line {line_num + 1}: Every line must begin with 'Visual:' or 'Voice:'. Please fix!")
                
        if len(script_scenes) == 0:
            raise Exception("No valid scenes were found. Please use the exact Visual: / Voice: formatting.")
        # ---------------------------------

        log_box.insert(tk.END, "🚀 Starting video generation sequence...\n")
        
        final_clips = []
        source_clips = []
        
        for index, scene in enumerate(script_scenes):
            video_file = f"temp_vid_{index}.mp4"
            audio_file = f"temp_aud_{index}.mp3"
            
            log_print = lambda msg: update_log(msg)
            
            download_stock_video(api_key, scene['visual'], video_file, log_print)
            generate_voiceover(scene['voice'], audio_file, log_print, voice_choice)
            
            video_clip = VideoFileClip(video_file)
            audio_clip = AudioFileClip(audio_file)
            source_clips.append((video_clip, audio_clip))
            
            if video_clip.duration < audio_clip.duration:
                loops = math.ceil(audio_clip.duration / video_clip.duration)
                video_clip = concatenate_videoclips([video_clip] * loops)
                
            video_clip = video_clip.subclipped(0, audio_clip.duration)
            video_clip = video_clip.with_audio(audio_clip)
            video_clip = video_clip.resized(width=1920) 
            
            final_clips.append(video_clip)

        update_log("✂️ Stitching all scenes together. (Check your terminal window for progress bar!)")
        
        final_movie = concatenate_videoclips(final_clips, method="compose")
        final_movie.write_videofile("final_video.mp4", codec="libx264", audio_codec="aac", fps=24)
        
        update_log("🧹 Releasing memory and deleting temp files...")
        final_movie.close()
        for clip in final_clips: clip.close()
        for v_clip, a_clip in source_clips: v_clip.close(); a_clip.close()
        
        for index in range(len(script_scenes)):
            try:
                if os.path.exists(f"temp_vid_{index}.mp4"): os.remove(f"temp_vid_{index}.mp4")
                if os.path.exists(f"temp_aud_{index}.mp3"): os.remove(f"temp_aud_{index}.mp3")
            except:
                pass
            
        update_log("✅ DONE! Video saved in your folder as 'final_video.mp4'.")
        messagebox.showinfo("Success!", "Video rendered successfully!")

    except Exception as e:
        update_log(f"❌ ERROR: {str(e)}")
        messagebox.showerror("Error", str(e))
    finally:
        btn_generate.config(state=tk.NORMAL)


# --- UI HELPER FUNCTIONS ---
def update_log(msg):
    log_box.insert(tk.END, msg + "\n")
    log_box.see(tk.END)

def start_generation():
    threading.Thread(target=create_video_thread, daemon=True).start()


# --- 3. BUILD THE GRAPHICAL UI ---
window = tk.Tk()
window.title("Automated Video Creator V2")
window.geometry("700x750")
window.configure(padx=20, pady=20)

frame_top = ttk.LabelFrame(window, text="⚙️ Configuration")
frame_top.pack(fill="x", pady=10)

tk.Label(frame_top, text="Pexels API Key:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
api_entry = ttk.Entry(frame_top, width=65)
api_entry.grid(row=0, column=1, padx=5, pady=5)
api_entry.insert(0, "KPwsjAyOxYePDSkp7uSn1ist6DYqkbPAAUqGEMnOXGYmsWt97L6Jduwj") 

tk.Label(frame_top, text="AI Voice Engine:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
voice_var = tk.StringVar(value="Male")

radio_frame = tk.Frame(frame_top)
radio_frame.grid(row=1, column=1, sticky="w", padx=5)
ttk.Radiobutton(radio_frame, text="Neural Male", variable=voice_var, value="Male").pack(side="left", padx=5)
ttk.Radiobutton(radio_frame, text="Neural Female", variable=voice_var, value="Female").pack(side="left", padx=5)

# --- SCRIPTING FRAME WITH UPDATED TEXT ---
frame_mid = ttk.LabelFrame(window, text="📝 Your Script (Lines must start with 'Visual:' or 'Voice:')")
frame_mid.pack(fill="both", expand=True, pady=10)

# Provided Sample format using the newly requested layout!
default_script = """Visual: slow broken computer
Voice: Is your PC booting incredibly slow?

Visual: frustrated man
Voice: Don't smash your monitor just yet!

Visual: typing fast on modern laptop
Voice: Try this simple setting tweak to speed it up in seconds."""

script_box = scrolledtext.ScrolledText(frame_mid, height=10, width=80)
script_box.pack(padx=10, pady=10, fill="both", expand=True)
script_box.insert(tk.END, default_script)

btn_generate = ttk.Button(window, text="🎥 Render Full Video", command=start_generation)
btn_generate.pack(pady=10)

frame_bot = ttk.LabelFrame(window, text="📋 Event Log")
frame_bot.pack(fill="both", expand=True)
log_box = scrolledtext.ScrolledText(frame_bot, height=10, width=80, bg="#2E2E2E", fg="#00FF00")
log_box.pack(padx=10, pady=10, fill="both", expand=True)

window.mainloop()