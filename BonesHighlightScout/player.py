# ==============================================================================
# CLIP PLAYER for Bones Highlight Scout
# ==============================================================================
# The preview window: previous / next clip, play / pause, a scrubber and a volume slider.
# PyAV decodes on a background thread, Windows' waveOut plays the sound, and the sound's position is the
# clock the picture follows, so they stay in sync. If there's no sound device it runs on a plain timer.

import collections
import ctypes
import threading
import time
import tkinter as tk

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from PIL import Image, ImageTk

MAX_W, MAX_H = 960, 540      # size of the picture in the window
MAX_FPS = 30                 # enough for a preview; 60 fps streams skip every other frame
AUDIO_AHEAD = 0.8            # seconds of sound / picture decoded ahead of what's playing
VIDEO_AHEAD = 1.0


# --- SOUND (Windows waveOut) ---
class _WaveFormat(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("wFormatTag", ctypes.c_ushort), ("nChannels", ctypes.c_ushort), ("nSamplesPerSec", ctypes.c_uint),
                ("nAvgBytesPerSec", ctypes.c_uint), ("nBlockAlign", ctypes.c_ushort), ("wBitsPerSample", ctypes.c_ushort),
                ("cbSize", ctypes.c_ushort)]


class _WaveHeader(ctypes.Structure):
    _fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", ctypes.c_uint), ("dwBytesRecorded", ctypes.c_uint),
                ("dwUser", ctypes.c_size_t), ("dwFlags", ctypes.c_uint), ("dwLoops", ctypes.c_uint),
                ("lpNext", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]


class _MMTime(ctypes.Structure):
    _fields_ = [("wType", ctypes.c_uint), ("value", ctypes.c_uint), ("pad", ctypes.c_uint)]


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

    def __init__(self, a, b, c, d):
        super().__init__(a, b, c, (ctypes.c_ubyte * 8)(*d))


def _com_call(obj, index, restype, argtypes, *args):
    """Calls method number `index` of a COM object through its table of functions."""
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(table[index])(obj, *args)


def _default_endpoint_id():
    """The ID of Windows' default sound output, from Core Audio."""
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)
    ole32.CoCreateInstance.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p)
    ole32.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    clsid = _GUID(0xBCDE0395, 0xE52F, 0x467C, (0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E))     # MMDeviceEnumerator
    iid = _GUID(0xA95664D2, 0x9614, 0x4F35, (0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6))       # IMMDeviceEnumerator
    enumerator, device, text = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    if ole32.CoCreateInstance(ctypes.byref(clsid), None, 23, ctypes.byref(iid), ctypes.byref(enumerator)) != 0: return None
    try:
        # GetDefaultAudioEndpoint(eRender, eConsole), then GetId
        if _com_call(enumerator, 4, ctypes.c_long, (ctypes.c_int, ctypes.c_int, ctypes.c_void_p), 0, 0, ctypes.byref(device)) != 0: return None
        try:
            if _com_call(device, 5, ctypes.c_long, (ctypes.c_void_p,), ctypes.byref(text)) != 0: return None
            result = ctypes.wstring_at(text)
            ole32.CoTaskMemFree(text)
            return result
        finally:
            _com_call(device, 2, ctypes.c_ulong, ())
    finally:
        _com_call(enumerator, 2, ctypes.c_ulong, ())


def _default_device_number(winmm):
    """waveOut's number for Windows' default output. With lots of virtual audio devices (Sonar, VB-Cable...)
    waveOut's own "default device" can fail, so its devices are matched against Core Audio's default by ID."""
    winmm.waveOutMessage.argtypes = (ctypes.c_size_t, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p)
    winmm.waveOutMessage.restype = ctypes.c_uint
    wanted = _default_endpoint_id()
    if wanted is None: return 0
    for number in range(winmm.waveOutGetNumDevs()):
        size = ctypes.c_size_t(0)
        if winmm.waveOutMessage(number, 0x0812, ctypes.byref(size), None) != 0 or not size.value: continue   # DRV_QUERYFUNCTIONINSTANCEIDSIZE
        text = ctypes.create_unicode_buffer(size.value // ctypes.sizeof(ctypes.c_wchar) + 1)
        if winmm.waveOutMessage(number, 0x0811, text, ctypes.c_void_p(size.value)) == 0 and text.value.lower() == wanted.lower():   # DRV_QUERYFUNCTIONINSTANCEID
            return number
    return 0


class WaveOut:
    RATE = 48000
    BYTES_PER_SEC = RATE * 4     # 16-bit stereo
    _DONE = 1
    _TIME_BYTES = 4

    def __init__(self):
        self.w = ctypes.windll.winmm
        for name in ("waveOutPrepareHeader", "waveOutUnprepareHeader", "waveOutWrite"):
            getattr(self.w, name).argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint)
        self.w.waveOutGetPosition.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint)
        self.w.waveOutSetVolume.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        for name in ("waveOutPause", "waveOutRestart", "waveOutReset", "waveOutClose"):
            getattr(self.w, name).argtypes = (ctypes.c_void_p,)
        self.w.waveOutOpen.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_uint)
        fmt = _WaveFormat(1, 2, self.RATE, self.BYTES_PER_SEC, 4, 16, 0)
        self.handle = ctypes.c_void_p()
        wave_mapper = ctypes.c_size_t(-1).value    # waveOut's own "default device"
        if self.w.waveOutOpen(ctypes.byref(self.handle), wave_mapper, ctypes.byref(fmt), 0, 0, 0) != 0:
            self.device = _default_device_number(self.w)
            if self.w.waveOutOpen(ctypes.byref(self.handle), self.device, ctypes.byref(fmt), 0, 0, 0) != 0:
                raise OSError("no sound device")
        self.pending = collections.deque()
        self.written = 0

    def _reap(self):
        while self.pending and self.pending[0][0].dwFlags & self._DONE:
            header, _ = self.pending.popleft()
            self.w.waveOutUnprepareHeader(self.handle, ctypes.byref(header), ctypes.sizeof(header))

    def write(self, data):
        self._reap()
        buffer = ctypes.create_string_buffer(data, len(data))
        header = _WaveHeader()
        header.lpData = ctypes.cast(buffer, ctypes.c_void_p)
        header.dwBufferLength = len(data)
        self.w.waveOutPrepareHeader(self.handle, ctypes.byref(header), ctypes.sizeof(header))
        self.w.waveOutWrite(self.handle, ctypes.byref(header), ctypes.sizeof(header))
        self.pending.append((header, buffer))
        self.written += len(data)

    def played_seconds(self):
        position = _MMTime(self._TIME_BYTES, 0, 0)
        self.w.waveOutGetPosition(self.handle, ctypes.byref(position), ctypes.sizeof(position))
        return position.value / self.BYTES_PER_SEC

    def queued_seconds(self):
        return self.written / self.BYTES_PER_SEC - self.played_seconds()

    def set_paused(self, paused):
        (self.w.waveOutPause if paused else self.w.waveOutRestart)(self.handle)

    def reset(self, paused):
        self.w.waveOutReset(self.handle)   # drops what's queued and sets the position back to 0
        self._reap()
        self.written = 0
        self.set_paused(paused)

    def set_volume(self, volume):
        level = int(0xFFFF * volume * volume)   # squared, because hearing isn't linear
        self.w.waveOutSetVolume(self.handle, level | (level << 16))

    def close(self):
        self.w.waveOutReset(self.handle)
        self._reap()
        self.w.waveOutClose(self.handle)


# --- DECODING ---
class Engine:
    """Plays one video file; clips are just ranges of it. All public methods are for the UI thread."""

    def __init__(self, path, max_size=(MAX_W, MAX_H)):
        import av
        self.av = av
        self.path = path
        self.container = av.open(path)
        self.vs = self.container.streams.video[0]
        self.vs.thread_type = "AUTO"
        self.astream = self.container.streams.audio[0] if self.container.streams.audio else None
        self.offset = (self.container.start_time or 0) / av.time_base   # file timestamps may not start at 0
        k = min(max_size[0] / self.vs.width, max_size[1] / self.vs.height)
        self.size = (max(2, int(self.vs.width * k) // 2 * 2), max(2, int(self.vs.height * k) // 2 * 2))
        try: self.wave = WaveOut() if self.astream is not None else None
        except (OSError, AttributeError): self.wave = None

        self.lock = threading.Lock()
        self.frames = collections.deque()
        self.seek_to = None
        self.show_next_frame = True      # right after a seek, show a frame even while paused
        self.paused = False
        self.stopping = False
        self.eof = False
        self.end = 0.0
        self.target = 0.0
        self.audio_base = None           # time of the first sound sample since the last seek
        self.timer_pos, self.timer_mark = 0.0, time.perf_counter()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    # --- controls ---
    def play_range(self, start, end):
        with self.lock: self.end = end
        self.seek(start)

    def seek(self, seconds):
        with self.lock:
            self.seek_to = seconds
            self.show_next_frame = True

    def set_paused(self, paused):
        with self.lock:
            if paused == self.paused: return
            self.timer_pos, self.timer_mark = self._timer_now(), time.perf_counter()
            self.paused = paused
            if self.wave: self.wave.set_paused(paused)

    def set_volume(self, volume):
        if self.wave: self.wave.set_volume(volume)

    def close(self):
        self.stopping = True
        self.thread.join(2)
        try:
            if self.wave: self.wave.close()
            self.container.close()
        except Exception: pass

    # --- clock ---
    def _timer_now(self):
        return self.timer_pos + (0.0 if self.paused else time.perf_counter() - self.timer_mark)

    def clock(self):
        with self.lock:
            if self.seek_to is not None: return self.seek_to
            if not self.wave: return self._timer_now()
            if self.audio_base is None: return self.target
            return self.audio_base + self.wave.played_seconds()

    def next_frame(self):
        """The newest decoded frame that's due, or None."""
        now = self.clock()
        with self.lock:
            if self.seek_to is not None or not self.frames: return None
            if self.show_next_frame:
                self.show_next_frame = False
                return self.frames.popleft()
            if self.paused: return None
            frame = None
            while self.frames and self.frames[0][0] <= now:
                frame = self.frames.popleft()
            return frame

    def finished(self):
        """Past the end of the range, or the file ran out and everything has played."""
        if self.clock() >= self.end - 0.02: return True
        with self.lock:
            if not self.eof or self.frames or self.seek_to is not None: return False
            return not self.wave or self.wave.queued_seconds() <= 0.01

    # --- background thread ---
    def _apply_seek(self, seconds, audio):
        self.container.seek(int((seconds + self.offset) / self.vs.time_base), stream=self.vs, backward=True)
        with self.lock:
            self.frames.clear()
            self.target = seconds
            self.audio_base = None
            self.eof = False
            self.timer_pos, self.timer_mark = seconds, time.perf_counter()
            if self.wave: self.wave.reset(self.paused)
            if self.seek_to == seconds: self.seek_to = None
        return self.av.AudioResampler(format="s16", layout="stereo", rate=WaveOut.RATE) if audio else None

    def _handle_video(self, frame, state):
        t = float(frame.time) - self.offset
        if t < self.target - 0.001: return
        if t - state["last_video"] < 1 / (MAX_FPS + 1): return
        state["last_video"] = t
        pixels = frame.reformat(self.size[0], self.size[1], "rgb24").to_ndarray().tobytes()
        with self.lock: self.frames.append((t, pixels))
        if t > self.end + 0.2: state["past_end"] = True

    def _handle_audio(self, frame, resampler):
        t = float(frame.time) - self.offset
        if t >= self.end + 0.2: return
        for out in resampler.resample(frame):
            data = bytes(out.planes[0])[:out.samples * 4]
            if t < self.target:     # the frame straddles the seek point: drop the part before it
                cut = int((self.target - t) * WaveOut.RATE) * 4
                data, t = data[cut:], self.target
            if not data: continue
            with self.lock:
                if self.audio_base is None: self.audio_base = t
                self.wave.write(data)

    def _have_enough(self):
        with self.lock: video = self.frames[-1][0] - self.frames[0][0] if len(self.frames) > 1 else 0.0
        audio = self.wave.queued_seconds() if self.wave else AUDIO_AHEAD
        return (video >= VIDEO_AHEAD and audio >= AUDIO_AHEAD) or video >= 5.0   # 5 s cap: memory, if the sound track has a gap

    def _run(self):
        streams = [self.vs] + ([self.astream] if self.wave else [])
        resampler, packets, state = None, None, {"last_video": -1.0, "past_end": False}
        while not self.stopping:
            try:
                with self.lock: wanted = self.seek_to
                if wanted is not None:
                    resampler = self._apply_seek(wanted, bool(self.wave))
                    packets = self.container.demux(*streams)
                    state = {"last_video": -1.0, "past_end": False}
                    continue
                if packets is None or state["past_end"] or self.eof or self._have_enough():
                    time.sleep(0.01); continue
                packet = next(packets, None)
                if packet is None:
                    self.eof = True; continue
                for frame in packet.decode():
                    if packet.stream.type == "video": self._handle_video(frame, state)
                    else: self._handle_audio(frame, resampler)
            except self.av.error.EOFError:
                self.eof = True
            except Exception:   # a damaged spot in the file: treat it as the end rather than crash the preview
                self.eof = True


# --- WINDOW ---
def _fmt(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class PlaybackControls:
    """Play / pause and the scrubber, shared by the preview window and the vertical layout editor.
    The window provides engine, start, end, scrubbing, was_paused, ended, scrubber, pos_var, play_btn, time_label and _show()."""

    def toggle(self):
        if not self.engine: return
        if self.ended:   # at the end: play again from the top
            self.ended = False
            self.engine.seek(self.start)
            self.engine.set_paused(False)
            self.play_btn.configure(text="PAUSE")
            return
        paused = not self.engine.paused
        self.engine.set_paused(paused)
        self.play_btn.configure(text="PLAY" if paused else "PAUSE")

    def _jump(self, seconds):
        if not self.engine: return
        target = min(max(self.engine.clock() + seconds, self.start), self.end - 0.1)
        self.ended = False
        self.play_btn.configure(text="PLAY" if self.engine.paused else "PAUSE")
        self.engine.seek(target)

    def _tick(self):
        """Shows the frame that's due and keeps the scrubber, the time and the end of the clip up to date."""
        if not self.engine: return
        frame = self.engine.next_frame()
        if frame: self._show(frame[1])
        if not self.scrubbing:
            now = self.engine.clock()
            self.pos_var.set(min(max(now - self.start, 0), self.end - self.start))
            self._update_time(now - self.start)
            if not self.ended and not self.engine.paused and self.engine.finished():
                self.ended = True
                self.engine.set_paused(True)
                self.play_btn.configure(text="REPLAY")

    def _update_time(self, position):
        self.time_label.configure(text=f"{_fmt(position)} / {_fmt(self.end - self.start)}")

    def _bind_scrubber(self):
        self._seek_job = None
        self.scrubber.bind("<ButtonPress-1>", self._scrub_press)
        self.scrubber.bind("<B1-Motion>", self._scrub_move)
        self.scrubber.bind("<ButtonRelease-1>", self._scrub_release)

    def _scrub_position(self, event):
        width = max(self.scrubber.winfo_width() - 16, 1)    # the handle's half width on each side
        return min(max((event.x - 8) / width, 0.0), 1.0) * (self.end - self.start)

    def _scrub_press(self, event):
        if not self.engine: return "break"
        self.scrubbing = True
        self.was_paused = self.engine.paused
        self.engine.set_paused(True)
        self._scrub_move(event)
        return "break"

    def _scrub_move(self, event):
        if not self.scrubbing: return "break"
        position = self._scrub_position(event)
        self.pos_var.set(position)
        self._update_time(position)
        if self._seek_job is None:   # seek at most ~12 times a second while dragging
            self._seek_job = self.after(80, self._scrub_seek)
        return "break"

    def _scrub_seek(self):
        self._seek_job = None
        if self.engine: self.engine.seek(self.start + self.pos_var.get())

    def _scrub_release(self, event):
        if not self.scrubbing: return "break"
        self.scrubbing = False
        if self._seek_job is not None:
            self.after_cancel(self._seek_job); self._seek_job = None
        position = self._scrub_position(event)
        self.pos_var.set(position)
        self.engine.seek(self.start + position)
        self.ended = False
        self.engine.set_paused(self.was_paused)
        self.play_btn.configure(text="PLAY" if self.was_paused else "PAUSE")
        return "break"


class ClipPreview(PlaybackControls, tb.Toplevel):
    """on_step(-1 or +1) asks the app for the previous / next clip; on_volume(v) lets it remember the volume."""

    def __init__(self, parent, volume, on_step, on_volume, on_close):
        super().__init__(parent)
        self.title("Clip preview")
        self.on_step, self.on_volume, self.on_close = on_step, on_volume, on_close
        self.engine = None
        self.start = self.end = 0.0
        self.scrubbing = False
        self.was_paused = False
        self.ended = False
        self.photo = None

        self.heading = tb.Label(self, text="", font=("Segoe UI", 10, "bold"), padding=(12, 10, 12, 6))
        self.heading.pack(fill=X)
        self.canvas = tk.Canvas(self, width=MAX_W, height=MAX_H, bg="#000000", highlightthickness=0)
        self.canvas.pack(padx=12)
        self.image_item = self.canvas.create_image(MAX_W // 2, MAX_H // 2)

        scrub = tb.Frame(self, padding=(12, 8, 12, 0)); scrub.pack(fill=X)
        self.time_label = tb.Label(scrub, text="00:00 / 00:00", width=14); self.time_label.pack(side=RIGHT, padx=(10, 0))
        self.pos_var = tk.DoubleVar(value=0)
        self.scrubber = tb.Scale(scrub, from_=0, to=1, variable=self.pos_var, bootstyle="primary", takefocus=False)
        self.scrubber.pack(side=LEFT, fill=X, expand=YES)
        self._bind_scrubber()

        bar = tb.Frame(self, padding=12); bar.pack(fill=X)
        self.prev_btn = tb.Button(bar, text="PREVIOUS", style="Bones.TButton", takefocus=False, command=lambda: self.on_step(-1))
        self.prev_btn.pack(side=LEFT)
        self.play_btn = tb.Button(bar, text="PAUSE", style="BonesAccent.TButton", width=9, takefocus=False, command=self.toggle)
        self.play_btn.pack(side=LEFT, padx=6)
        self.next_btn = tb.Button(bar, text="NEXT", style="Bones.TButton", takefocus=False, command=lambda: self.on_step(1))
        self.next_btn.pack(side=LEFT)

        self.volume_label = tb.Label(bar, text="", width=5, anchor="e"); self.volume_label.pack(side=RIGHT)
        self.volume_var = tk.DoubleVar(value=volume)
        tb.Scale(bar, from_=0, to=1, variable=self.volume_var, length=170, bootstyle="primary", takefocus=False,
                 command=lambda v: self._set_volume(float(v))).pack(side=RIGHT, padx=8)
        tb.Label(bar, text="VOLUME").pack(side=RIGHT)
        self.volume_label.configure(text=f"{int(volume * 100)}%")

        self.bind("<space>", lambda e: self.toggle())
        self.bind("<Left>", lambda e: self._jump(-5))
        self.bind("<Right>", lambda e: self._jump(5))
        self.bind("<Prior>", lambda e: self.on_step(-1))
        self.bind("<Next>", lambda e: self.on_step(1))
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)

        # Open centred over the main window.
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_reqheight()) // 2)
        self.geometry(f"+{x}+{y}")
        self._poll()

    # --- loading ---
    def load(self, path, start, end, heading, has_prev, has_next):
        if self.engine is None or self.engine.path != path:
            if self.engine: self.engine.close()
            self.engine = Engine(path)    # raises if the file can't be opened; the app falls back to ffplay
            self.engine.set_volume(self.volume_var.get())
        self.start, self.end, self.ended = start, end, False
        self.heading.configure(text=heading)
        self.scrubber.configure(from_=0, to=max(end - start, 0.1))
        self.pos_var.set(0)
        self.prev_btn.configure(state=NORMAL if has_prev else DISABLED)
        self.next_btn.configure(state=NORMAL if has_next else DISABLED)
        self.engine.play_range(start, end)
        self.engine.set_paused(False)
        self.play_btn.configure(text="PAUSE")
        self.lift(); self.focus_force()

    def set_neighbours(self, has_prev, has_next):
        self.prev_btn.configure(state=NORMAL if has_prev else DISABLED)
        self.next_btn.configure(state=NORMAL if has_next else DISABLED)

    # --- playback ---
    def _set_volume(self, volume):
        self.volume_label.configure(text=f"{int(volume * 100)}%")
        if self.engine: self.engine.set_volume(volume)
        self.on_volume(volume)

    def _poll(self):
        if not self.winfo_exists(): return
        self._tick()
        self.after(15, self._poll)

    def _show(self, pixels):
        image = Image.frombuffer("RGB", self.engine.size, pixels, "raw", "RGB", 0, 1)
        if self.photo is None or (self.photo.width(), self.photo.height()) != image.size:
            self.photo = ImageTk.PhotoImage(image, master=self)
            self.canvas.itemconfigure(self.image_item, image=self.photo)
        else:
            self.photo.paste(image)

    # --- closing ---
    def close(self):
        engine, self.engine = self.engine, None
        if engine: engine.close()
        self.on_close()
        self.destroy()
