# ==============================================================================
# BONES HIGHLIGHT SCOUT v3.0.0
# ==============================================================================
# Finds the loud / hype moments in a video from its audio, lets you review and
# trim them, then exports the keepers with ffmpeg.
# Needs ffmpeg, ffprobe and ffplay on PATH. Python packages: requirements.txt

import ctypes
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import traceback
from dataclasses import dataclass

import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.dialogs import Messagebox
from ttkbootstrap.widgets.scrolled import ScrolledFrame
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# --- DPI FIX ---
if platform.system() == "Windows":
    try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception: pass

# --- CONSTANTS ---
APP_VERSION = "3.0.0"
ANALYSIS_RATE = 8000        # Hz. Plenty for loudness, and keeps hour-long VODs small in memory
HOP_SECONDS = 0.05          # one loudness reading every 50 ms
SMOOTH_SECONDS = 0.5        # pops shorter than this don't count as highlights
BASELINE_SECONDS = 30       # what "normal" loudness means in spike mode
DB_FLOOR = -60.0

PLOT_BG = "#2b2b2b"
WAVE_GREEN = "#28a745"
UNSELECTED_GREY = "#777777"

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "BonesHighlightScout")
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
ERROR_LOG_PATH = os.path.join(APP_DIR, "error.log")
NO_WINDOW = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0

VIDEO_TYPES = [("Video files", "*.mp4 *.mkv *.mov *.avi *.webm *.flv *.m4v *.ts"), ("All files", "*.*")]
DETECT_MODES = {
    "Sudden spikes": "spike",
    "Loud moments": "loud",
}
DETECT_HINTS = {
    "spike": "Finds moments much louder than the 30s around them (and above the threshold). Best for gameplay with music.",
    "loud": "Finds anything louder than the threshold.",
}
EXPORT_MODES = {
    "Accurate (re-encode)": "accurate",
    "Fast (copy, may start early)": "copy",
    "Vertical 9:16 (blur fill)": "vertical_blur",
    "Vertical 9:16 (center crop)": "vertical_crop",
}
DEFAULT_SETTINGS = {
    "detect_mode": "Sudden spikes",
    "threshold_db": -35.0,
    "jump_db": 8.0,
    "pre_roll": 20.0,
    "post_roll": 8.0,
    "max_clips": 20,
    "export_mode": "Accurate (re-encode)",
    "last_dir": "",
}


class ScoutError(Exception):
    """A problem worth showing the user as-is."""


class Cancelled(Exception):
    pass


@dataclass
class Clip:
    start: float
    end: float
    peak: float
    score: float
    title: str = ""
    selected: bool = True


# --- HELPERS ---
def fmt_time(seconds):
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600); m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def parse_time(text):
    """'83', '1:23', '1:02:03' or '1:23.5' -> seconds, or None if it isn't a time."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3: return None
    try: nums = [float(p) for p in parts]
    except ValueError: return None
    if any(n < 0 for n in nums): return None
    total = 0.0
    for n in nums: total = total * 60 + n
    return total

_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

def safe_filename(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(". ")
    if name.upper() in _RESERVED_NAMES: name = "_" + name
    return name or "clip"

def unique_path(folder, name, ext, taken):
    """folder/name.ext, adding _2, _3... so nothing existing (or already queued) gets overwritten."""
    candidate, n = os.path.join(folder, name + ext), 2
    while os.path.exists(candidate) or candidate.lower() in taken:
        candidate = os.path.join(folder, f"{name}_{n}{ext}"); n += 1
    taken.add(candidate.lower())
    return candidate

def error_tail(raw, lines=6):
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    return "\n".join(text.strip().splitlines()[-lines:]) or "(no details)"

def log_error(text):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f: f.write(text + "\n")
    except OSError: pass

def missing_tools():
    return [t for t in ("ffmpeg", "ffprobe", "ffplay") if shutil.which(t) is None]

def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f: saved = json.load(f)
        settings.update({k: v for k, v in saved.items() if k in DEFAULT_SETTINGS and type(v) is type(DEFAULT_SETTINGS[k])})
    except (OSError, ValueError): pass
    if settings["detect_mode"] not in DETECT_MODES: settings["detect_mode"] = DEFAULT_SETTINGS["detect_mode"]
    if settings["export_mode"] not in EXPORT_MODES: settings["export_mode"] = DEFAULT_SETTINGS["export_mode"]
    return settings

def save_settings(settings):
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f: json.dump(settings, f, indent=2)
    except OSError: pass


# --- FFMPEG PROCESSES ---
# Every ffmpeg/ffplay we start is tracked so closing the app or pressing CANCEL never leaves one running.
_active_procs = set()
_procs_lock = threading.Lock()

def start_process(cmd, **kwargs):
    try: proc = subprocess.Popen(cmd, creationflags=NO_WINDOW, **kwargs)
    except FileNotFoundError: raise ScoutError(f"{cmd[0]} wasn't found. Install ffmpeg and make sure it's on PATH.")
    with _procs_lock: _active_procs.add(proc)
    return proc

def untrack_process(proc):
    with _procs_lock: _active_procs.discard(proc)

def kill_all_processes():
    with _procs_lock:
        for proc in list(_active_procs):
            if proc.poll() is None: proc.kill()

def probe_video(path):
    """Returns (duration_seconds, has_audio)."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", path]
    try: result = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    except FileNotFoundError: raise ScoutError("ffprobe wasn't found. Install ffmpeg and make sure it's on PATH.")
    if result.returncode != 0: raise ScoutError("Couldn't read this file. Is it a video?\n\n" + error_tail(result.stderr))
    try: info = json.loads(result.stdout or b"{}")
    except ValueError: info = {}
    try: duration = float(info.get("format", {}).get("duration") or 0)
    except ValueError: duration = 0.0
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return duration, has_audio

def analyze_audio(path, duration, progress, cancel_event):
    """Streams the audio out of ffmpeg and returns its loudness in dB, one value per HOP_SECONDS."""
    hop = int(ANALYSIS_RATE * HOP_SECONDS)
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", path,
           "-vn", "-ac", "1", "-ar", str(ANALYSIS_RATE), "-f", "s16le", "-"]
    expected = duration * ANALYSIS_RATE
    parts, carry, read = [], np.empty(0, np.float32), 0
    with tempfile.TemporaryFile() as err:
        proc = start_process(cmd, stdout=subprocess.PIPE, stderr=err)
        try:
            while True:
                if cancel_event.is_set(): raise Cancelled()
                data = proc.stdout.read(hop * 2 * 200)   # 10 s of audio per read
                if not data: break
                data = data[:len(data) // 2 * 2]
                samples = np.concatenate([carry, np.frombuffer(data, np.int16).astype(np.float32) / 32768.0])
                usable = len(samples) // hop * hop
                parts.append(np.sqrt(np.mean(samples[:usable].reshape(-1, hop) ** 2, axis=1)))
                carry = samples[usable:]
                read += len(data) // 2
                if expected: progress(min(read / expected, 1.0))
        except BaseException:
            proc.kill(); raise
        finally:
            proc.stdout.close(); proc.wait(); untrack_process(proc)
        if cancel_event.is_set(): raise Cancelled()
        if proc.returncode != 0:
            err.seek(0); raise ScoutError("ffmpeg couldn't read the audio:\n\n" + error_tail(err.read()))
    rms = np.concatenate(parts) if parts else np.empty(0, np.float32)
    return np.maximum(20 * np.log10(np.maximum(rms, 1e-6)), DB_FLOOR)


# --- DETECTION ---
def smooth_db(db):
    k = max(1, int(SMOOTH_SECONDS / HOP_SECONDS))
    if len(db) <= k: return db.copy()
    # Pad with the edge values: padding with zeros would make the first/last half second look loud.
    padded = np.pad(db, (k // 2, k - 1 - k // 2), mode="edge")
    return np.convolve(padded, np.ones(k) / k, mode="valid")

def local_baseline(smooth):
    """Rolling median loudness over BASELINE_SECONDS, i.e. what 'normal' sounds like at each moment."""
    block = int(round(1 / HOP_SECONDS))   # work in 1 s blocks so this stays fast on long videos
    n = len(smooth) // block
    if n < 3: return np.full_like(smooth, np.median(smooth) if len(smooth) else DB_FLOOR)
    blocks = np.median(smooth[:n * block].reshape(n, block), axis=1)
    w = min(BASELINE_SECONDS, n)
    padded = np.pad(blocks, (w // 2, w - 1 - w // 2), mode="edge")
    rolled = np.median(np.lib.stride_tricks.sliding_window_view(padded, w), axis=1)
    base = np.repeat(rolled, block)
    return np.pad(base, (0, len(smooth) - len(base)), mode="edge")

def detection_limit(base, mode, threshold_db, jump_db):
    """The loudness a moment has to beat to count, at every point in time."""
    if mode == "spike": return np.maximum(base + jump_db, threshold_db)
    return np.full_like(base, threshold_db)

def find_highlights(smooth, base, mode, threshold_db, jump_db, pre_roll, post_roll, max_clips, duration):
    """Picks the strongest moments first, skipping any whose clip would overlap one already picked."""
    score = smooth - detection_limit(base, mode, threshold_db, jump_db)
    score = np.where(score > 0, score, -np.inf)
    times = np.arange(len(score)) * HOP_SECONDS

    picked = []
    while len(picked) < max_clips:
        i = int(np.argmax(score))
        if not np.isfinite(score[i]): break
        t = float(times[i])
        start, end = max(0.0, t - pre_roll), min(duration, t + post_roll)
        if end - start < 1: score[i] = -np.inf; continue
        picked.append(Clip(start=start, end=end, peak=t, score=float(score[i])))
        # Rule out every moment whose clip would overlap this one. A long loud stretch still yields several clips.
        score[(times > start - post_roll) & (times < end + pre_roll)] = -np.inf
    picked.sort(key=lambda c: c.start)
    for n, clip in enumerate(picked, 1): clip.title = f"Clip_{n:02d}"
    return picked


# --- EXPORT ---
def build_export_cmd(src, clip, out, mode):
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-ss", f"{clip.start:.3f}", "-i", src, "-t", f"{clip.end - clip.start:.3f}"]
    if mode == "copy":
        cmd += ["-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        if mode == "vertical_blur":
            graph = ("[0:v]split=2[bg0][fg0];"
                     "[bg0]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:2[bg];"
                     "[fg0]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
            cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "0:a:0?"]
        elif mode == "vertical_crop":
            cmd += ["-vf", "crop=ih*9/16:ih,scale=1080:1920", "-map", "0:v:0", "-map", "0:a:0?"]
        else:
            cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    return cmd + ["-progress", "pipe:1", "-nostats", out]

def run_export(cmd, out, clip_seconds, progress, cancel_event):
    """Runs one ffmpeg export. Returns None on success or an error message."""
    def remove_partial():
        try: os.remove(out)
        except OSError: pass
    with tempfile.TemporaryFile() as err:
        proc = start_process(cmd, stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8", errors="replace")
        try:
            for line in proc.stdout:
                if cancel_event.is_set(): raise Cancelled()
                key, _, value = line.strip().partition("=")
                if key == "out_time_us" and value.isdigit() and clip_seconds > 0:
                    progress(min(int(value) / 1e6 / clip_seconds, 1.0))
        except BaseException:
            proc.kill(); proc.wait(); remove_partial(); raise
        finally:
            proc.stdout.close(); proc.wait(); untrack_process(proc)
        if cancel_event.is_set(): remove_partial(); raise Cancelled()
        if proc.returncode != 0:
            err.seek(0); remove_partial()
            return error_tail(err.read())
    return None


# --- APP ---
@dataclass
class ClipRow:
    clip: Clip
    frame: tb.Frame
    sel_var: tk.BooleanVar
    title_var: tk.StringVar
    start_var: tk.StringVar
    end_var: tk.StringVar
    start_entry: tb.Entry
    end_entry: tb.Entry
    length_label: tb.Label
    valid: bool = True


class HighlightScout:
    def __init__(self):
        self.settings = load_settings()
        self.root = tb.Window(themename="darkly")
        self.root.title(f"Bones Highlight Scout v{APP_VERSION}")
        self.root.geometry("1280x780"); self.root.minsize(1000, 640)
        self.root.report_callback_exception = self._on_tk_error

        self.ui_queue = queue.Queue()        # worker threads hand UI updates to the main thread through this
        self.cancel_event = threading.Event()
        self.busy = False
        self.preview_proc = None

        self.video_path = ""
        self.duration = 0.0
        self.smooth = None
        self.base = None
        self.clips = []
        self.rows = []
        self.span_artists = []
        self.limit_line = None

        self._build_ui()
        self._poll_queue()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(300, self._check_tools)

    # --- UI BUILD ---
    def _build_ui(self):
        root = self.root
        root.columnconfigure(0, weight=3); root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        review = tb.LabelFrame(root, text="REVIEW_CHAMBER"); review.grid(row=0, column=0, padx=(10, 5), pady=10, sticky="nsew")
        params = tb.LabelFrame(root, text="SLICE_PARAMETERS"); params.grid(row=0, column=1, padx=(5, 10), pady=10, sticky="nsew")
        wave = tb.LabelFrame(root, text="THE_WAVEFORM_MAP  (click to preview)"); wave.grid(row=1, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="ew")
        bar = tb.Frame(root); bar.grid(row=2, column=0, columnspan=2, padx=10, pady=(0, 10), sticky="ew")

        self._build_params(params)
        self._build_review(review)
        self._build_waveform(wave)
        self._build_bottom_bar(bar)

    def _slider(self, parent, label, from_, to, value, fmt, on_change=None):
        header = tb.Frame(parent); header.pack(fill=X, padx=10, pady=(8, 0))
        tb.Label(header, text=label).pack(side=LEFT)
        value_label = tb.Label(header, text=fmt(value)); value_label.pack(side=RIGHT)
        var = tk.DoubleVar(value=value)
        def changed(v):
            value_label.config(text=fmt(float(v)))
            if on_change: on_change()
        scale = tb.Scale(parent, from_=from_, to=to, variable=var, command=changed, bootstyle="success")
        scale.pack(fill=X, padx=10, pady=(2, 4))
        return var, scale

    def _build_params(self, parent):
        s = self.settings
        tb.Label(parent, text="DETECTION").pack(anchor="w", padx=10, pady=(10, 0))
        self.detect_var = tk.StringVar(value=s["detect_mode"])
        combo = tb.Combobox(parent, textvariable=self.detect_var, values=list(DETECT_MODES), state="readonly", bootstyle="success")
        combo.pack(fill=X, padx=10, pady=4)
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_detect_mode_changed())
        self.detect_hint = tb.Label(parent, text="", font=("Segoe UI", 8), foreground="#999999", wraplength=260, justify=LEFT)
        self.detect_hint.pack(anchor="w", padx=10)

        self.threshold_var, _ = self._slider(parent, "dB_THRESHOLD", -60, 0, s["threshold_db"], lambda v: f"{v:.1f}dB", self._update_limit_line)
        self.jump_var, self.jump_scale = self._slider(parent, "SPIKE_JUMP", 3, 30, s["jump_db"], lambda v: f"+{v:.1f}dB", self._update_limit_line)
        self.pre_var, _ = self._slider(parent, "PRE_ROLL (before)", 0, 120, s["pre_roll"], lambda v: f"{int(v)}s")
        self.post_var, _ = self._slider(parent, "POST_ROLL (after)", 0, 120, s["post_roll"], lambda v: f"{int(v)}s")
        self.max_var, _ = self._slider(parent, "MAX_CLIPS", 1, 100, s["max_clips"], lambda v: f"{int(v)}")

        self.scan_btn = tb.Button(parent, text="SCAN", bootstyle="success", command=self.scan)
        self.scan_btn.pack(fill=X, padx=10, pady=(14, 10))
        self._on_detect_mode_changed()

    def _build_review(self, parent):
        header = tb.Frame(parent); header.pack(fill=X, padx=8, pady=(8, 4))
        self.toggle_btn = tb.Button(header, text="SELECT ALL", bootstyle="success-outline", command=self.toggle_all)
        self.toggle_btn.pack(side=LEFT)
        self.count_label = tb.Label(header, text=""); self.count_label.pack(side=LEFT, padx=12)
        tb.Label(header, text="Times accept 83, 1:23 or 1:02:03", font=("Segoe UI", 8), foreground="#999999").pack(side=RIGHT)

        self.list_frame = ScrolledFrame(parent, autohide=True)
        self.list_frame.pack(fill=BOTH, expand=YES, padx=8)
        self.empty_label = tb.Label(self.list_frame, text="Load a video, then press SCAN.", foreground="#999999")
        self.empty_label.pack(pady=40)

        footer = tb.Frame(parent); footer.pack(fill=X, padx=8, pady=8)
        footer.columnconfigure(3, weight=1)
        tb.Label(footer, text="EXPORT").grid(row=0, column=0, padx=(0, 6))
        self.export_var = tk.StringVar(value=self.settings["export_mode"])
        tb.Combobox(footer, textvariable=self.export_var, values=list(EXPORT_MODES), state="readonly", width=26, bootstyle="success").grid(row=0, column=1, padx=(0, 12))
        tb.Label(footer, text="TO").grid(row=0, column=2, padx=(0, 6))
        self.out_var = tk.StringVar()
        tb.Entry(footer, textvariable=self.out_var, bootstyle="success").grid(row=0, column=3, sticky="ew")
        tb.Button(footer, text="...", bootstyle="secondary-outline", width=3, command=self.choose_output_dir).grid(row=0, column=4, padx=6)
        self.render_btn = tb.Button(footer, text="RENDER SELECTED", bootstyle="success", command=self.render)
        self.render_btn.grid(row=0, column=5)

    def _build_waveform(self, parent):
        self.fig = Figure(figsize=(10, 2.1), dpi=100, facecolor=PLOT_BG)
        self.fig.subplots_adjust(left=0.05, right=0.99, top=0.94, bottom=0.17)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas.get_tk_widget().pack(fill=BOTH, expand=YES)
        self.canvas.mpl_connect("button_press_event", self._on_wave_click)
        self._draw_waveform()

    def _build_bottom_bar(self, bar):
        bar.columnconfigure(1, weight=1)
        self.load_btn = tb.Button(bar, text="LOAD VIDEO", bootstyle="success-outline", command=self.load_video)
        self.load_btn.grid(row=0, column=0, padx=(0, 10))
        self.file_label = tb.Label(bar, text="No video loaded", foreground="#999999"); self.file_label.grid(row=0, column=1, sticky="w")
        self.status_label = tb.Label(bar, text="IDLE", font=("Segoe UI", 8)); self.status_label.grid(row=0, column=2, padx=10)
        self.progress_bar = tb.Progressbar(bar, bootstyle="success-striped", mode="determinate", length=260, maximum=1.0)
        self.progress_bar.grid(row=0, column=3, padx=(0, 10))
        self.cancel_btn = tb.Button(bar, text="CANCEL", bootstyle="danger-outline", command=self.cancel, state=DISABLED)
        self.cancel_btn.grid(row=0, column=4)

    # --- BACKGROUND WORK ---
    def _poll_queue(self):
        try:
            while True: self.ui_queue.get_nowait()()
        except queue.Empty: pass
        self.root.after(50, self._poll_queue)

    def _run_in_background(self, status, work, on_done):
        """Runs work(progress) on a worker thread, then on_done(result) back on the UI thread."""
        self.cancel_event.clear()
        self._set_busy(True, status)
        post = self.ui_queue.put

        def progress(fraction, text=None):
            post(lambda: self._set_progress(fraction, text))

        def target():
            try:
                result = work(progress)
            except Cancelled:
                post(lambda: self._set_busy(False, "CANCELLED"))
            except ScoutError as e:
                message = str(e)
                post(lambda: (self._set_busy(False, "ERROR"), self._show_error(message)))
            except Exception:
                details = traceback.format_exc(); log_error(details)
                post(lambda: (self._set_busy(False, "ERROR"), self._show_error(f"Something went wrong:\n\n{error_tail(details)}\n\nFull details: {ERROR_LOG_PATH}")))
            else:
                post(lambda: (self._set_busy(False), on_done(result)))

        threading.Thread(target=target, daemon=True).start()

    def _set_busy(self, busy, status=None):
        self.busy = busy
        state = DISABLED if busy else NORMAL
        for btn in (self.load_btn, self.scan_btn, self.render_btn): btn.configure(state=state)
        self.cancel_btn.configure(state=NORMAL if busy else DISABLED)
        self.progress_bar.configure(value=0)
        if status: self.status_label.configure(text=status)

    def _set_progress(self, fraction, text=None):
        if fraction is not None: self.progress_bar.configure(value=fraction)
        if text: self.status_label.configure(text=text)

    def cancel(self):
        self.cancel_event.set()
        kill_all_processes()

    # --- LOAD ---
    def load_video(self):
        if self.busy: return
        path = filedialog.askopenfilename(title="Choose a video", filetypes=VIDEO_TYPES, initialdir=self.settings["last_dir"] or None)
        if not path: return
        self.settings["last_dir"] = os.path.dirname(path)
        self._stop_preview()

        def work(progress):
            duration, has_audio = probe_video(path)
            if not has_audio: raise ScoutError("This video has no audio track, so there's nothing to scan.")
            progress(0, "READING AUDIO...")
            db = analyze_audio(path, duration, progress, self.cancel_event)
            if len(db) == 0: raise ScoutError("No audio could be read from this file.")
            smooth = smooth_db(db)
            return max(duration, len(db) * HOP_SECONDS), smooth, local_baseline(smooth)

        def done(result):
            self.duration, self.smooth, self.base = result
            self.video_path = path
            stem = os.path.splitext(os.path.basename(path))[0]
            self.out_var.set(os.path.join(os.path.dirname(path), f"{stem}_clips"))
            self.file_label.configure(text=f"{os.path.basename(path)}  ({fmt_time(self.duration)})", foreground="")
            self._set_clips([])
            self._draw_waveform()
            self.status_label.configure(text="READY TO SCAN")

        self._run_in_background("OPENING VIDEO...", work, done)

    # --- SCAN ---
    def scan(self):
        if self.busy: return
        if self.smooth is None: self._show_warning("Load a video first!"); return
        clips = find_highlights(self.smooth, self.base, DETECT_MODES[self.detect_var.get()],
                                self.threshold_var.get(), self.jump_var.get(),
                                round(self.pre_var.get()), round(self.post_var.get()),
                                int(round(self.max_var.get())), self.duration)
        self._set_clips(clips)
        if clips:
            self.status_label.configure(text=f"FOUND {len(clips)} CLIPS")
        else:
            self.status_label.configure(text="NO CLIPS FOUND")
            self._show_info("No clips found. Try lowering dB_THRESHOLD or SPIKE_JUMP.\nThe dashed line on the waveform shows what a moment has to beat.")

    def _on_detect_mode_changed(self):
        mode = DETECT_MODES[self.detect_var.get()]
        self.detect_hint.configure(text=DETECT_HINTS[mode])
        self.jump_scale.configure(state=NORMAL if mode == "spike" else DISABLED)
        self._update_limit_line()

    # --- REVIEW ---
    def _set_clips(self, clips):
        self.clips = clips
        for row in self.rows: row.frame.destroy()
        self.rows = []
        if clips: self.empty_label.pack_forget()
        else: self.empty_label.pack(pady=40)
        for n, clip in enumerate(clips, 1): self.rows.append(self._make_row(n, clip))
        self.list_frame.yview_moveto(0)
        self._refresh_counts(); self._draw_clip_spans()

    def _make_row(self, number, clip):
        frame = tb.Frame(self.list_frame); frame.pack(fill=X, pady=2, padx=(0, 14))
        sel_var = tk.BooleanVar(value=clip.selected)
        title_var = tk.StringVar(value=clip.title)
        start_var = tk.StringVar(value=fmt_time(clip.start))
        end_var = tk.StringVar(value=fmt_time(clip.end))

        tb.Checkbutton(frame, variable=sel_var, bootstyle="success-round-toggle").pack(side=LEFT, padx=(4, 6))
        tb.Label(frame, text=f"{number:02d}", width=3, foreground="#999999").pack(side=LEFT)
        tb.Entry(frame, textvariable=title_var, bootstyle="success").pack(side=LEFT, fill=X, expand=YES, padx=(4, 8))
        start_entry = tb.Entry(frame, textvariable=start_var, width=8, justify=CENTER, bootstyle="success"); start_entry.pack(side=LEFT)
        tb.Label(frame, text="–").pack(side=LEFT, padx=3)
        end_entry = tb.Entry(frame, textvariable=end_var, width=8, justify=CENTER, bootstyle="success"); end_entry.pack(side=LEFT)
        length_label = tb.Label(frame, text="", width=6, foreground="#999999"); length_label.pack(side=LEFT, padx=6)

        row = ClipRow(clip, frame, sel_var, title_var, start_var, end_var, start_entry, end_entry, length_label)
        tb.Button(frame, text="DELETE", bootstyle="danger-outline", command=lambda: self._delete_row(row)).pack(side=RIGHT, padx=(4, 0))
        tb.Button(frame, text="PREVIEW", bootstyle="success-outline", command=lambda: self._preview_row(row)).pack(side=RIGHT, padx=(4, 0))

        # Traces keep the clip in sync on every keystroke, so nothing typed is lost when you click straight to RENDER.
        sel_var.trace_add("write", lambda *_: self._on_select_changed(row))
        title_var.trace_add("write", lambda *_: setattr(row.clip, "title", row.title_var.get()))
        start_var.trace_add("write", lambda *_: self._on_time_edited(row))
        end_var.trace_add("write", lambda *_: self._on_time_edited(row))
        self._update_length_label(row)
        return row

    def _on_time_edited(self, row):
        start, end = parse_time(row.start_var.get()), parse_time(row.end_var.get())
        row.valid = start is not None and end is not None and start < end and end <= self.duration + 0.5
        style = "success" if row.valid else "danger"
        row.start_entry.configure(bootstyle=style); row.end_entry.configure(bootstyle=style)
        if row.valid:
            # Leave the precise detected time alone unless the user actually changed the shown value.
            if fmt_time(start) != fmt_time(row.clip.start): row.clip.start = start
            if fmt_time(end) != fmt_time(row.clip.end): row.clip.end = min(end, self.duration)
            self._draw_clip_spans()
        self._update_length_label(row)

    def _update_length_label(self, row):
        row.length_label.configure(text=f"{row.clip.end - row.clip.start:.0f}s" if row.valid else "??")

    def _on_select_changed(self, row):
        row.clip.selected = row.sel_var.get()
        self._refresh_counts(); self._draw_clip_spans()

    def _delete_row(self, row):
        self.clips.remove(row.clip); self.rows.remove(row); row.frame.destroy()
        if not self.clips: self.empty_label.pack(pady=40)
        self._refresh_counts(); self._draw_clip_spans()

    def _preview_row(self, row):
        if not row.valid: self._show_warning("Fix the start/end time first (it's shown in red)."); return
        self.preview(row.clip.start, row.clip.end)

    def toggle_all(self):
        new_state = not all(c.selected for c in self.clips)
        for row in self.rows: row.sel_var.set(new_state)

    def _refresh_counts(self):
        selected = sum(c.selected for c in self.clips)
        self.count_label.configure(text=f"{len(self.clips)} clips · {selected} selected" if self.clips else "")
        all_selected = bool(self.clips) and selected == len(self.clips)
        self.toggle_btn.configure(text="DESELECT ALL" if all_selected else "SELECT ALL", state=NORMAL if self.clips else DISABLED)

    # --- WAVEFORM ---
    def _style_axes(self):
        ax = self.ax
        ax.set_facecolor(PLOT_BG)
        ax.set_ylim(DB_FLOOR, 0)
        ax.tick_params(colors="#888888", labelsize=8)
        for side in ("top", "right"): ax.spines[side].set_visible(False)
        for side in ("left", "bottom"): ax.spines[side].set_color("#555555")
        ax.set_ylabel("dB", color="#888888", fontsize=8)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: fmt_time(x)))

    def _draw_waveform(self):
        ax = self.ax
        ax.clear(); self._style_axes()
        self.span_artists = []; self.limit_line = None
        if self.smooth is None:
            ax.set_xlim(0, 60)
            ax.text(30, -30, "No video loaded", color="#777777", ha="center", va="center")
            self.canvas.draw_idle(); return

        # Shrink to ~4000 points for drawing: the loudest value per bin, so short peaks still show.
        n = len(self.smooth); bins = min(n, 4000)
        starts = np.linspace(0, n, bins + 1).astype(int)[:-1]
        widths = np.diff(np.append(starts, n))
        self.disp_t = (starts + widths / 2) * HOP_SECONDS
        self.disp_base = np.add.reduceat(self.base, starts) / widths
        loudness = np.maximum.reduceat(self.smooth, starts)

        ax.fill_between(self.disp_t, DB_FLOOR, loudness, color=WAVE_GREEN, alpha=0.85, linewidth=0)
        self.limit_line, = ax.plot(self.disp_t, self.disp_base, color="white", linestyle="--", linewidth=1.2, alpha=0.85)
        ax.set_xlim(0, self.duration)
        self._update_limit_line()
        self._draw_clip_spans()

    def _update_limit_line(self):
        if self.limit_line is None: return
        limit = detection_limit(self.disp_base, DETECT_MODES[self.detect_var.get()], self.threshold_var.get(), self.jump_var.get())
        self.limit_line.set_ydata(limit)
        self.canvas.draw_idle()

    def _draw_clip_spans(self):
        for artist in self.span_artists: artist.remove()
        self.span_artists = []
        if self.smooth is not None:
            for n, clip in enumerate(self.clips, 1):
                color = WAVE_GREEN if clip.selected else UNSELECTED_GREY
                self.span_artists.append(self.ax.axvspan(clip.start, clip.end, color=color, alpha=0.3 if clip.selected else 0.15, linewidth=0))
                self.span_artists.append(self.ax.text(clip.start, -2, str(n), color="white", fontsize=7, va="top", clip_on=True))
        self.canvas.draw_idle()

    def _on_wave_click(self, event):
        if self.smooth is None or event.inaxes is not self.ax or event.xdata is None: return
        t = float(event.xdata)
        for clip in self.clips:
            if clip.start <= t <= clip.end: self.preview(clip.start, clip.end); return
        self.preview(max(0.0, t - 2), min(self.duration, t + 13))

    # --- PREVIEW ---
    def preview(self, start, end):
        if not self.video_path: return
        self._stop_preview()
        cmd = ["ffplay", "-hide_banner", "-loglevel", "error", "-autoexit", "-x", "960", "-y", "540",
               "-window_title", f"Preview {fmt_time(start)} - {fmt_time(end)}",
               "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", self.video_path]
        try: self.preview_proc = start_process(cmd)
        except ScoutError as e: self._show_error(str(e))

    def _stop_preview(self):
        if self.preview_proc is not None:
            if self.preview_proc.poll() is None: self.preview_proc.kill()
            untrack_process(self.preview_proc); self.preview_proc = None

    # --- RENDER ---
    def choose_output_dir(self):
        folder = filedialog.askdirectory(title="Export clips to", initialdir=self.out_var.get() or self.settings["last_dir"] or None)
        if folder: self.out_var.set(folder)

    def render(self):
        if self.busy: return
        if not self.video_path: self._show_warning("Load a video first!"); return
        selected_rows = [r for r in self.rows if r.clip.selected]
        if not selected_rows: self._show_warning("No clips selected."); return
        bad = [f"{r.clip.title or '(untitled)'}" for r in selected_rows if not r.valid]
        if bad: self._show_warning("These clips have a start/end time that isn't valid (shown in red):\n\n" + "\n".join(bad)); return
        out_dir = self.out_var.get().strip()
        if not out_dir: self._show_warning("Choose a folder to export to."); return

        mode = EXPORT_MODES[self.export_var.get()]
        ext = os.path.splitext(self.video_path)[1] if mode == "copy" else ".mp4"
        try: os.makedirs(out_dir, exist_ok=True)
        except OSError as e: self._show_error(f"Couldn't create the export folder:\n{e}"); return
        taken = set()
        jobs = [(r.clip, unique_path(out_dir, safe_filename(r.clip.title), ext, taken)) for r in selected_rows]
        src = self.video_path

        def work(progress):
            failures = []
            for i, (clip, out) in enumerate(jobs):
                name = os.path.basename(out)
                progress(i / len(jobs), f"RENDERING {i + 1}/{len(jobs)}: {name}")
                error = run_export(build_export_cmd(src, clip, out, mode), out, clip.end - clip.start,
                                   lambda f: progress((i + f) / len(jobs)), self.cancel_event)
                if error: failures.append(f"{name}:\n{error}")
            return failures

        def done(failures):
            exported = len(jobs) - len(failures)
            self.status_label.configure(text=f"EXPORTED {exported}/{len(jobs)}")
            if failures:
                log_error("\n\n".join(failures))
                self._show_error(f"Exported {exported} of {len(jobs)} clips. These failed:\n\n" + "\n\n".join(failures[:3])
                                 + (f"\n\n...and {len(failures) - 3} more" if len(failures) > 3 else ""))
            if exported and messagebox.askyesno("Done", f"Exported {exported} clip(s) to:\n{out_dir}\n\nOpen the folder?", parent=self.root):
                os.startfile(out_dir)

        self._run_in_background("RENDERING...", work, done)

    # --- MESSAGES ---
    def _show_info(self, message): Messagebox.show_info(message, "Bones Highlight Scout", parent=self.root)
    def _show_warning(self, message): Messagebox.show_warning(message, "Bones Highlight Scout", parent=self.root)
    def _show_error(self, message): Messagebox.show_error(message, "Bones Highlight Scout", parent=self.root)

    def _on_tk_error(self, exc, value, tb_):
        details = "".join(traceback.format_exception(exc, value, tb_)); log_error(details)
        self._show_error(f"Something went wrong:\n\n{error_tail(details)}\n\nFull details: {ERROR_LOG_PATH}")

    def _check_tools(self):
        missing = missing_tools()
        if missing:
            self._show_warning(f"Couldn't find {', '.join(missing)} on PATH.\n\nInstall ffmpeg (https://ffmpeg.org/download.html) and add its bin folder to PATH, "
                               "then restart Bones Highlight Scout.")

    # --- CLOSE ---
    def _on_close(self):
        self.cancel()
        self._stop_preview()
        self.settings.update({
            "detect_mode": self.detect_var.get(),
            "threshold_db": round(float(self.threshold_var.get()), 1),
            "jump_db": round(float(self.jump_var.get()), 1),
            "pre_roll": float(round(self.pre_var.get())),
            "post_roll": float(round(self.post_var.get())),
            "max_clips": int(round(self.max_var.get())),
            "export_mode": self.export_var.get(),
        })
        save_settings(self.settings)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    HighlightScout().run()
