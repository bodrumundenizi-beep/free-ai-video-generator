import os
import math
import requests
import threading
import subprocess
import sys
import tkinter as tk
import re
from tkinter import ttk, messagebox, scrolledtext, filedialog

# MoviePy v2
from moviepy import VideoFileClip, AudioFileClip, concatenate_videoclips

# --- 1. IMPROVED HD STOCK SEARCH & DOWNLOAD ---
def get_best_hd_file(video_obj, target_orientation):
    """Finds the highest resolution MP4 file that best matches orientation."""
    files = video_obj.get('video_files', [])
    if not files:
        return None
    
    # Filter for mp4 links with valid dimensions
    valid = [f for f in files if f.get('link') and f.get('width') and f.get('height') and f.get('file_type') == 'video/mp4']
    if not valid:
        valid = [f for f in files if f.get('link')]
    
    if not valid:
        return None

    # Sort descending by resolution (width * height) to guarantee crisp 1080p/4K
    valid.sort(key=lambda x: (x.get('width', 0) * x.get('height', 0)), reverse=True)
    return valid[0]['link']


def download_stock_video(api_key, query, filename, log_func, orientation):
    headers = {"Authorization": api_key}
    
    # Generate search queries: full query, then simplified 2-word fallbacks
    words = query.strip().split()
    query_attempts = [query]
    if len(words) > 2:
        query_attempts.append(" ".join(words[:2]))
        query_attempts.append(words[0])

    video_url = None
    for attempt in query_attempts:
        log_func(f"🔍 Searching Pexels for: '{attempt}' ({orientation})...")
        url = f"https://api.pexels.com/videos/search?query={attempt}&orientation={orientation}&per_page=15&size=large"
        response = requests.get(url, headers=headers)
        
        # Fallback to no orientation filter if portrait search is empty
        if response.status_code != 200 or len(response.json().get('videos', [])) == 0:
            url = f"https://api.pexels.com/videos/search?query={attempt}&per_page=15&size=large"
            response = requests.get(url, headers=headers)

        if response.status_code == 200:
            videos = response.json().get('videos', [])
            if videos:
                # Pick the first video with a valid HD stream
                for v in videos:
                    best_link = get_best_hd_file(v, orientation)
                    if best_link:
                        video_url = best_link
                        break
            if video_url:
                break

    if not video_url:
        raise Exception(f"No usable stock footage found for query: '{query}'")

    log_func(f"⬇️ Downloading High-Quality HD stream...")
    r = requests.get(video_url, stream=True)
    with open(filename, 'wb') as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
    return filename


def generate_voiceover(text, filename, log_func, voice_choice):
    voice_map = {
        "Neural Male": "en-US-GuyNeural",
        "Neural Female": "en-US-AriaNeural",
        "Happy/Upbeat (Female)": "en-US-JennyNeural",
        "Deep/Narrator (Male)": "en-US-ChristopherNeural"
    }
    
    voice_code = voice_map.get(voice_choice, "en-US-GuyNeural")
    log_func(f"🗣️ Generating {voice_choice} Voice: '{text}'")
    
    cmd = [
        sys.executable, "-m", "edge_tts",
        "--voice", voice_code,
        "--text", text,
        "--write-media", filename,
        "--rate", "+10%",
        "--pitch", "+5Hz"
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise Exception("Voice generation failed! Make sure you ran 'pip install edge-tts'.")
    return filename


# --- 2. MAIN PROCESS ---
def create_video_thread():
    try:
        btn_generate.config(state=tk.DISABLED)
        
        api_key = api_entry.get().strip()
        voice_choice = voice_var.get()
        ratio_choice = ratio_var.get()
        save_path = output_entry.get().strip()
        script_text = script_box.get("1.0", tk.END).strip()
        
        if not api_key: raise Exception("API Key cannot be blank.")
        if not save_path: raise Exception("Save location cannot be blank.")
        if not script_text: raise Exception("Script cannot be blank.")
        
        if ratio_choice == "16:9":
            target_w, target_h = 1920, 1080
            orientation = "landscape"
        else:
            target_w, target_h = 1080, 1920
            orientation = "portrait"

        # Parse Script
        script_scenes = []
        current_visual = None
        
        for line_num, line in enumerate(script_text.split("\n")):
            line = line.strip()
            if not line:
                continue
                
            if re.match(r"(?i)^visual\s*:", line):
                current_visual = re.sub(r"(?i)^visual\s*:", "", line).strip()
            elif re.match(r"(?i)^voice\s*:", line):
                if current_visual is None:
                    raise Exception(f"Error near Line {line_num + 1}: Missing 'Visual:' before 'Voice:'")
                current_voice = re.sub(r"(?i)^voice\s*:", "", line).strip()
                script_scenes.append({"visual": current_visual, "voice": current_voice})
                current_visual = None 
            else:
                raise Exception(f"Error on Line {line_num + 1}: Line must begin with 'Visual:' or 'Voice:'")
                
        if not script_scenes:
            raise Exception("No valid scenes found.")

        update_log(f"🚀 Starting {ratio_choice} HD video generation...")
        
        final_clips = []
        source_clips = []
        
        for index, scene in enumerate(script_scenes):
            video_file = f"temp_vid_{index}.mp4"
            audio_file = f"temp_aud_{index}.mp3"
            
            log_print = lambda msg: update_log(msg)
            
            download_stock_video(api_key, scene['visual'], video_file, log_print, orientation)
            generate_voiceover(scene['voice'], audio_file, log_print, voice_choice)
            
            video_clip = VideoFileClip(video_file)
            audio_clip = AudioFileClip(audio_file)
            source_clips.append((video_clip, audio_clip))
            
            if video_clip.duration < audio_clip.duration:
                loops = math.ceil(audio_clip.duration / video_clip.duration)
                video_clip = concatenate_videoclips([video_clip] * loops)
                
            video_clip = video_clip.subclipped(0, audio_clip.duration)
            video_clip = video_clip.with_audio(audio_clip)

            # High-Quality Scale & Center Crop
            scale_factor = max(target_w / video_clip.w, target_h / video_clip.h)
            video_clip = video_clip.resized(scale_factor)
            
            video_clip = video_clip.cropped(
                x_center=video_clip.w / 2,
                y_center=video_clip.h / 2,
                width=target_w,
                height=target_h
            )
            
            final_clips.append(video_clip)

        update_log(f"✂️ Stitching high-resolution {ratio_choice} scenes...")
        
        final_movie = concatenate_videoclips(final_clips, method="compose")
        final_movie.write_videofile(save_path, codec="libx264", audio_codec="aac", fps=30, preset="fast", bitrate="8000k")
        
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
            
        update_log(f"✅ DONE! High-Quality Video saved to:\n{save_path}")
        messagebox.showinfo("Success!", f"HD Video rendered successfully!\n\nSaved at:\n{save_path}")

    except Exception as e:
        update_log(f"❌ ERROR: {str(e)}")
        messagebox.showerror("Error", str(e))
    finally:
        btn_generate.config(state=tk.NORMAL)


# --- UI HELPERS & THEME ---
def update_log(msg):
    log_box.insert(tk.END, msg + "\n")
    log_box.see(tk.END)

def start_generation():
    threading.Thread(target=create_video_thread, daemon=True).start()

def choose_save_location():
    chosen_file = filedialog.asksaveasfilename(
        defaultextension=".mp4",
        filetypes=[("MP4 Video", "*.mp4"), ("All Files", "*.*")],
        initialdir=os.getcwd(),
        initialfile="final_video.mp4"
    )
    if chosen_file:
        output_entry.delete(0, tk.END)
        output_entry.insert(0, chosen_file)

is_dark_mode = True

def toggle_theme():
    global is_dark_mode
    is_dark_mode = not is_dark_mode
    apply_theme()

def apply_theme():
    if is_dark_mode:
        bg_main, fg_main, bg_input = "#1e1e1e", "#ffffff", "#2d2d30"
        btn_theme.config(text="☀️ Light Mode", bg="#333333", fg="#ffffff")
        log_bg, log_fg = "#141414", "#00ff66"
    else:
        bg_main, fg_main, bg_input = "#f4f4f5", "#18181b", "#ffffff"
        btn_theme.config(text="🌙 Dark Mode", bg="#e4e4e7", fg="#18181b")
        log_bg, log_fg = "#27272a", "#a3e635"

    window.configure(bg=bg_main)
    header_frame.configure(bg=bg_main)
    title_label.configure(bg=bg_main, fg=fg_main)
    path_frame.configure(bg=bg_main)
    ratio_frame.configure(bg=bg_main)
    radio_frame.configure(bg=bg_main)

    for lbl in custom_labels:
        lbl.configure(bg=bg_main, fg=fg_main)

    style = ttk.Style()
    style.theme_use('clam')
    style.configure(".", background=bg_main, foreground=fg_main)
    style.configure("TLabelframe", background=bg_main, foreground=fg_main)
    style.configure("TLabelframe.Label", background=bg_main, foreground=fg_main, font=('Segoe UI', 9, 'bold'))
    style.configure("TLabel", background=bg_main, foreground=fg_main)
    style.configure("TRadiobutton", background=bg_main, foreground=fg_main)
    style.configure("TEntry", fieldbackground=bg_input, foreground=fg_main)
    style.configure("TButton", background=bg_input, foreground=fg_main)
    style.map("TButton", background=[('active', '#3f3f46' if is_dark_mode else '#d4d4d8')])

    script_box.configure(bg=bg_input, fg=fg_main, insertbackground=fg_main)
    log_box.configure(bg=log_bg, fg=log_fg, insertbackground=fg_main)


# --- UI BUILD ---
window = tk.Tk()
window.title("Automated Video Creator V2 (HD Edition)")
window.geometry("860x860")
window.configure(padx=20, pady=15)

custom_labels = []

header_frame = tk.Frame(window)
header_frame.pack(fill="x", pady=(0, 10))

title_label = tk.Label(header_frame, text="🎬 AI Video Studio (HD Pro)", font=("Segoe UI", 14, "bold"))
title_label.pack(side="left")

btn_theme = tk.Button(header_frame, text="☀️ Light Mode", font=("Segoe UI", 9, "bold"), relief="groove", padx=10, pady=3, command=toggle_theme)
btn_theme.pack(side="right")

frame_top = ttk.LabelFrame(window, text="⚙️ Configuration")
frame_top.pack(fill="x", pady=5)

lbl_api = tk.Label(frame_top, text="Pexels API Key:")
lbl_api.grid(row=0, column=0, padx=5, pady=5, sticky="w")
custom_labels.append(lbl_api)

api_entry = ttk.Entry(frame_top, width=70)
api_entry.grid(row=0, column=1, padx=5, pady=5, sticky="w")
api_entry.insert(0, "KPwsjAyOxYePDSkp7uSn1ist6DYqkbPAAUqGEMnOXGYmsWt97L6Jduwj")

lbl_save = tk.Label(frame_top, text="Save Video To:")
lbl_save.grid(row=1, column=0, padx=5, pady=5, sticky="w")
custom_labels.append(lbl_save)

path_frame = tk.Frame(frame_top)
path_frame.grid(row=1, column=1, sticky="w", padx=5, pady=5)

output_entry = ttk.Entry(path_frame, width=58)
output_entry.pack(side="left", padx=(0, 5))
output_entry.insert(0, os.path.join(os.getcwd(), "final_video.mp4"))

btn_browse = ttk.Button(path_frame, text="📁 Browse...", command=choose_save_location)
btn_browse.pack(side="left")

lbl_ratio = tk.Label(frame_top, text="Aspect Ratio:")
lbl_ratio.grid(row=2, column=0, padx=5, pady=5, sticky="w")
custom_labels.append(lbl_ratio)

ratio_var = tk.StringVar(value="9:16")
ratio_frame = tk.Frame(frame_top)
ratio_frame.grid(row=2, column=1, sticky="w", padx=5, pady=5)
ttk.Radiobutton(ratio_frame, text="📱 9:16 (Shorts / TikTok)", variable=ratio_var, value="9:16").pack(side="left", padx=5)
ttk.Radiobutton(ratio_frame, text="🖥️ 16:9 (Landscape)", variable=ratio_var, value="16:9").pack(side="left", padx=15)

lbl_voice = tk.Label(frame_top, text="AI Voice Engine:")
lbl_voice.grid(row=3, column=0, padx=5, pady=5, sticky="w")
custom_labels.append(lbl_voice)

voice_var = tk.StringVar(value="Neural Male")
voice_options = ["Neural Male", "Neural Female", "Happy/Upbeat (Female)", "Deep/Narrator (Male)"]
radio_frame = tk.Frame(frame_top)
radio_frame.grid(row=3, column=1, sticky="w", padx=5, pady=5)
for opt in voice_options:
    ttk.Radiobutton(radio_frame, text=opt, variable=voice_var, value=opt).pack(side="left", padx=5)

frame_mid = ttk.LabelFrame(window, text="📝 Your Script (Lines must start with 'Visual:' or 'Voice:')")
frame_mid.pack(fill="both", expand=True, pady=10)

default_script = """Visual: hard drive
Voice: Your PC could be hoarding gigabytes of junk you will never use.

Visual: computer data
Voice: Temporary files and old cache pile up silently, slowing your whole system down.

Visual: keyboard typing
Voice: Here is the fix. Press the Windows key, search for Disk Cleanup, and open it.

Visual: mouse click
Voice: Select your main drive, then click Clean up system files."""

script_box = scrolledtext.ScrolledText(frame_mid, height=8, width=80, font=("Consolas", 10))
script_box.pack(padx=10, pady=10, fill="both", expand=True)
script_box.insert(tk.END, default_script)

btn_generate = ttk.Button(window, text="🎥 Render Full HD Video", command=start_generation)
btn_generate.pack(pady=5)

frame_bot = ttk.LabelFrame(window, text="📋 Event Log")
frame_bot.pack(fill="both", expand=True, pady=5)
log_box = scrolledtext.ScrolledText(frame_bot, height=8, width=80, font=("Consolas", 9))
log_box.pack(padx=10, pady=10, fill="both", expand=True)

apply_theme()
window.mainloop()
