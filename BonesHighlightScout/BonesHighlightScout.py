# ==============================================================================
# BONES HIGHLIGHT SCOUT v2.1.30 (Finalized: Full Review Chamber UI)
# ==============================================================================

import ctypes
import platform
import os
import tkinter as tk
from tkinter import filedialog, messagebox
import ttkbootstrap as tb
from ttkbootstrap.constants import *
import librosa
import librosa.display
import numpy as np
import threading
import subprocess
from moviepy.video.io.VideoFileClip import VideoFileClip
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# --- DPI FIX ---
if platform.system() == "Windows":
    try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception: pass

# --- GLOBAL STATE ---
detected_events = {}
current_video_path = ""
current_audio_path = "temp_audio.mp3"
fig_ref = None; ax_ref = None; line_ref = None

# --- HELPERS ---
def show_custom_message(root, title, message):
    win = tb.Toplevel(root)
    win.title(title); win.geometry("300x150"); win.configure(bg="#1a1a1a")
    win.attributes("-topmost", True)
    tb.Label(win, text=message, font=("Segoe UI", 10), foreground="white", background="#1a1a1a", wraplength=250).pack(pady=30)
    tb.Button(win, text="OK", bootstyle="success-outline", command=win.destroy).pack(pady=10)

def process_audio(video_path, progress_bar, status_label, root):
    clip = VideoFileClip(video_path)
    if progress_bar: progress_bar.configure(value=50); root.update()
    clip.audio.write_audiofile(current_audio_path, logger=None)
    if progress_bar: progress_bar.configure(value=100); root.update()
    return current_audio_path

def draw_waveform(frame, audio_path, threshold_val=-30):
    global fig_ref, ax_ref, line_ref
    if not os.path.exists(audio_path): return
    if fig_ref is None:
        for widget in frame.winfo_children(): widget.destroy()
        y, sr = librosa.load(audio_path, duration=30)
        fig_ref, ax = plt.subplots(figsize=(10, 2), dpi=100)
        fig_ref.patch.set_facecolor('#2b2b2b'); ax.set_facecolor('#2b2b2b')
        librosa.display.waveshow(y, sr=sr, ax=ax, color='#28a745')
        ax_ref = ax.twinx(); ax_ref.set_ylim(-60, 0)
        line_ref = ax_ref.axhline(y=threshold_val, color='white', linestyle='--', linewidth=1.5, alpha=0.8)
        ax_ref.set_ylabel('dB SCALE', color='#28a745', fontsize=8)
        ax_ref.tick_params(axis='y', colors='#888888', labelsize=8)
        ax.axis('off'); ax_ref.spines['right'].set_color('#28a745')
        canvas = FigureCanvasTkAgg(fig_ref, master=frame)
        canvas.draw(); canvas.get_tk_widget().pack(fill=BOTH, expand=YES)
    else:
        line_ref.set_ydata([threshold_val]); fig_ref.canvas.draw_idle()

# --- PREVIEW & EXPORT ---
def preview_clip(start_time, end_time):
    duration = end_time - start_time
    cmd = ['ffplay', '-ss', str(start_time), '-t', str(duration), '-autoexit', current_video_path]
    threading.Thread(target=lambda: subprocess.run(cmd), daemon=True).start()

def render_clips(root):
    selected = [d for d in detected_events.values() if d.get('selected', False)]
    if not selected: messagebox.showwarning("Empty", "No clips selected."); return
    for data in selected:
        cmd = ['ffmpeg', '-y', '-ss', str(data['start']), '-i', current_video_path, '-t', str(data['end'] - data['start']), '-c', 'copy', f"{data['title']}.mp4"]
        subprocess.run(cmd)
    messagebox.showinfo("Success", f"Exported {len(selected)} clips.")

# --- SCAN ---
def run_scan(t_slider, d_slider, limit_slider, root):
    if not os.path.exists(current_audio_path): messagebox.showwarning("Warning", "Load a video first!"); return
    y, sr = librosa.load(current_audio_path); peaks = np.abs(y); threshold = 10**(t_slider.get() / 20); window_size = int(sr); detected_events.clear()
    last_end = -1; eff_dur = d_slider.get(); max_clips = int(limit_slider.get())
    for i in range(0, len(peaks), window_size):
        if len(detected_events) >= max_clips: break
        chunk = peaks[i:i + window_size]
        if len(chunk) > 0 and np.max(chunk) > threshold:
            t = librosa.samples_to_time(i + np.argmax(chunk), sr=sr)
            if t > last_end:
                start = max(0, t - (eff_dur / 2)); end = start + eff_dur
                detected_events[len(detected_events)] = {'start': start, 'end': end, 'title': f"Clip_{len(detected_events)+1:02d}", 'selected': True}
                last_end = end
    if detected_events: create_review_chamber(root)
    else: messagebox.showinfo("Info", "No clips found.")

# --- REVIEW ---
def create_review_chamber(root):
    chamber = tb.Toplevel(root); chamber.title("Review Chamber"); chamber.geometry("800x600"); chamber.configure(bg="#1a1a1a")
    canvas = tk.Canvas(chamber, bg="#1a1a1a", highlightthickness=0); scrollbar = tb.Scrollbar(chamber, orient="vertical", bootstyle="success", command=canvas.yview)
    frame = tb.Frame(canvas, style="dark.TFrame"); frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=frame, anchor="nw", width=780); canvas.configure(yscrollcommand=scrollbar.set); scrollbar.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
    
    all_selected = all(d['selected'] for d in detected_events.values())
    
    def master_toggle():
        new_state = not all(d['selected'] for d in detected_events.values())
        for d in detected_events.values(): d['selected'] = new_state
        chamber.destroy(); create_review_chamber(root)
    
    btn_text = "DESELECT ALL" if all_selected else "SELECT ALL"
    tb.Button(frame, text=btn_text, bootstyle="success-outline" if all_selected else "success", command=master_toggle).pack(pady=10)
    
    for i, data in list(detected_events.items()):
        c = tb.Frame(frame, style="dark.TFrame"); c.pack(fill=X, pady=2, padx=5)
        chk_var = tk.BooleanVar(value=data['selected'])
        chk = tb.Checkbutton(c, variable=chk_var, bootstyle="success-round-toggle", command=lambda v=data, cv=chk_var: v.update({'selected': cv.get()}))
        chk.pack(side=LEFT, padx=5)
        
        entry = tb.Entry(c, bootstyle="success")
        entry.insert(0, data['title']); entry.bind("<FocusOut>", lambda e, v=data, w=entry: v.update({'title': w.get()}))
        entry.pack(side=LEFT, fill=X, expand=YES, padx=10)
        
        tb.Button(c, text="PREVIEW", bootstyle="success-outline", command=lambda s=data['start'], e=data['end']: preview_clip(s, e)).pack(side=RIGHT, padx=5)
        tb.Button(c, text="DELETE", bootstyle="danger-outline", command=lambda k=i, c=c: [detected_events.pop(k), c.destroy()]).pack(side=RIGHT, padx=5)
        
    tb.Button(frame, text="RENDER", bootstyle="success", command=lambda: render_clips(root)).pack(fill=X, pady=20)

# --- MAIN ---
def main():
    global fig_ref
    root = tb.Window(themename="darkly"); root.title("Bones Highlight Scout"); root.geometry("1200x700")
    root.columnconfigure(0, weight=3); root.columnconfigure(1, weight=1)
    params_frame = tb.LabelFrame(root, text="SLICE_PARAMETERS"); params_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
    wave_frame = tb.LabelFrame(root, text="THE_WAVEFORM_MAP"); wave_frame.grid(row=1, column=0, columnspan=2, padx=10, pady=10, sticky="ew")
    
    tb.Label(params_frame, text="dB_THRESHOLD").pack(anchor="w", padx=10, pady=(10,0))
    t_val = tb.Label(params_frame, text="-30.0dB"); t_val.pack(anchor="e", padx=10)
    t_slider = tb.Scale(params_frame, from_=-60, to=0, command=lambda v: [t_val.config(text=f"{float(v):.1f}dB"), draw_waveform(wave_frame, current_audio_path, float(v))])
    t_slider.set(-30); t_slider.pack(fill=X, padx=10, pady=5)
    
    tb.Label(params_frame, text="CLIP_DURATION").pack(anchor="w", padx=10, pady=(10,0))
    d_val = tb.Label(params_frame, text="30s"); d_val.pack(anchor="e", padx=10)
    d_slider = tb.Scale(params_frame, from_=5, to=300, command=lambda v: d_val.config(text=f"{int(float(v))}s"))
    d_slider.set(30); d_slider.pack(fill=X, padx=10, pady=5)
    
    tb.Label(params_frame, text="MAX_CLIPS").pack(anchor="w", padx=10, pady=(10,0))
    l_val = tb.Label(params_frame, text="20"); l_val.pack(anchor="e", padx=10)
    l_slider = tb.Scale(params_frame, from_=1, to=100, command=lambda v: l_val.config(text=f"{int(float(v))}"))
    l_slider.set(20); l_slider.pack(fill=X, padx=10, pady=5)
    
    tb.Button(params_frame, text="SCAN", bootstyle="success", command=lambda: run_scan(t_slider, d_slider, l_slider, root)).pack(fill=X, padx=10, pady=20)
    
    status_label = tb.Label(root, text="IDLE", font=("Segoe UI", 8)); status_label.grid(row=3, column=0, columnspan=2, pady=5)
    progress_bar = tb.Progressbar(root, bootstyle="success-striped", mode='determinate', length=300); progress_bar.grid(row=4, column=0, columnspan=2, pady=5)
    
    def load_video():
        global current_video_path, fig_ref
        path = filedialog.askopenfilename()
        if path:
            fig_ref = None
            current_video_path = path
            status_label.configure(text="PROCESSING...")
            audio = process_audio(path, progress_bar, status_label, root)
            draw_waveform(wave_frame, audio, t_slider.get())
            progress_bar.configure(value=0)
            status_label.configure(text="READY TO SCAN")
            show_custom_message(root, "Ready", "Video processed and ready for scan.")
            
    tb.Button(root, text="LOAD VIDEO", command=load_video).grid(row=2, column=0, columnspan=2, pady=10)

    def on_closing():
        if os.path.exists(current_audio_path): os.remove(current_audio_path)
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()