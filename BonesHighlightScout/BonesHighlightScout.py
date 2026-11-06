# ==============================================================================
# BONES HIGHLIGHT SCOUT v3.0.0
# ==============================================================================
# Finds the loud / hype moments in a video from its audio, lets you review and
# trim them, then exports the keepers with ffmpeg.
# Needs ffmpeg and ffprobe on PATH. Python packages: requirements.txt

import ctypes
import hashlib
import io
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import webbrowser
from dataclasses import asdict, dataclass, field

import tkinter as tk
import tkinter.font
from tkinter import filedialog

import numpy as np
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.dialogs import Messagebox, MessageDialog
from ttkbootstrap.style import ThemeDefinition
from ttkbootstrap.widgets.scrolled import ScrolledFrame
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES   # optional: drag-and-drop loading
except ImportError:
    TkinterDnD = None

import hype
import vertical
import player

# --- DPI FIX / TASKBAR ICON ---
if platform.system() == "Windows":
    try: ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception: pass
    # Without this Windows groups the window under python.exe and shows Python's icon in the taskbar.
    try: ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Bones.HighlightScout")
    except Exception: pass

# --- CONSTANTS ---
APP_VERSION = "3.0.0"
ANALYSIS_RATE = hype.SAMPLE_RATE   # 16 kHz: what the reaction model needs; loudness comes from the same pass
HOP_SECONDS = 0.05          # one loudness reading every 50 ms
HYPE_MIN_STRENGTH = 0.5     # how much has to be going on before a moment counts in Hype mode
SMOOTH_SECONDS = 0.5        # pops shorter than this don't count as highlights
BASELINE_SECONDS = 30       # what "normal" loudness means in spike mode
DB_FLOOR = -60.0

# --- BONES THEME ---
# Same palette as Bones Caption Studio and Bones' Sound Check: dark grays with a teal accent,
# switching to blue in colorblind mode.
WINDOW_BG = "#121212"
PANEL_BG = "#1E1E1E"
PANEL_BORDER = "#2D2D2D"
INPUT_BG = "#151515"
BUTTON_BG = "#2D2D2D"
BUTTON_BORDER = "#3D3D3D"
BUTTON_PRESSED = "#222831"
TEXT = "#E0E0E0"
TEXT_MUTED = "#888888"
TEXT_DISABLED = "#555555"
FONT = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 8)

PALETTES = {
    False: {"accent": "#00ADB5", "accent_hover": "#00FFF5", "accent_dim": "#1A3A3A", "on_accent": "#121212",
            "danger_text": "#FF5A5A", "danger_hover": "#3A1E1E",
            "good": "#4CAF50", "warn": "#FFC107", "problem": "#F44336"},
    True:  {"accent": "#1565C0", "accent_hover": "#1976D2", "accent_dim": "#1A2A3F", "on_accent": "#FFFFFF",
            "danger_text": "#FFFFFF", "danger_hover": "#D32F2F",
            "good": "#42A5F5", "warn": "#FFD54F", "problem": "#FF7043"},
}

def theme_name(colorblind):
    return "bones_colorblind" if colorblind else "bones"

def theme_colors(colorblind):
    p = PALETTES[colorblind]
    return {"primary": p["accent"], "secondary": BUTTON_BG, "success": p["good"], "info": p["accent_hover"],
            "warning": p["warn"], "danger": p["problem"], "light": TEXT, "dark": PANEL_BG,
            # selectbg also fills read-only dropdowns, so it stays gray rather than accent.
            "bg": PANEL_BG, "fg": TEXT, "selectbg": BUTTON_BG, "selectfg": TEXT,
            "border": PANEL_BORDER, "inputfg": TEXT, "inputbg": INPUT_BG, "active": BUTTON_BORDER}

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "BonesHighlightScout")
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
ERROR_LOG_PATH = os.path.join(APP_DIR, "error.log")
SESSIONS_DIR = os.path.join(APP_DIR, "sessions")
SPEECH_DIR = os.path.join(APP_DIR, "speech")          # cached transcripts, one per video
WHISPER_DIR = os.path.join(APP_DIR, "models")         # the speech model downloads here the first time
ICON_FILE = "HighlightScout.ico"

CAPTION_STUDIO_NAME = "Bones' Caption Studio"
CAPTION_STUDIO_EXE = "BonesCaptionStudio.exe"
CAPTION_STUDIO_URL = None   # TODO: download page for Bones' Caption Studio. Until it's set, GET IT shows a "coming soon" note.
CAPTION_STUDIO_PITCH = "Try Bones' Caption Studio!"

NO_WINDOW = subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
LOW_PRIORITY = subprocess.BELOW_NORMAL_PRIORITY_CLASS if platform.system() == "Windows" else 0
NORMAL_PRIORITY = subprocess.NORMAL_PRIORITY_CLASS if platform.system() == "Windows" else 0
WORK_THREADS = max(2, (os.cpu_count() or 4) - 4)   # renders and the sound model leave a few CPU threads free

VIDEO_TYPES = [("Video files", "*.mp4 *.mkv *.mov *.avi *.webm *.flv *.m4v *.ts"), ("All files", "*.*")]
DETECT_MODES = {
    "Hype moments (AI)": "hype",
    "Sudden spikes": "spike",
    "Loud moments": "loud",
}
DETECT_HINTS = {
    "hype": "Finds reactions: laughter, screams, hype phrases and chat spikes. Tags show why.",
    "spike": "Finds moments much louder than the 30s around them (and above the threshold). Best for gameplay with music.",
    "loud": "Finds anything louder than the threshold.",
}
EXPORT_MODES = {
    "Accurate (re-encode)": "accurate",
    "Fast (copy, may start early)": "copy",
    "Vertical 9:16 (blur fill)": "vertical_blur",
    "Vertical 9:16 (center crop)": "vertical_crop",
    "Vertical short (cam + game)": "short",
}
DEFAULT_SETTINGS = {
    "detect_mode": "Hype moments (AI)",
    "listen_speech": True,
    "threshold_db": -35.0,
    "jump_db": 8.0,
    "pre_roll": 20.0,
    "post_roll": 8.0,
    "max_clips": 20,
    "export_mode": "Accurate (re-encode)",
    "last_dir": "",
    "colorblind": False,
    "caption_studio_path": "",
    "short_layout": {},
    "preview_volume": 0.8,
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
    tags: list = field(default_factory=list)   # why it was picked, e.g. LAUGHTER, "NO WAY", CHAT x6


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

def app_dir():
    """Folder holding the .exe when built with PyInstaller, otherwise this script's folder."""
    return os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))

def resource_path(name):
    """Files bundled into the build (like the icon) live in _MEIPASS when frozen."""
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), name)

def find_tool(name):
    """An ffmpeg/ folder next to the app wins over PATH, so a build can ship its own ffmpeg."""
    bundled = os.path.join(app_dir(), "ffmpeg", name + ".exe")
    return bundled if os.path.isfile(bundled) else shutil.which(name)

def tool(name):
    return find_tool(name) or name

def missing_tools():
    return [t for t in ("ffmpeg", "ffprobe") if find_tool(t) is None]   # ffplay is only a fallback for the built-in player

# --- CLIP MEMORY ---
# Clips are saved per video (keyed by path, size and modified time) so reopening a video brings them back.
def session_file(video_path):
    try: st = os.stat(video_path)
    except OSError: return None
    key = f"{os.path.abspath(video_path).lower()}|{st.st_size}|{int(st.st_mtime)}"
    return os.path.join(SESSIONS_DIR, hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".json")

def save_session(video_path, clips):
    path = session_file(video_path)
    if not path: return
    try:
        if not clips:
            if os.path.exists(path): os.remove(path)
            return
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"video": video_path, "clips": [asdict(c) for c in clips]}, f, indent=1)
    except OSError: pass

def load_session(video_path, duration):
    path = session_file(video_path)
    if not path: return []
    try:
        with open(path, encoding="utf-8") as f: saved = json.load(f)["clips"]
        clips = [Clip(start=float(c["start"]), end=float(c["end"]), peak=float(c["peak"]), score=float(c["score"]),
                      title=str(c["title"]), selected=bool(c["selected"]), tags=[str(t) for t in c.get("tags", [])]) for c in saved]
    except (OSError, TypeError, ValueError, KeyError): return []
    return [c for c in clips if 0 <= c.start < c.end <= duration + 0.5]

# --- BONES' CAPTION STUDIO ---
def _caption_studio_from_registry():
    try: import winreg
    except ImportError: return
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{CAPTION_STUDIO_EXE}") as key:
                yield winreg.QueryValue(key, None)
        except OSError: pass
        for root in (r"Software\Microsoft\Windows\CurrentVersion\Uninstall", r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try: uninstall = winreg.OpenKey(hive, root)
            except OSError: continue
            with uninstall:
                for i in range(winreg.QueryInfoKey(uninstall)[0]):
                    try:
                        with winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, i)) as app:
                            if "caption studio" not in str(winreg.QueryValueEx(app, "DisplayName")[0]).lower(): continue
                            for value in ("InstallLocation", "DisplayIcon"):
                                try: yield str(winreg.QueryValueEx(app, value)[0]).split(",")[0].strip('"')
                                except OSError: pass
                    except OSError: pass

def _caption_studio_from_shortcuts():
    """Targets of Desktop / Start Menu shortcuts named like Caption Studio (resolved by Windows itself)."""
    script = ("$ws=New-Object -ComObject WScript.Shell;"
              "foreach($f in 'Desktop','CommonDesktopDirectory','StartMenu','CommonStartMenu'){"
              "$d=[Environment]::GetFolderPath($f); if($d -and (Test-Path $d)){"
              "Get-ChildItem $d -Recurse -Depth 3 -Filter *.lnk -ErrorAction SilentlyContinue |"
              "Where-Object { $_.Name -match 'caption\\s*studio' } | ForEach-Object { $ws.CreateShortcut($_.FullName).TargetPath } } }")
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
        yield from (line.strip() for line in result.stdout.splitlines() if line.strip())
    except (OSError, subprocess.SubprocessError): return

def _search_folder_for_exe(folder, depth=4):
    """Looks for the Caption Studio .exe under folder, skipping PyInstaller's half-built build/ copies."""
    for current, dirs, files in os.walk(folder):
        level = current[len(folder):].count(os.sep)
        dirs[:] = [d for d in dirs if d.lower() not in ("build", "venv", ".venv", "node_modules", "_internal", ".git") and level < depth]
        if CAPTION_STUDIO_EXE in files: return os.path.join(current, CAPTION_STUDIO_EXE)
    return None

def _caption_studio_candidates(saved_path):
    if saved_path: yield saved_path
    yield from _caption_studio_from_registry()
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if not base: continue
        for folder in ("Bones' Caption Studio", "Bones Caption Studio", "BonesCaptionStudio"):
            yield os.path.join(base, folder, CAPTION_STUDIO_EXE)
    # Installed side by side with Highlight Scout (e.g. both in one "Bones" folder).
    for parent in {os.path.dirname(app_dir()), os.path.dirname(os.path.dirname(app_dir()))}:
        try: siblings = [os.path.join(parent, d) for d in os.listdir(parent) if "caption" in d.lower() and "studio" in d.lower()]
        except OSError: continue
        for folder in siblings: yield _search_folder_for_exe(folder)
    for target in _caption_studio_from_shortcuts():
        yield _search_folder_for_exe(target) if os.path.isdir(target) else target

def find_caption_studio(saved_path=""):
    for candidate in _caption_studio_candidates(saved_path):
        if candidate and os.path.isdir(candidate): candidate = _search_folder_for_exe(candidate)
        if candidate and candidate.lower().endswith(".exe") and os.path.isfile(candidate): return candidate
    return None

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

def start_process(cmd, low_priority=True, **kwargs):
    """Heavy jobs run below normal priority so the rest of the PC stays responsive. Previews stay at normal so they play smoothly."""
    try: proc = subprocess.Popen(cmd, creationflags=NO_WINDOW | (LOW_PRIORITY if low_priority else NORMAL_PRIORITY), **kwargs)
    except FileNotFoundError: raise ScoutError(f"{os.path.basename(cmd[0])} wasn't found. Install ffmpeg and make sure it's on PATH.")
    with _procs_lock: _active_procs.add(proc)
    return proc

def dark_title_bar(window):
    """Windows' own dark title bar, in the app's background color. Windows 10 ignores the color and just goes dark."""
    if platform.system() != "Windows": return
    def colorref(color):   # Windows wants 0x00BBGGRR
        return int(color[5:7] + color[3:5] + color[1:3], 16)
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        for attribute, value in ((20, 1), (35, colorref(WINDOW_BG)), (36, colorref(TEXT))):   # dark mode, bar color, title color
            value = ctypes.c_int(value)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attribute, ctypes.byref(value), ctypes.sizeof(value))
    except Exception: pass

def set_own_priority(low):
    """The AI models run inside this process, so it drops to low priority while a job is running."""
    if platform.system() != "Windows": return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), LOW_PRIORITY if low else NORMAL_PRIORITY)
    except Exception: pass

def untrack_process(proc):
    with _procs_lock: _active_procs.discard(proc)

def kill_all_processes():
    with _procs_lock:
        for proc in list(_active_procs):
            if proc.poll() is None: proc.kill()

def probe_video_size(path):
    """(width, height) of the video, or (16, 9) if it can't be read: only the shape matters."""
    cmd = [tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", path]
    try:
        stream = json.loads(subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW).stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (OSError, ValueError, KeyError, IndexError):
        return 16, 9

def grab_frame(path, seconds):
    """One frame of the video as a PIL image (for the vertical layout editor)."""
    cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{seconds:.3f}", "-i", path, "-frames:v", "1",
           "-vf", "scale='min(1920,iw)':-2", "-f", "image2pipe", "-c:v", "png", "-"]
    proc = start_process(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try: data, _ = proc.communicate()
    finally: untrack_process(proc)
    image = vertical.Image.open(io.BytesIO(data))
    image.load()
    return image

def probe_video(path):
    """Returns (duration_seconds, has_audio)."""
    cmd = [tool("ffprobe"), "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", path]
    try: result = subprocess.run(cmd, capture_output=True, creationflags=NO_WINDOW)
    except FileNotFoundError: raise ScoutError("ffprobe wasn't found. Install ffmpeg and make sure it's on PATH.")
    if result.returncode != 0: raise ScoutError("Couldn't read this file. Is it a video?\n\n" + error_tail(result.stderr))
    try: info = json.loads(result.stdout or b"{}")
    except ValueError: info = {}
    try: duration = float(info.get("format", {}).get("duration") or 0)
    except ValueError: duration = 0.0
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    return duration, has_audio

def analyze_audio(path, duration, progress, cancel_event, on_samples=None):
    """Streams the audio out of ffmpeg and returns its loudness in dB, one value per HOP_SECONDS.
    on_samples(audio) also gets every piece of 16 kHz audio (the reaction detector listens along)."""
    hop = int(ANALYSIS_RATE * HOP_SECONDS)
    cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-i", path,
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
                fresh = np.frombuffer(data, np.int16).astype(np.float32) / 32768.0
                if on_samples: on_samples(fresh)
                samples = np.concatenate([carry, fresh])
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

def read_audio_chunk(path, start, seconds):
    """A stretch of the video's audio as 16 kHz mono float32 (for speech recognition)."""
    cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{seconds:.3f}",
           "-i", path, "-vn", "-ac", "1", "-ar", str(hype.SAMPLE_RATE), "-f", "s16le", "-"]
    proc = start_process(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try: data, _ = proc.communicate()
    finally: untrack_process(proc)
    return np.frombuffer(data[:len(data) // 2 * 2], np.int16).astype(np.float32) / 32768.0

def speech_cache_path(video_path):
    session = session_file(video_path)
    return os.path.join(SPEECH_DIR, os.path.basename(session)) if session else None


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

def pick_moments(score, pre_roll, post_roll, max_clips, duration):
    """Picks the strongest moments (score > 0) first, skipping any whose clip would overlap one already picked."""
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
    return picked

def name_untitled(clips):
    for n, clip in enumerate(clips, 1):
        if not clip.title: clip.title = f"Clip_{n:02d}"
    return clips

def find_highlights(smooth, base, mode, threshold_db, jump_db, pre_roll, post_roll, max_clips, duration):
    score = smooth - detection_limit(base, mode, threshold_db, jump_db)
    return name_untitled(pick_moments(score, pre_roll, post_roll, max_clips, duration))

def find_hype_moments(smooth, base, hype_data, pre_roll, post_roll, max_clips, duration):
    """Hype mode: combines loudness, reaction sounds, speech and chat, then tags each clip with why it was picked."""
    signals = hype.build_signals(smooth, base, HOP_SECONDS, hype_data)
    clips = pick_moments(signals.strength - HYPE_MIN_STRENGTH, pre_roll, post_roll, max_clips, duration)
    for clip in clips:
        if hype_data.speech: clip.start, clip.end = hype.snap_to_speech(clip.start, clip.end, hype_data.speech, duration)
        clip.tags, title = hype.describe_moment(signals, hype_data, HOP_SECONDS, clip.peak, clip.start, clip.end)
        clip.title = title or ""
    # Snapping to sentences can make neighbours overlap; those are one moment, so join them.
    merged = []
    for clip in clips:
        if merged and clip.start < merged[-1].end:
            prev = merged[-1]
            prev.end = max(prev.end, clip.end)
            prev.tags = list(dict.fromkeys(prev.tags + clip.tags))[:5]
            prev.title = prev.title or clip.title
        else:
            merged.append(clip)
    return name_untitled(merged)


# --- EXPORT ---
def build_export_cmd(src, clip, out, mode, short_graph=None):
    cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-ss", f"{clip.start:.3f}", "-i", src, "-t", f"{clip.end - clip.start:.3f}"]
    if mode == "copy":
        cmd += ["-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        if mode == "short":
            cmd += ["-filter_complex", short_graph, "-map", "[v]", "-map", "0:a:0?"]
        elif mode == "vertical_blur":
            graph = ("[0:v]split=2[bg0][fg0];"
                     "[bg0]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:2[bg];"
                     "[fg0]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
            cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "0:a:0?"]
        elif mode == "vertical_crop":
            cmd += ["-vf", "crop=ih*9/16:ih,scale=1080:1920", "-map", "0:v:0", "-map", "0:a:0?"]
        else:
            cmd += ["-map", "0:v:0", "-map", "0:a:0?"]
        threads = str(WORK_THREADS)
        cmd += ["-filter_threads", threads, "-filter_complex_threads", threads, "-threads", threads,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
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
    num_label: tb.Label
    valid: bool = True


class HighlightScout:
    def __init__(self):
        self.settings = load_settings()
        self.root = tb.Window(themename="darkly", iconphoto=None)   # None: skip ttkbootstrap's own icon so ours shows
        self.style = self.root.style
        for colorblind in (False, True):
            self.style.register_theme(ThemeDefinition(theme_name(colorblind), theme_colors(colorblind), "dark"))
        self.colorblind_var = tk.BooleanVar(value=self.settings["colorblind"])
        self.palette = PALETTES[self.colorblind_var.get()]
        self.root.title(f"Bones Highlight Scout v{APP_VERSION}")
        self.root.geometry("1280x780"); self.root.minsize(1000, 640)
        self.root.report_callback_exception = self._on_tk_error
        # Every window as it appears: the main one ("Tk") and pop-ups like the layout editor and messages ("Toplevel").
        for window_class in ("Tk", "Toplevel"):
            self.root.bind_class(window_class, "<Map>", lambda e: dark_title_bar(e.widget), add="+")
        try: self.root.iconbitmap(default=resource_path(ICON_FILE))   # default= also covers dialogs
        except tk.TclError: pass

        self.ui_queue = queue.Queue()        # worker threads hand UI updates to the main thread through this
        self.cancel_event = threading.Event()
        self.busy = False
        self.preview_proc = None
        self.preview_window = None           # the clip player, while it's open
        self.preview_row = None              # the clip it's showing (None for a spot clicked on the waveform)

        self.video_path = ""
        self.duration = 0.0
        self.smooth = None
        self.base = None
        self.hype = hype.HypeData()          # reactions / speech / chat for the loaded video
        self.clips = []
        self.rows = []
        self.current_row = None
        self.last_export_dir = ""
        self.video_size = (16, 9)
        self._save_job = None
        self.span_artists = []
        self.limit_line = None

        self.dnd_enabled = self._load_drag_and_drop()
        self._apply_theme(redraw=False)
        self._build_ui()
        self._register_drop_targets()
        self._bind_shortcuts()
        self._poll_queue()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(300, self._check_tools)
        self.root.after(500, self._check_ai_parts)

    # --- THEME ---
    def _apply_theme(self, redraw=True):
        colorblind = self.colorblind_var.get()
        self.palette = p = PALETTES[colorblind]
        s = self.style
        s.theme_use(theme_name(colorblind))
        self.root.configure(background=WINDOW_BG)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"): tk.font.nametofont(name).configure(family=FONT[0], size=FONT[1])

        # Custom styles live per theme, so they're (re)applied after every switch.
        s.configure("TLabelframe", background=PANEL_BG, bordercolor=PANEL_BORDER, lightcolor=PANEL_BG, darkcolor=PANEL_BG)
        s.configure("TLabelframe.Label", background=PANEL_BG, foreground=p["accent"], font=FONT_BOLD)
        s.configure("Panel.TFrame", background=PANEL_BG, bordercolor=PANEL_BORDER)
        s.configure("Muted.TLabel", background=PANEL_BG, foreground=TEXT_MUTED, font=FONT_SMALL)
        s.configure("Status.TLabel", background=PANEL_BG, foreground=p["accent"], font=FONT_BOLD)
        s.configure("RowNum.TLabel", background=PANEL_BG, foreground=TEXT_MUTED, font=FONT)
        s.configure("RowNumCurrent.TLabel", background=PANEL_BG, foreground=p["accent"], font=FONT_BOLD)
        s.configure("Tags.TLabel", background=PANEL_BG, foreground=p["accent_hover"], font=("Segoe UI", 8, "bold"))

        def button(name, bg, fg, border, hover_bg, hover_fg, hover_border, disabled_bg, disabled_fg, disabled_border):
            s.configure(name, background=bg, foreground=fg, bordercolor=border, lightcolor=bg, darkcolor=bg,
                        focuscolor=fg, focusthickness=0, font=FONT_BOLD, padding=(12, 6), relief=FLAT)
            s.map(name,
                  background=[("disabled", disabled_bg), ("pressed", BUTTON_PRESSED), ("active", hover_bg)],
                  foreground=[("disabled", disabled_fg), ("active", hover_fg)],
                  bordercolor=[("disabled", disabled_border), ("active", hover_border)],
                  lightcolor=[("disabled", disabled_bg), ("pressed", BUTTON_PRESSED), ("active", hover_bg)],
                  darkcolor=[("disabled", disabled_bg), ("pressed", BUTTON_PRESSED), ("active", hover_bg)])
        # Regular buttons: dark gray with accent text. Main actions: solid accent. Delete/Cancel: red text.
        button("Bones.TButton", BUTTON_BG, p["accent"], BUTTON_BORDER, BUTTON_BORDER, p["accent"], p["accent"], INPUT_BG, TEXT_DISABLED, "#222222")
        button("BonesAccent.TButton", p["accent"], "#EEEEEE", p["accent"], p["accent_hover"], p["on_accent"], p["accent_hover"], p["accent_dim"], "#666666", p["accent_dim"])
        button("BonesDanger.TButton", "#252525", p["danger_text"], BUTTON_BORDER, p["danger_hover"], "#FFFFFF", "#FF5A5A", INPUT_BG, TEXT_DISABLED, "#222222")
        s.configure("Bones.TButton", padding=(10, 4))
        s.configure("BonesDanger.TButton", padding=(10, 4))

        if redraw:
            self._draw_waveform()

    # --- UI BUILD ---
    def _build_ui(self):
        root = self.root
        root.columnconfigure(0, weight=3); root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        review = tb.Labelframe(root, text="REVIEW_CHAMBER", padding=6); review.grid(row=0, column=0, padx=(12, 6), pady=12, sticky="nsew")
        params = tb.Labelframe(root, text="SLICE_PARAMETERS", padding=6); params.grid(row=0, column=1, padx=(6, 12), pady=12, sticky="nsew")
        wave = tb.Labelframe(root, text="THE_WAVEFORM_MAP  (click to preview)", padding=6); wave.grid(row=1, column=0, columnspan=2, padx=12, pady=(0, 12), sticky="ew")
        bar = tb.Frame(root, style="Panel.TFrame", padding=8); bar.grid(row=2, column=0, columnspan=2, padx=12, pady=(0, 12), sticky="ew")
        self.panels = (review, params, wave, bar)

        self._build_params(params)
        self._build_review(review)
        self._build_waveform(wave)
        self._build_bottom_bar(bar)

    def _slider(self, parent, label, from_, to, value, fmt, on_change=None):
        header = tb.Frame(parent); header.pack(fill=X, padx=10, pady=(6, 0))
        tb.Label(header, text=label).pack(side=LEFT)
        value_label = tb.Label(header, text=fmt(value)); value_label.pack(side=RIGHT)
        var = tk.DoubleVar(value=value)
        def changed(v):
            value_label.config(text=fmt(float(v)))
            if on_change: on_change()
        scale = tb.Scale(parent, from_=from_, to=to, variable=var, command=changed, bootstyle="primary")
        scale.pack(fill=X, padx=10, pady=(2, 4))
        return var, scale

    def _build_params(self, parent):
        s = self.settings
        tb.Label(parent, text="DETECTION").pack(anchor="w", padx=10, pady=(10, 0))
        self.detect_var = tk.StringVar(value=s["detect_mode"])
        combo = tb.Combobox(parent, textvariable=self.detect_var, values=list(DETECT_MODES), state="readonly", bootstyle="primary")
        combo.pack(fill=X, padx=10, pady=4)
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_detect_mode_changed())
        self.detect_hint = tb.Label(parent, text="", style="Muted.TLabel", wraplength=260, justify=LEFT)
        self.detect_hint.pack(anchor="w", padx=10)

        # Loudness modes show the dB sliders; Hype mode shows its own options in the same spot.
        mode_area = tb.Frame(parent); mode_area.pack(fill=X)
        self.level_frame = tb.Frame(mode_area)
        self.threshold_var, _ = self._slider(self.level_frame, "dB_THRESHOLD", -60, 0, s["threshold_db"], lambda v: f"{v:.1f}dB", self._update_limit_line)
        self.jump_var, self.jump_scale = self._slider(self.level_frame, "SPIKE_JUMP", 3, 30, s["jump_db"], lambda v: f"+{v:.1f}dB", self._update_limit_line)

        self.hype_frame = tb.Frame(mode_area)
        self.speech_var = tk.BooleanVar(value=s["listen_speech"])
        tb.Checkbutton(self.hype_frame, text="Listen to speech (slower, names clips)", variable=self.speech_var, bootstyle="primary",
                       command=self._update_hype_status).pack(anchor="w", padx=10, pady=(6, 2))
        self.hype_status = tb.Label(self.hype_frame, text="", style="Muted.TLabel", justify=LEFT, wraplength=260)
        self.hype_status.pack(anchor="w", padx=10)
        chat_row = tb.Frame(self.hype_frame); chat_row.pack(fill=X, padx=10, pady=(4, 0))
        tb.Button(chat_row, text="LOAD CHAT REPLAY...", style="Bones.TButton", command=self.choose_chat_file).pack(side=LEFT, fill=X, expand=YES)
        tb.Button(chat_row, text="CLEAR", style="BonesDanger.TButton", command=self.clear_chat).pack(side=LEFT, padx=(6, 0))

        self.pre_var, _ = self._slider(parent, "PRE_ROLL (before)", 0, 120, s["pre_roll"], lambda v: f"{int(v)}s")
        self.post_var, _ = self._slider(parent, "POST_ROLL (after)", 0, 120, s["post_roll"], lambda v: f"{int(v)}s")
        self.max_var, _ = self._slider(parent, "MAX_CLIPS", 1, 100, s["max_clips"], lambda v: f"{int(v)}")

        self.scan_btn = tb.Button(parent, text="SCAN", style="BonesAccent.TButton", command=self.scan)
        self.scan_btn.pack(fill=X, padx=10, pady=(10, 8))
        self._on_detect_mode_changed()

    def _build_review(self, parent):
        header = tb.Frame(parent); header.pack(fill=X, padx=8, pady=(8, 4))
        self.toggle_btn = tb.Button(header, text="SELECT ALL", style="Bones.TButton", command=self.toggle_all)
        self.toggle_btn.pack(side=LEFT)
        self.count_label = tb.Label(header, text=""); self.count_label.pack(side=LEFT, padx=12)
        tb.Label(header, style="Muted.TLabel", justify=RIGHT,
                 text="Times accept 83, 1:23 or 1:02:03\n"
                      "Space preview · ↑↓ pick clip · Del delete · Esc stop · Ctrl+O load · F5 scan · Ctrl+R render").pack(side=RIGHT)

        self.list_frame = ScrolledFrame(parent, autohide=True)
        self.list_frame.pack(fill=BOTH, expand=YES, padx=8)
        hint = "Drop a video anywhere in this window (or click LOAD VIDEO), then press SCAN." if self.dnd_enabled else "Load a video, then press SCAN."
        self.empty_label = tb.Label(self.list_frame, text=hint, foreground=TEXT_MUTED)
        self.empty_label.pack(pady=40)

        footer = tb.Frame(parent); footer.pack(fill=X, padx=8, pady=8)
        footer.columnconfigure(3, weight=1)
        tb.Label(footer, text="EXPORT").grid(row=0, column=0, padx=(0, 6))
        self.export_var = tk.StringVar(value=self.settings["export_mode"])
        tb.Combobox(footer, textvariable=self.export_var, values=list(EXPORT_MODES), state="readonly", width=26, bootstyle="primary").grid(row=0, column=1, padx=(0, 12))
        tb.Label(footer, text="TO").grid(row=0, column=2, padx=(0, 6))
        self.out_var = tk.StringVar()
        tb.Entry(footer, textvariable=self.out_var, bootstyle="primary").grid(row=0, column=3, sticky="ew")
        tb.Button(footer, text="...", style="Bones.TButton", width=3, command=self.choose_output_dir).grid(row=0, column=4, padx=6)
        self.render_btn = tb.Button(footer, text="RENDER SELECTED", style="BonesAccent.TButton", command=self.render)
        self.render_btn.grid(row=0, column=5)
        tb.Button(footer, text="CAPTION STUDIO", style="Bones.TButton", command=self.open_caption_studio).grid(row=0, column=6, padx=(6, 0))
        self.short_label = tb.Label(footer, text="", style="Muted.TLabel")
        self.short_label.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        tb.Button(footer, text="VERTICAL SHORT LAYOUT...", style="Bones.TButton", command=self.open_short_editor).grid(row=1, column=5, columnspan=2, sticky="ew", pady=(6, 0))
        self._update_short_label()

    def _build_waveform(self, parent):
        self.fig = Figure(figsize=(10, 2.1), dpi=100, facecolor=PANEL_BG)
        self.fig.subplots_adjust(left=0.05, right=0.975, top=0.94, bottom=0.17)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas.get_tk_widget().pack(fill=BOTH, expand=YES)
        self.canvas.mpl_connect("button_press_event", self._on_wave_click)
        self._draw_waveform()

    def _build_bottom_bar(self, bar):
        bar.columnconfigure(1, weight=1)
        self.load_btn = tb.Button(bar, text="LOAD VIDEO", style="Bones.TButton", command=self.load_video)
        self.load_btn.grid(row=0, column=0, padx=(0, 10))
        self.file_label = tb.Label(bar, text="No video loaded", foreground=TEXT_MUTED); self.file_label.grid(row=0, column=1, sticky="w")
        self.status_label = tb.Label(bar, text="IDLE", style="Status.TLabel"); self.status_label.grid(row=0, column=2, padx=10)
        self.progress_bar = tb.Progressbar(bar, bootstyle="primary", mode="determinate", length=260, maximum=1.0)
        self.progress_bar.grid(row=0, column=3, padx=(0, 10))
        self.cancel_btn = tb.Button(bar, text="CANCEL", style="BonesDanger.TButton", command=self.cancel, state=DISABLED)
        self.cancel_btn.grid(row=0, column=4)
        tb.Checkbutton(bar, text="Colorblind mode", variable=self.colorblind_var, bootstyle="primary",
                       command=self._apply_theme).grid(row=0, column=5, padx=(16, 0))

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
        set_own_priority(low=busy)
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

    # --- DRAG AND DROP ---
    def _load_drag_and_drop(self):
        if TkinterDnD is None: return False
        try: TkinterDnD._require(self.root)
        except (RuntimeError, tk.TclError): return False
        return True

    def _register_drop_targets(self):
        if not self.dnd_enabled: return
        # Windows hands a drop to the nearest registered parent, so the four panels cover the whole window.
        for panel in self.panels:
            panel.drop_target_register(DND_FILES)
            panel.dnd_bind("<<DropEnter>>", self._on_drag_enter)
            panel.dnd_bind("<<DropLeave>>", self._on_drag_leave)
            panel.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drag_enter(self, event):
        if not self.busy:
            self._status_before_drag = self.status_label.cget("text")
            self.status_label.configure(text="DROP TO LOAD VIDEO")
        return event.action

    def _on_drag_leave(self, event):
        if not self.busy and getattr(self, "_status_before_drag", None):
            self.status_label.configure(text=self._status_before_drag)
        return event.action

    def _on_drop(self, event):
        self._on_drag_leave(event)
        files = [p for p in self.root.tk.splitlist(event.data) if os.path.isfile(p)]
        if self.busy: self._show_warning("Wait for the current job to finish (or press CANCEL) first.")
        elif not files: self._show_warning("That isn't a file. Drop a video file to load it.")
        else: self.load_video(files[0])
        return event.action

    # --- LOAD ---
    def load_video(self, path=None):
        if self.busy: return
        if path is None:
            path = filedialog.askopenfilename(title="Choose a video", filetypes=VIDEO_TYPES, initialdir=self.settings["last_dir"] or None)
        if not path: return
        self.settings["last_dir"] = os.path.dirname(path)
        self._stop_preview()
        self._save_session_now()   # keep the current video's clips before switching

        def work(progress):
            duration, has_audio = probe_video(path)
            size = probe_video_size(path)
            if not has_audio: raise ScoutError("This video has no audio track, so there's nothing to scan.")
            progress(0, "READING AUDIO...")
            detector = hype.load_reaction_detector(resource_path("models"), WORK_THREADS)   # listens for laughter etc. in the same pass
            db = analyze_audio(path, duration, progress, self.cancel_event, detector.feed if detector else None)
            if len(db) == 0: raise ScoutError("No audio could be read from this file.")
            smooth = smooth_db(db)
            data = hype.HypeData(reactions=detector.finish() if detector else None)
            cache = speech_cache_path(path)
            data.speech = hype.load_speech(cache) if cache else None
            data.chat_path = hype.find_chat_file(path) or ""
            if data.chat_path:
                try: data.chat = hype.load_chat(data.chat_path)
                except (OSError, ValueError): data.chat_path = ""
            return max(duration, len(db) * HOP_SECONDS), smooth, local_baseline(smooth), data, size

        def done(result):
            self.duration, self.smooth, self.base, self.hype, self.video_size = result
            self.video_path = path
            self._update_hype_status()
            stem = os.path.splitext(os.path.basename(path))[0]
            self.out_var.set(os.path.join(os.path.dirname(path), f"{stem}_clips"))
            self.file_label.configure(text=f"{os.path.basename(path)}  ({fmt_time(self.duration)})", foreground=TEXT)
            restored = load_session(path, self.duration)
            self._set_clips(restored)
            self._draw_waveform()
            self.status_label.configure(text=f"RESTORED {len(restored)} CLIPS FROM LAST TIME" if restored else "READY TO SCAN")

        self._run_in_background("OPENING VIDEO...", work, done)

    # --- SCAN ---
    def scan(self):
        if self.busy: return
        if self.smooth is None: self._show_warning("Load a video first!"); return
        mode = DETECT_MODES[self.detect_var.get()]
        pre, post, max_clips = round(self.pre_var.get()), round(self.post_var.get()), int(round(self.max_var.get()))
        if mode == "hype":
            if self.speech_var.get() and self.hype.speech is None and hype.speech_available():
                self._transcribe_then_scan(); return      # comes back here once speech is ready
            clips = find_hype_moments(self.smooth, self.base, self.hype, pre, post, max_clips, self.duration)
            hint = "No reactions found. Try a longer video, turn on speech, or load a chat replay."
        else:
            clips = find_highlights(self.smooth, self.base, mode, self.threshold_var.get(), self.jump_var.get(),
                                    pre, post, max_clips, self.duration)
            hint = "No clips found. Try lowering dB_THRESHOLD or SPIKE_JUMP.\nThe dashed line on the waveform shows what a moment has to beat."
        self._set_clips(clips)
        self._schedule_session_save()
        if clips:
            self.status_label.configure(text=f"FOUND {len(clips)} CLIPS")
        else:
            self.status_label.configure(text="NO CLIPS FOUND")
            self._show_info(hint)

    def _on_detect_mode_changed(self):
        mode = DETECT_MODES[self.detect_var.get()]
        self.detect_hint.configure(text=DETECT_HINTS[mode])
        self.jump_scale.configure(state=NORMAL if mode == "spike" else DISABLED)
        if mode == "hype":
            self.level_frame.pack_forget(); self.hype_frame.pack(fill=X)
        else:
            self.hype_frame.pack_forget(); self.level_frame.pack(fill=X)
        self._update_hype_status()
        self._update_limit_line()

    # --- HYPE MODE ---
    def _check_ai_parts(self):
        """Loads the AI libraries once in the background, so the panel can say whether they work."""
        def probe():
            hype.reactions_available(resource_path("models")); hype.speech_available()
            self.ui_queue.put(self._update_hype_status)
        threading.Thread(target=probe, daemon=True).start()

    def _update_hype_status(self):
        if not hasattr(self, "hype_status"): return
        if not hype.availability_checked():
            self.hype_status.configure(text="Checking AI parts...\n\n"); return
        h = self.hype
        if not hype.reactions_available(resource_path("models")) or (self.smooth is not None and h.reactions is None):
            reactions = "Reactions: unavailable (model or onnxruntime missing)"
        elif self.smooth is None:
            reactions = "Reactions: ready, listens when a video loads"
        else:
            reactions = "Reactions: ready"
        if not hype.speech_available():
            speech = "Speech: unavailable (faster-whisper not installed)"
        elif not self.speech_var.get():
            speech = "Speech: off"
        elif h.speech is not None:
            speech = f"Speech: ready ({len(h.speech)} lines)"
        elif self.smooth is not None:
            minutes = self.duration / 60 / 50   # measured: a 4.3 h stream took ~5 min on CPU (silence is skipped)
            speech = f"Speech: runs on SCAN ({'under a minute' if minutes < 1 else f'about {round(minutes)} min'})"
        else:
            speech = "Speech: runs on SCAN"
        chat = f"Chat: {os.path.basename(h.chat_path)} ({len(h.chat):,} messages)" if h.chat else "Chat: none (optional, Twitch / YouTube replay)"
        self.hype_status.configure(text="\n".join((reactions, speech, chat)))

    def _transcribe_then_scan(self):
        path, duration = self.video_path, self.duration
        first_time = not os.path.isdir(os.path.join(WHISPER_DIR, f"models--Systran--faster-whisper-{hype.WHISPER_MODEL}"))

        def work(progress):
            progress(0, "DOWNLOADING SPEECH MODEL (ONE TIME, ~145 MB)..." if first_time else "LOADING SPEECH MODEL...")
            try: transcriber = hype.SpeechTranscriber(WHISPER_DIR)
            except Exception as e:
                log_error(traceback.format_exc())
                raise ScoutError(f"Couldn't load the speech model{' (check your internet connection for the one-time download)' if first_time else ''}:\n\n{e}")
            lines = transcriber.transcribe(lambda start, seconds: read_audio_chunk(path, start, seconds), duration,
                                           lambda f: progress(f, f"LISTENING TO SPEECH {int(f * 100)}%"), self.cancel_event.is_set)
            if lines is None: raise Cancelled()
            cache = speech_cache_path(path)
            if cache: hype.save_speech(cache, lines)
            return lines

        def done(lines):
            if path != self.video_path: return
            self.hype.speech = lines
            self._update_hype_status()
            self.scan()

        self._run_in_background("PREPARING SPEECH...", work, done)

    def choose_chat_file(self):
        if not self.video_path: self._show_warning("Load a video first, then its chat replay."); return
        path = filedialog.askopenfilename(title="Chat replay for this video", initialdir=os.path.dirname(self.video_path),
                                          filetypes=[("Chat replay", "*.json"), ("All files", "*.*")])
        if not path: return
        try: chat = hype.load_chat(path)
        except (OSError, ValueError) as e: self._show_error(f"Couldn't read that chat file:\n\n{e}"); return
        if not chat:
            self._show_warning("No chat messages found in that file.\n\nSupported: Twitch chat saved with TwitchDownloader (JSON) "
                               "and YouTube live chat saved with yt-dlp (.live_chat.json)."); return
        self.hype.chat, self.hype.chat_path = chat, path
        self._update_hype_status()
        self.status_label.configure(text=f"CHAT LOADED · {len(chat):,} MESSAGES")

    def clear_chat(self):
        self.hype.chat, self.hype.chat_path = None, ""
        self._update_hype_status()

    # --- REVIEW ---
    def _set_clips(self, clips):
        self.clips = clips
        for row in self.rows: row.frame.destroy()
        self.rows = []
        if clips: self.empty_label.pack_forget()
        else: self.empty_label.pack(pady=40)
        for n, clip in enumerate(clips, 1): self.rows.append(self._make_row(n, clip))
        self.current_row = None
        self._set_current_row(self.rows[0] if self.rows else None)
        self.list_frame.yview_moveto(0)
        self._refresh_counts(); self._draw_clip_spans()
        self._refresh_preview_buttons()

    def _make_row(self, number, clip):
        frame = tb.Frame(self.list_frame); frame.pack(fill=X, pady=2, padx=(0, 14))
        sel_var = tk.BooleanVar(value=clip.selected)
        title_var = tk.StringVar(value=clip.title)
        start_var = tk.StringVar(value=fmt_time(clip.start))
        end_var = tk.StringVar(value=fmt_time(clip.end))

        frame.columnconfigure(2, weight=1)
        tb.Checkbutton(frame, variable=sel_var, bootstyle="primary-round-toggle").grid(row=0, column=0, padx=(4, 6))
        num_label = tb.Label(frame, text=f"{number:02d}", width=3, style="RowNum.TLabel"); num_label.grid(row=0, column=1)
        tb.Entry(frame, textvariable=title_var, bootstyle="primary").grid(row=0, column=2, sticky="ew", padx=(4, 8))
        start_entry = tb.Entry(frame, textvariable=start_var, width=8, justify=CENTER, bootstyle="primary"); start_entry.grid(row=0, column=3)
        tb.Label(frame, text="–").grid(row=0, column=4, padx=3)
        end_entry = tb.Entry(frame, textvariable=end_var, width=8, justify=CENTER, bootstyle="primary"); end_entry.grid(row=0, column=5)
        length_label = tb.Label(frame, text="", width=6, foreground=TEXT_MUTED); length_label.grid(row=0, column=6, padx=6)

        row = ClipRow(clip, frame, sel_var, title_var, start_var, end_var, start_entry, end_entry, length_label, num_label)
        tb.Button(frame, text="PREVIEW", style="Bones.TButton", command=lambda: self._preview_row(row)).grid(row=0, column=7, padx=(4, 0))
        tb.Button(frame, text="DELETE", style="BonesDanger.TButton", command=lambda: self._delete_row(row)).grid(row=0, column=8, padx=(4, 0))
        if clip.tags:   # why Hype mode picked it
            tb.Label(frame, text="  ·  ".join(clip.tags), style="Tags.TLabel").grid(row=1, column=2, columnspan=7, sticky="w", padx=(6, 0), pady=(0, 4))

        # Clicking or typing anywhere in a row makes it the one the keyboard shortcuts act on.
        for widget in (frame, *frame.winfo_children()):
            widget.bind("<Button-1>", lambda e: self._set_current_row(row), add="+")
            widget.bind("<FocusIn>", lambda e: self._set_current_row(row), add="+")

        # Traces keep the clip in sync on every keystroke, so nothing typed is lost when you click straight to RENDER.
        sel_var.trace_add("write", lambda *_: self._on_select_changed(row))
        title_var.trace_add("write", lambda *_: self._on_title_edited(row))
        start_var.trace_add("write", lambda *_: self._on_time_edited(row))
        end_var.trace_add("write", lambda *_: self._on_time_edited(row))
        self._update_length_label(row)
        return row

    def _set_current_row(self, row):
        if row is self.current_row: return
        if self.current_row is not None and self.current_row in self.rows:
            self.current_row.num_label.configure(style="RowNum.TLabel")
        self.current_row = row
        if row is not None:
            row.num_label.configure(style="RowNumCurrent.TLabel")
            self._scroll_row_into_view(row)

    def _scroll_row_into_view(self, row):
        self.root.update_idletasks()
        total = self.list_frame.winfo_height()          # full height of the list
        if total <= 1: return
        first, last = (float(f) for f in self.list_frame.vscroll.get())   # visible part, as fractions
        top, bottom = row.frame.winfo_y(), row.frame.winfo_y() + row.frame.winfo_height()
        if top < first * total: self.list_frame.yview_moveto(top / total)
        elif bottom > last * total: self.list_frame.yview_moveto(bottom / total - (last - first))

    def _on_title_edited(self, row):
        row.clip.title = row.title_var.get()
        self._schedule_session_save()

    def _on_time_edited(self, row):
        start, end = parse_time(row.start_var.get()), parse_time(row.end_var.get())
        row.valid = start is not None and end is not None and start < end and end <= self.duration + 0.5
        style = "primary" if row.valid else "danger"
        row.start_entry.configure(bootstyle=style); row.end_entry.configure(bootstyle=style)
        if row.valid:
            # Leave the precise detected time alone unless the user actually changed the shown value.
            if fmt_time(start) != fmt_time(row.clip.start): row.clip.start = start
            if fmt_time(end) != fmt_time(row.clip.end): row.clip.end = min(end, self.duration)
            self._draw_clip_spans()
            self._schedule_session_save()
        self._update_length_label(row)

    def _update_length_label(self, row):
        row.length_label.configure(text=f"{row.clip.end - row.clip.start:.0f}s" if row.valid else "??")

    def _on_select_changed(self, row):
        row.clip.selected = row.sel_var.get()
        self._refresh_counts(); self._draw_clip_spans()
        self._schedule_session_save()

    def _delete_row(self, row):
        index = self.rows.index(row)
        self.clips.remove(row.clip); self.rows.remove(row); row.frame.destroy()
        if row is self.current_row:
            self.current_row = None
            if self.rows: self._set_current_row(self.rows[min(index, len(self.rows) - 1)])
        if not self.clips: self.empty_label.pack(pady=40)
        self._refresh_counts(); self._draw_clip_spans()
        self._schedule_session_save()
        self._refresh_preview_buttons()

    def _preview_row(self, row):
        self._set_current_row(row)
        if not row.valid: self._show_warning("Fix the start/end time first (it's shown in red)."); return
        index = self.rows.index(row)
        heading = f"Clip {row.num_label.cget('text')}  ·  {row.clip.title or '(untitled)'}"
        self.preview_row = row
        self._open_player(row.clip.start, row.clip.end, heading, index > 0, index < len(self.rows) - 1)

    # --- CLIP MEMORY ---
    def _schedule_session_save(self):
        if self._save_job is not None: self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(800, self._save_session_now)

    def _save_session_now(self):
        if self._save_job is not None:
            self.root.after_cancel(self._save_job); self._save_job = None
        if self.video_path: save_session(self.video_path, self.clips)

    # --- KEYBOARD SHORTCUTS ---
    def _bind_shortcuts(self):
        r = self.root
        r.bind_all("<Control-o>", lambda e: self._in_main_window(e) and self.load_video())
        r.bind_all("<Control-O>", lambda e: self._in_main_window(e) and self.load_video())
        r.bind_all("<F5>", lambda e: self._in_main_window(e) and self.scan())
        r.bind_all("<Control-r>", lambda e: self._in_main_window(e) and self.render())
        r.bind_all("<Control-R>", lambda e: self._in_main_window(e) and self.render())
        r.bind_all("<Escape>", lambda e: self._in_main_window(e) and self._on_escape(e))
        r.bind_all("<space>", lambda e: self._row_key(e, lambda row: self._preview_row(row)))
        r.bind_all("<Delete>", lambda e: self._row_key(e, self._delete_row))
        r.bind_all("<Up>", lambda e: self._row_key(e, lambda row: self._move_current(-1)))
        r.bind_all("<Down>", lambda e: self._row_key(e, lambda row: self._move_current(1)))

    def _in_main_window(self, event):
        """Shortcuts belong to the main window, not to pop-ups like the layout editor (Delete mustn't remove a clip there)."""
        try: return event.widget.winfo_toplevel() is self.root
        except AttributeError: return False

    def _row_key(self, event, action):
        """Row shortcuts stay out of the way while typing in a box or using a dropdown/button."""
        if not self._in_main_window(event): return
        if event.widget.winfo_class() in ("TEntry", "Entry", "TCombobox", "TSpinbox", "Text", "TButton", "TCheckbutton", "Listbox"): return
        if self.busy or not self.rows: return
        if self.current_row not in self.rows: self._set_current_row(self.rows[0])
        action(self.current_row)
        return "break"

    def _move_current(self, step):
        index = self.rows.index(self.current_row) + step
        self._set_current_row(self.rows[max(0, min(index, len(self.rows) - 1))])

    def _on_escape(self, event):
        self._stop_preview()
        self.root.focus_set()   # leave a text box so Space / arrows / Delete work again

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
        self.fig.set_facecolor(PANEL_BG)
        ax.set_facecolor(INPUT_BG)
        ax.set_ylim(DB_FLOOR, 0)
        ax.tick_params(colors=TEXT_MUTED, labelsize=8)
        for side in ("top", "right"): ax.spines[side].set_visible(False)
        for side in ("left", "bottom"): ax.spines[side].set_color(BUTTON_BORDER)
        ax.set_ylabel("dB", color=TEXT_MUTED, fontsize=8)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: fmt_time(x)))

    def _draw_waveform(self):
        ax = self.ax
        ax.clear(); self._style_axes()
        self.span_artists = []; self.limit_line = None
        if self.smooth is None:
            ax.set_xlim(0, 60)
            ax.text(30, -30, "Drop a video here" if self.dnd_enabled else "No video loaded", color=TEXT_MUTED, ha="center", va="center")
            self.canvas.draw_idle(); return

        # Shrink to ~4000 points for drawing: the loudest value per bin, so short peaks still show.
        n = len(self.smooth); bins = min(n, 4000)
        starts = np.linspace(0, n, bins + 1).astype(int)[:-1]
        widths = np.diff(np.append(starts, n))
        self.disp_t = (starts + widths / 2) * HOP_SECONDS
        self.disp_base = np.add.reduceat(self.base, starts) / widths
        loudness = np.maximum.reduceat(self.smooth, starts)

        ax.fill_between(self.disp_t, DB_FLOOR, loudness, color=self.palette["accent"], alpha=0.9, linewidth=0)
        self.limit_line, = ax.plot(self.disp_t, self.disp_base, color=TEXT, linestyle="--", linewidth=1.2, alpha=0.85)
        ax.set_xlim(0, self.duration)
        self._update_limit_line()
        self._draw_clip_spans()

    def _update_limit_line(self):
        if self.limit_line is None: return
        mode = DETECT_MODES[self.detect_var.get()]
        self.limit_line.set_visible(mode != "hype")   # Hype mode isn't about a loudness line
        if mode != "hype":
            self.limit_line.set_ydata(detection_limit(self.disp_base, mode, self.threshold_var.get(), self.jump_var.get()))
        self.canvas.draw_idle()

    def _draw_clip_spans(self):
        for artist in self.span_artists: artist.remove()
        self.span_artists = []
        if self.smooth is not None:
            for row in self.rows:   # label each block with its row's number, which stays put when others are deleted
                clip = row.clip
                color = self.palette["accent_hover"] if clip.selected else TEXT_MUTED
                self.span_artists.append(self.ax.axvspan(clip.start, clip.end, color=color, alpha=0.18 if clip.selected else 0.1, linewidth=0))
                self.span_artists.append(self.ax.text(clip.start, -2, str(int(row.num_label.cget("text"))), color=TEXT, fontsize=7, va="top", clip_on=True))
        self.canvas.draw_idle()

    def _on_wave_click(self, event):
        if self.smooth is None or event.inaxes is not self.ax or event.xdata is None: return
        t = float(event.xdata)
        for row in self.rows:
            if row.clip.start <= t <= row.clip.end: self._preview_row(row); return
        self.preview(max(0.0, t - 2), min(self.duration, t + 13))

    # --- PREVIEW ---
    def preview(self, start, end):
        """A spot clicked on the waveform, not one of the clips: no previous / next."""
        if not self.video_path: return
        self.preview_row = None
        self._open_player(start, end, f"Preview  ·  {fmt_time(start)} - {fmt_time(end)}", False, False)

    def _open_player(self, start, end, heading, has_prev, has_next):
        if self.preview_window is None:
            self.preview_window = player.ClipPreview(self.root, self.settings["preview_volume"], self._step_preview,
                                                     lambda v: self.settings.update(preview_volume=v), self._on_player_closed)
        try:
            self.preview_window.load(self.video_path, start, end, heading, has_prev, has_next)
        except Exception:   # a file the player can't open or a missing library: ffplay can still show it
            log_error(traceback.format_exc())
            self.preview_window.close()
            self._preview_with_ffplay(start, end)

    def _preview_with_ffplay(self, start, end):
        cmd = [tool("ffplay"), "-hide_banner", "-loglevel", "error", "-autoexit", "-x", "960", "-y", "540",
               "-window_title", f"Preview {fmt_time(start)} - {fmt_time(end)}",
               "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", self.video_path]
        try: self.preview_proc = start_process(cmd, low_priority=False)
        except ScoutError as e: self._show_error(str(e))

    def _step_preview(self, step):
        if self.preview_row not in self.rows: return
        index = self.rows.index(self.preview_row) + step
        if 0 <= index < len(self.rows): self._preview_row(self.rows[index])

    def _refresh_preview_buttons(self):
        """The clip list changed: Previous / Next may have nowhere to go now."""
        if self.preview_window is None: return
        index = self.rows.index(self.preview_row) if self.preview_row in self.rows else None
        self.preview_window.set_neighbours(index is not None and index > 0, index is not None and index < len(self.rows) - 1)

    def _on_player_closed(self):
        self.preview_window, self.preview_row = None, None

    def _stop_preview(self):
        if self.preview_window is not None: self.preview_window.close()
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
        short_graph = None
        if mode == "short":
            layout = vertical.validate_layout(self.settings["short_layout"])
            if layout is None:   # first time: set the boxes, then carry on rendering
                self.open_short_editor(then_render=True); return
            short_graph = vertical.short_filtergraph(layout, self.video_size[0] / self.video_size[1])
        ext = os.path.splitext(self.video_path)[1] if mode == "copy" else ".mp4"
        try: os.makedirs(out_dir, exist_ok=True)
        except OSError as e: self._show_error(f"Couldn't create the export folder:\n{e}"); return
        taken = set()
        jobs = [(r.clip, unique_path(out_dir, safe_filename(r.clip.title), ext, taken)) for r in selected_rows]
        src = self.video_path

        def work(progress):
            failures, exported = [], []
            for i, (clip, out) in enumerate(jobs):
                name = os.path.basename(out)
                progress(i / len(jobs), f"RENDERING {i + 1}/{len(jobs)}: {name}")
                error = run_export(build_export_cmd(src, clip, out, mode, short_graph), out, clip.end - clip.start,
                                   lambda f: progress((i + f) / len(jobs)), self.cancel_event)
                if error: failures.append(f"{name}:\n{error}")
                else: exported.append(out)
            return failures, exported

        def done(result):
            failures, exported = result
            self.status_label.configure(text=f"EXPORTED {len(exported)}/{len(jobs)}")
            if failures:
                log_error("\n\n".join(failures))
                self._show_error(f"Exported {len(exported)} of {len(jobs)} clips. These failed:\n\n" + "\n\n".join(failures[:3])
                                 + (f"\n\n...and {len(failures) - 3} more" if len(failures) > 3 else ""))
            if exported:
                self.last_export_dir = out_dir
                choice = self._ask(f"Exported {len(exported)} clip(s) to:\n{out_dir}", "Done",
                                   ["Close:secondary", "Open folder:secondary", "Send to Caption Studio:primary"])
                if choice == "Open folder": os.startfile(out_dir)
                elif choice == "Send to Caption Studio": self.open_caption_studio(exported[0])

        self._run_in_background("RENDERING...", work, done)

    # --- VERTICAL SHORT ---
    def _update_short_label(self):
        saved = vertical.validate_layout(self.settings["short_layout"])
        self.short_label.configure(text="Vertical short: webcam + gameplay layout saved" if saved
                                   else "Vertical short: place the webcam and gameplay boxes, then export with \"Vertical short\"")

    def open_short_editor(self, then_render=False):
        if self.busy: return
        if not self.video_path: self._show_warning("Load a video first, so the layout can be set on a real frame."); return
        aspect = self.video_size[0] / self.video_size[1]
        layout = vertical.validate_layout(self.settings["short_layout"]) or vertical.default_layout(aspect)
        row = self.current_row if self.current_row in self.rows else None
        start = row.clip.peak if row else self.duration / 2
        path = self.video_path

        def saved(new_layout):
            self.settings["short_layout"] = new_layout
            self._update_short_label()
            self.export_var.set(next(k for k, v in EXPORT_MODES.items() if v == "short"))
            self.status_label.configure(text="VERTICAL LAYOUT SAVED")
            if then_render: self.render()

        vertical.ShortLayoutEditor(self.root, layout, aspect, self.duration, start, lambda t: grab_frame(path, t),
                                   self.ui_queue.put, self.palette, saved)

    # --- BONES' CAPTION STUDIO ---
    def open_caption_studio(self, clip_path=None):
        """Starts Caption Studio if it's installed, and opens the clips folder beside it so a clip can be dragged in.
        If it isn't installed, offers the download instead."""
        self.root.configure(cursor="watch"); self.root.update_idletasks()
        try: exe = find_caption_studio(self.settings["caption_studio_path"])
        finally: self.root.configure(cursor="")
        if exe is None: self._offer_caption_studio(); return

        # Caption Studio ignores the extra argument today; it's passed so a future version can open the clip directly.
        try: subprocess.Popen([exe] + ([clip_path] if clip_path else []), cwd=os.path.dirname(exe), creationflags=NORMAL_PRIORITY)
        except OSError as e: self._show_error(f"Couldn't start {CAPTION_STUDIO_NAME}:\n\n{e}"); return
        self.settings["caption_studio_path"] = exe
        folder = self.last_export_dir or self.out_var.get().strip()
        if folder and os.path.isdir(folder):
            os.startfile(folder)
            self.status_label.configure(text="CAPTION STUDIO OPENED · DRAG A CLIP INTO IT")
        else:
            self.status_label.configure(text="CAPTION STUDIO OPENED")

    def _offer_caption_studio(self):
        choice = self._ask(f"{CAPTION_STUDIO_NAME} isn't installed on this PC.\n\n{CAPTION_STUDIO_PITCH}", CAPTION_STUDIO_NAME,
                           ["Close:secondary", "I already have it...:secondary", "Get it:primary"])
        if choice == "Get it":
            if CAPTION_STUDIO_URL: webbrowser.open(CAPTION_STUDIO_URL)
            else: self._show_info(f"The {CAPTION_STUDIO_NAME} download link is coming soon.")
        elif choice == "I already have it...":
            exe = filedialog.askopenfilename(title=f"Find {CAPTION_STUDIO_EXE}",
                                             filetypes=[(CAPTION_STUDIO_NAME, CAPTION_STUDIO_EXE), ("Programs", "*.exe")])
            if exe:
                self.settings["caption_studio_path"] = exe
                self.open_caption_studio()

    # --- MESSAGES ---
    def _show_info(self, message): Messagebox.show_info(message, "Bones Highlight Scout", parent=self.root)
    def _show_warning(self, message): Messagebox.show_warning(message, "Bones Highlight Scout", parent=self.root)
    def _show_error(self, message): Messagebox.show_error(message, "Bones Highlight Scout", parent=self.root)

    def _ask(self, message, title, buttons):
        """Themed dialog with custom buttons ("Text:bootstyle"); returns the clicked button's text or None."""
        dialog = MessageDialog(message, title=title, buttons=buttons, parent=self.root, default=buttons[-1].split(":")[0])
        dialog.show()
        return dialog.result

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
        self._save_session_now()
        self.settings.update({
            "detect_mode": self.detect_var.get(),
            "threshold_db": round(float(self.threshold_var.get()), 1),
            "jump_db": round(float(self.jump_var.get()), 1),
            "pre_roll": float(round(self.pre_var.get())),
            "post_roll": float(round(self.post_var.get())),
            "max_clips": int(round(self.max_var.get())),
            "export_mode": self.export_var.get(),
            "listen_speech": self.speech_var.get(),
            "colorblind": self.colorblind_var.get(),
        })
        save_settings(self.settings)
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    HighlightScout().run()
