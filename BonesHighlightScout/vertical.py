# ==============================================================================
# VERTICAL SHORT LAYOUT for Bones Highlight Scout
# ==============================================================================
# Twitch-style portrait layout. Two boxes on the video say WHAT to show (cam, game); two boxes on the 9:16
# preview say WHERE it goes (cam_out, game_out), and they can change without touching the selection. The webcam
# is always drawn on top. Boxes are [x, y, w, h] fractions of their frame, so a layout works at any resolution.
# An output box always keeps its selection's shape, so nothing gets stretched or cut off.
# Clips follow a shared layout unless they've been given their own.

import math
import tkinter as tk

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageTk

from player import Engine, PlaybackControls

OUT_W, OUT_H = 1080, 1920
MIN_BOX = 0.04                       # smallest source box, as a fraction of the frame
MIN_OUT = 0.1                        # narrowest box in the short
MAX_CAM_H = 0.6                      # the position buttons won't let the webcam take more of the short than this
HANDLE = 7                           # corner handle size on the source view (half width)
OUT_HANDLE = 6                       # the same on the preview
EDGE_GRAB = 5                        # how close to a side of a source box counts as grabbing that side (pixels)
SOURCE_W, SOURCE_H = 640, 480        # the most room the video takes in the editor
PREVIEW_W, PREVIEW_H = 270, 480
OWN_MARK = "  ●"                     # after a clip's name in the list: it has its own layout

DEFAULT_SHADOW = {"on": False, "angle": 45.0, "distance": 16.0, "intensity": 70.0, "softness": 20.0}
DEFAULT_BLUR = {"on": True, "amount": 20.0}


# --- GEOMETRY ---
def _even(n):
    return max(2, int(round(n / 2)) * 2)


def _clamp_box(box):
    x, y, w, h = box
    w, h = min(w, 1.0), min(h, 1.0)
    return [min(max(x, 0.0), 1.0 - w), min(max(y, 0.0), 1.0 - h), w, h]


def _out_ratio(source, aspect):
    """Height per unit of width of an output box, so it has the same shape as its selection."""
    return (source[3] / (source[2] * aspect)) * (OUT_W / OUT_H)


def fit_out(out, source, aspect):
    """Keeps the output box's size and centre but gives it the selection's shape."""
    ratio = _out_ratio(source, aspect)
    cx, cy = out[0] + out[2] / 2, out[1] + out[3] / 2
    w = min(out[2], 1.0, 1.0 / ratio)
    h = w * ratio
    return _clamp_box([cx - w / 2, cy - h / 2, w, h])


def _contain(source, aspect, y0, y1):
    """The biggest output box with the selection's shape that fits between two heights in the short."""
    ratio = _out_ratio(source, aspect)
    room = y1 - y0
    w = min(1.0, room / ratio)
    h = w * ratio
    return [(1.0 - w) / 2, y0 + (room - h) / 2, w, h]


def _free_region(cam_out):
    """The part of the short the gameplay has to itself, given where the webcam is."""
    centre = cam_out[1] + cam_out[3] / 2
    if centre < 0.4: return cam_out[1] + cam_out[3], 1.0
    if centre > 0.6: return 0.0, cam_out[1]
    return 0.0, 1.0


def place_webcam(layout, where, aspect):
    """The position buttons: webcam at the top, middle or bottom, gameplay fitted around it."""
    ratio = _out_ratio(layout["cam"], aspect)
    h = min(ratio, MAX_CAM_H)
    w = h / ratio
    y = {"top": 0.0, "middle": (1.0 - h) / 2, "bottom": 1.0 - h}[where]
    layout["cam_out"] = _clamp_box([(1.0 - w) / 2, y, w, h])
    layout["game_out"] = _contain(layout["game"], aspect, *_free_region(layout["cam_out"]))


def fill_gap(layout, aspect):
    """Reshapes the gameplay selection so it fills the space beside the webcam, with no background showing."""
    y0, y1 = _free_region(layout["cam_out"])
    slot = (y1 - y0) * OUT_H / OUT_W      # slot height per unit of width, in pixels of the short
    x, y, w, h = layout["game"]
    cx, cy = x + w / 2, y + h / 2
    h = w * aspect * slot
    if h > 1.0: w, h = w / h, 1.0
    layout["game"] = _clamp_box([cx - w / 2, cy - h / 2, w, h])
    layout["game_out"] = [0.0, y0, 1.0, y1 - y0]


def default_layout(aspect):
    w = 0.25
    cam = [0.02, 0.02, w, w * aspect * 9 / 16]     # a 16:9 webcam in the top-left corner
    layout = {"cam": cam, "game": [0.25, 0.0, 0.5, 1.0], "cam_out": [0, 0, 1, 1], "game_out": [0, 0, 1, 1],
              "shadow": dict(DEFAULT_SHADOW), "blur": dict(DEFAULT_BLUR)}
    place_webcam(layout, "top", aspect)
    fill_gap(layout, aspect)
    return layout


def copy_layout(layout):
    copied = {key: list(layout[key]) for key in ("cam", "game", "cam_out", "game_out")}
    copied["shadow"], copied["blur"] = dict(layout["shadow"]), dict(layout["blur"])
    return copied


def validate_layout(layout, aspect=16 / 9):
    """A complete layout, or None if it's unusable (a settings file can hold anything).
    Layouts saved before the short had its own boxes (they have a "style" instead) are converted."""
    try:
        boxes = {}
        for key in ("cam", "game"):
            x, y, w, h = (float(v) for v in layout[key])
            if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > 1.001 or y + h > 1.001: return None
            boxes[key] = [x, y, w, h]
        if "cam_out" in layout and "game_out" in layout:
            for key in ("cam_out", "game_out"):
                boxes[key] = _clamp_box([float(v) for v in layout[key]])
                if boxes[key][2] <= 0 or boxes[key][3] <= 0: return None
        else:
            cam_h = min(_out_ratio(boxes["cam"], aspect), 2 / 3)
            boxes["cam_out"] = [0.0, 0.0, 1.0, cam_h]
            boxes["game_out"] = [0.0, 0.0, 1.0, 1.0] if layout["style"] == "overlay" else [0.0, cam_h, 1.0, 1.0 - cam_h]
        shadow, blur = dict(DEFAULT_SHADOW), dict(DEFAULT_BLUR)
        shadow.update({k: type(DEFAULT_SHADOW[k])(v) for k, v in layout.get("shadow", {}).items() if k in DEFAULT_SHADOW})
        blur.update({k: type(DEFAULT_BLUR[k])(v) for k, v in layout.get("blur", {}).items() if k in DEFAULT_BLUR})
        return {**boxes, "shadow": shadow, "blur": blur}
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _shadow_offset(shadow):
    """Pixels in the short. Angle is the direction the shadow falls: 0 right, 90 down, 180 left, 270 up."""
    radians = math.radians(shadow["angle"])
    return shadow["distance"] * math.cos(radians), shadow["distance"] * math.sin(radians)


def _shadow_visible(shadow):
    return shadow["on"] and shadow["intensity"] > 0


# --- EXPORT ---
def short_filtergraph(layout, aspect):
    """ffmpeg filtergraph producing a 1080x1920 [v]: background, then gameplay, then shadow, then webcam."""
    cam_out, game_out = fit_out(layout["cam_out"], layout["cam"], aspect), fit_out(layout["game_out"], layout["game"], aspect)

    def part(source, out):
        x, y, w, h = source
        return f"crop=iw*{w:.5f}:ih*{h:.5f}:iw*{x:.5f}:ih*{y:.5f},scale={_even(out[2] * OUT_W)}:{_even(out[3] * OUT_H)},setsar=1"

    def corner(out):
        return round(out[0] * OUT_W), round(out[1] * OUT_H)

    blur, shadow = layout["blur"], layout["shadow"]
    if blur["on"] and blur["amount"] > 0:
        background = f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,crop={OUT_W}:{OUT_H},boxblur={int(blur['amount'])}:2,setsar=1"
    else:
        background = f"scale={OUT_W}:{OUT_H},setsar=1,drawbox=c=black:t=fill"
    gx, gy = corner(game_out)
    cx, cy = corner(cam_out)
    graph = (f"[0:v]split=3[b0][g0][c0];[b0]{background}[bg];[g0]{part(layout['game'], game_out)}[game];"
             f"[c0]{part(layout['cam'], cam_out)}[cam];[bg][game]overlay={gx}:{gy}[base]")
    if not _shadow_visible(shadow):
        return f"{graph};[base][cam]overlay={cx}:{cy}[v]"

    # The shadow is a black copy of the webcam on a transparent margin wide enough for its soft edge.
    soft = int(shadow["softness"])
    pad = soft * 2 + 2
    dx, dy = _shadow_offset(shadow)
    w, h = _even(cam_out[2] * OUT_W), _even(cam_out[3] * OUT_H)
    blurred = f",boxblur={soft}:2" if soft > 0 else ""
    return (f"{graph};[cam]format=rgba,split=2[cam1][s0];"
            f"[s0]drawbox=c=black:t=fill,colorchannelmixer=aa={shadow['intensity'] / 100:.3f},"
            f"pad={w + 2 * pad}:{h + 2 * pad}:{pad}:{pad}:color=black@0{blurred}[sh];"
            f"[base][sh]overlay={round(cx + dx - pad)}:{round(cy + dy - pad)}[withshadow];[withshadow][cam1]overlay={cx}:{cy}[v]")


def compose_preview(frame, layout, width=PREVIEW_W):
    """What the short will look like, using the same rules as the export."""
    W, H = frame.size
    aspect = W / H
    k = width / OUT_W
    height = round(OUT_H * k)
    cam_out, game_out = fit_out(layout["cam_out"], layout["cam"], aspect), fit_out(layout["game_out"], layout["game"], aspect)

    def part(source, out):
        x, y, w, h = source
        crop = frame.crop((int(x * W), int(y * H), max(int(x * W) + 1, int((x + w) * W)), max(int(y * H) + 1, int((y + h) * H))))
        return crop.resize((max(1, round(out[2] * width)), max(1, round(out[3] * height))), Image.BILINEAR)

    blur, shadow = layout["blur"], layout["shadow"]
    if blur["on"] and blur["amount"] > 0:
        result = ImageOps.fit(frame, (width, height), Image.BILINEAR).filter(ImageFilter.GaussianBlur(blur["amount"] * k * 0.9))
    else:
        result = Image.new("RGB", (width, height))
    result.paste(part(layout["game"], game_out), (round(game_out[0] * width), round(game_out[1] * height)))

    cam = part(layout["cam"], cam_out)
    x, y = round(cam_out[0] * width), round(cam_out[1] * height)
    if _shadow_visible(shadow):
        dx, dy = _shadow_offset(shadow)
        mask = Image.new("L", (width, height), 0)
        ImageDraw.Draw(mask).rectangle((x + dx * k, y + dy * k, x + dx * k + cam.width - 1, y + dy * k + cam.height - 1), fill=round(shadow["intensity"] * 2.55))
        if shadow["softness"] > 0: mask = mask.filter(ImageFilter.GaussianBlur(shadow["softness"] * k * 0.9))
        result.paste((0, 0, 0), (0, 0), mask)
    result.paste(cam, (x, y))     # pasted last: the webcam is always on top
    return result


# --- EDITOR WINDOW ---
class ShortLayoutEditor(PlaybackControls, tb.Toplevel):
    """Pick a clip on the left, play it, drag the boxes on the video to choose what's shown, and drag on the
    preview to choose where it goes.

    entries: one dict per clip with label, start, end, peak, layout and own (True if the clip has its own layout).
    shared: the layout clips follow unless they have their own, or None if none has been saved yet.
    on_save(entries, shared) gets the results; nothing changes if the window is cancelled."""

    def __init__(self, parent, path, entries, shared, aspect, volume, first, palette, on_volume, on_save):
        engine = Engine(path, (1280, 720))   # raises if the video can't be opened, before any window appears
        super().__init__(parent)
        self.title("Vertical short layout")
        self.transient(parent)
        self.engine = engine
        self.first_volume = volume
        self.engine.set_volume(volume)
        self.engine.set_paused(True)
        self.entries, self.aspect = entries, aspect
        self.shared = copy_layout(shared) if shared else None
        self.setting_shared = shared is None    # nothing saved yet: the first clip worked on sets the shared layout
        self.on_volume, self.on_save = on_volume, on_save
        self.cam_color, self.game_color = palette["accent_hover"], palette["warn"]
        self.index = None
        self.layout = None
        self.start = self.end = 0.0
        self.frame_image = None
        self.drag = None
        self.out_drag = None
        self.over_preview = False
        self.scrubbing = False
        self.was_paused = True
        self.ended = False
        self._photo = self._preview_photo = None
        self._preview_job = None

        self.cw = SOURCE_W
        self.ch = round(self.cw / aspect)
        if self.ch > SOURCE_H: self.ch = SOURCE_H; self.cw = round(self.ch * aspect)

        body = tb.Frame(self, padding=12); body.pack(fill=BOTH, expand=YES)
        self._build_clip_list(body)
        self._build_source(body)
        self._build_preview(body)
        self._build_controls(body)

        bar = tb.Frame(self, padding=(12, 0, 12, 12)); bar.pack(fill=X)
        self.all_btn = tb.Button(bar, text="USE ON ALL CLIPS", style="Bones.TButton", takefocus=False, command=self._use_on_all)
        if len(entries) > 1: self.all_btn.pack(side=LEFT)
        tb.Button(bar, text="SAVE LAYOUTS", style="BonesAccent.TButton", takefocus=False, command=self._save).pack(side=RIGHT)
        tb.Button(bar, text="CANCEL", style="BonesDanger.TButton", takefocus=False, command=self.close).pack(side=RIGHT, padx=6)
        tb.Button(bar, text="RESET", style="Bones.TButton", takefocus=False, command=self._reset).pack(side=RIGHT)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)
        self.preview.bind("<ButtonPress-1>", self._on_out_press)
        self.preview.bind("<B1-Motion>", self._on_out_drag)
        self.preview.bind("<ButtonRelease-1>", self._on_out_release)
        self.preview.bind("<Motion>", self._on_out_hover)
        self.preview.bind("<Enter>", lambda e: self._set_over_preview(True))
        self.preview.bind("<Leave>", lambda e: self._set_over_preview(False))
        self.bind("<space>", lambda e: self.toggle())
        self.bind("<Left>", lambda e: self._jump(-5))
        self.bind("<Right>", lambda e: self._jump(5))
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)

        self._select(first)
        self.clip_list.selection_set(first)

        # Open centred over the main window instead of wherever Windows puts it.
        self.update_idletasks()
        x = max(parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2, 0)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_reqheight()) // 2)
        self.geometry(f"+{x}+{y}")
        self.grab_set()
        self.focus_force()
        self._poll()

    # --- BUILDING ---
    def _build_clip_list(self, body):
        left = tb.Frame(body); left.pack(side=LEFT, fill=Y, padx=(0, 12))
        tb.Label(left, text="SELECTED CLIPS").pack(anchor="w", pady=(0, 6))
        holder = tb.Frame(left); holder.pack(fill=BOTH, expand=YES)
        scroll = tb.Scrollbar(holder, orient=VERTICAL, bootstyle="round")
        self.clip_list = tk.Listbox(holder, width=24, activestyle="none", exportselection=False, relief=FLAT, highlightthickness=0, takefocus=0,
                                    bg="#151515", fg="#E0E0E0", selectbackground="#2D2D2D", selectforeground="#FFFFFF", font=("Segoe UI", 9),
                                    yscrollcommand=scroll.set)
        scroll.configure(command=self.clip_list.yview)
        self.clip_list.pack(side=LEFT, fill=BOTH, expand=YES)
        scroll.pack(side=LEFT, fill=Y)
        for entry in self.entries: self.clip_list.insert(END, self._entry_text(entry))
        self.clip_list.bind("<<ListboxSelect>>", self._on_list_select)
        tb.Label(left, text=f"{OWN_MARK.strip()} = has its own layout", style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

    def _build_source(self, body):
        left = tb.Frame(body); left.pack(side=LEFT, fill=BOTH, expand=YES)
        tb.Label(left, text="WHAT TO SHOW").pack(anchor="w")
        self.hint = tb.Label(left, text="", style="Muted.TLabel", wraplength=self.cw, justify=LEFT); self.hint.pack(anchor="w", pady=(0, 6))
        self.canvas = tk.Canvas(left, width=self.cw, height=self.ch, bg="#000000", highlightthickness=0)
        self.canvas.pack(anchor="w")
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")

        scrub = tb.Frame(left); scrub.pack(fill=X, pady=(10, 0))
        self.play_btn = tb.Button(scrub, text="PLAY", style="BonesAccent.TButton", width=8, takefocus=False, command=self.toggle)
        self.play_btn.pack(side=LEFT, padx=(0, 10))
        self.time_label = tb.Label(scrub, text="00:00 / 00:00", width=14); self.time_label.pack(side=RIGHT, padx=(10, 0))
        self.pos_var = tk.DoubleVar(value=0)
        self.scrubber = tb.Scale(scrub, from_=0, to=1, variable=self.pos_var, bootstyle="primary", takefocus=False)
        self.scrubber.pack(side=LEFT, fill=X, expand=YES)
        self._bind_scrubber()

        sound = tb.Frame(left); sound.pack(fill=X, pady=(8, 0))
        tb.Label(sound, text="VOLUME").pack(side=LEFT)
        self.volume_var = tk.DoubleVar(value=self.first_volume)
        tb.Scale(sound, from_=0, to=1, length=150, variable=self.volume_var, bootstyle="primary", takefocus=False,
                 command=lambda v: self._set_volume(float(v))).pack(side=LEFT, padx=8)

    def _build_preview(self, body):
        right = tb.Frame(body); right.pack(side=LEFT, fill=Y, padx=(12, 0))
        tb.Label(right, text="WHERE IT GOES (9:16)").pack(anchor="w")
        tb.Label(right, text="Hover to move or resize here.", style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        self.preview = tk.Canvas(right, width=PREVIEW_W, height=PREVIEW_H, bg="#000000", highlightthickness=0)
        self.preview.pack()
        self.preview_item = self.preview.create_image(0, 0, anchor="nw")

    def _slider(self, parent, label, to, unit, get_set):
        """A labelled slider that edits one number of the layout."""
        row = tb.Frame(parent); row.pack(fill=X, pady=(4, 0))
        tb.Label(row, text=label).pack(side=LEFT)
        value_label = tb.Label(row, text="", foreground="#888888"); value_label.pack(side=RIGHT)
        var = tk.DoubleVar(value=0)

        def changed(value):
            value = float(value)
            value_label.configure(text=f"{int(value)}{unit}")
            get_set(value)
            self._edited()

        scale = tb.Scale(parent, from_=0, to=to, variable=var, bootstyle="primary", takefocus=False, command=changed)
        scale.pack(fill=X)
        return var, value_label, scale

    def _build_controls(self, body):
        panel = tb.Frame(body, width=220); panel.pack(side=LEFT, fill=Y, padx=(12, 0))
        tb.Label(panel, text="WEBCAM POSITION").pack(anchor="w")
        buttons = tb.Frame(panel); buttons.pack(fill=X, pady=(6, 0))
        for where in ("top", "middle", "bottom"):
            tb.Button(buttons, text=where.upper(), style="Bones.TButton", takefocus=False,
                      command=lambda w=where: self._place(w)).pack(side=LEFT, fill=X, expand=YES, padx=(0 if where == "top" else 4, 0))
        tb.Button(panel, text="FILL GAP WITH GAMEPLAY", style="Bones.TButton", takefocus=False, command=self._fill_gap).pack(fill=X, pady=(4, 0))

        tb.Label(panel, text="BACKGROUND").pack(anchor="w", pady=(14, 0))
        self.blur_var = tk.BooleanVar()
        tb.Checkbutton(panel, text="Blur background", variable=self.blur_var, bootstyle="primary", takefocus=False,
                       command=self._on_blur_toggle).pack(anchor="w", pady=(4, 0))
        self.blur_amount = self._slider(panel, "AMOUNT", 60, "", lambda v: self.layout["blur"].update(amount=v))

        tb.Label(panel, text="WEBCAM SHADOW").pack(anchor="w", pady=(14, 0))
        self.shadow_var = tk.BooleanVar()
        tb.Checkbutton(panel, text="Drop shadow", variable=self.shadow_var, bootstyle="primary", takefocus=False,
                       command=self._on_shadow_toggle).pack(anchor="w", pady=(4, 0))
        self.shadow_sliders = {
            "angle": self._slider(panel, "ANGLE", 360, "°", lambda v: self.layout["shadow"].update(angle=v)),
            "distance": self._slider(panel, "DISTANCE", 100, "", lambda v: self.layout["shadow"].update(distance=v)),
            "intensity": self._slider(panel, "INTENSITY", 100, "%", lambda v: self.layout["shadow"].update(intensity=v)),
            "softness": self._slider(panel, "SOFTNESS", 60, "", lambda v: self.layout["shadow"].update(softness=v)),
        }

    def _load_controls(self):
        """Shows the current clip's blur and shadow settings."""
        blur, shadow = self.layout["blur"], self.layout["shadow"]
        self.blur_var.set(blur["on"])
        self.shadow_var.set(shadow["on"])
        units = {"angle": "°", "intensity": "%"}
        for key, (var, label, scale) in self.shadow_sliders.items():
            var.set(shadow[key])
            label.configure(text=f"{int(shadow[key])}{units.get(key, '')}")
            scale.configure(state=NORMAL if shadow["on"] else DISABLED)
        var, label, scale = self.blur_amount
        var.set(blur["amount"])
        label.configure(text=f"{int(blur['amount'])}")
        scale.configure(state=NORMAL if blur["on"] else DISABLED)

    # --- CLIPS ---
    def _entry_text(self, entry):
        return entry["label"] + (OWN_MARK if entry["own"] else "")

    def _refresh_entry(self, index):
        selected = index == self.index
        self.clip_list.delete(index)
        self.clip_list.insert(index, self._entry_text(self.entries[index]))
        if selected: self.clip_list.selection_set(index)

    def _on_list_select(self, event):
        chosen = self.clip_list.curselection()
        if chosen and chosen[0] != self.index: self._select(chosen[0])

    def _select(self, index):
        if self.index is not None: self.setting_shared = self.setting_shared and self.shared is None
        self.index = index
        entry = self.entries[index]
        if not entry["own"] and self.shared: entry["layout"] = copy_layout(self.shared)   # follows the shared layout
        self.layout = entry["layout"]
        self.start, self.end = entry["start"], entry["end"]
        self.ended = False
        self.scrubber.configure(from_=0, to=max(self.end - self.start, 0.1))
        self.engine.set_paused(True)
        self.play_btn.configure(text="PLAY")
        self.engine.play_range(self.start, self.end)
        self.engine.seek(entry["peak"])            # open on the moment the clip was picked for
        self._load_controls()
        self._update_hint()
        self._draw_boxes()
        self._queue_preview()

    def _update_hint(self):
        if len(self.entries) < 2 and self.entries[0]["clip"] is None:
            text = "No clips selected, so this sets the layout every clip starts with."
        elif self.setting_shared:
            text = "First layout: all clips start from this one. Drag a box to move it, a corner to resize, a side to crop just that side."
        elif self.entries[self.index]["own"]:
            text = "This clip has its own layout. Drag a box to move it, a corner to resize, a side to crop just that side."
        else:
            text = "Drag a box to move it, a corner to resize, a side to crop just that side. Changes apply to this clip only."
        self.hint.configure(text=text)

    def _edited(self):
        """Something in the current clip's layout changed."""
        entry = self.entries[self.index]
        if self.setting_shared:
            self.shared = copy_layout(self.layout)   # the first clip worked on sets what the others start from
        elif not entry["own"]:
            entry["own"] = True
            self._refresh_entry(self.index)
            self._update_hint()
        self._draw_boxes()
        self._queue_preview()

    def _use_on_all(self):
        self.shared = copy_layout(self.layout)
        self.setting_shared = False
        for i, entry in enumerate(self.entries):
            entry["own"] = False
            if i != self.index: entry["layout"] = copy_layout(self.layout)
            self._refresh_entry(i)
        self._update_hint()

    # --- PICTURE ---
    def _poll(self):
        if not self.winfo_exists(): return
        self._tick()
        self.after(15, self._poll)

    def _show(self, pixels):
        self.frame_image = Image.frombuffer("RGB", self.engine.size, pixels, "raw", "RGB", 0, 1)
        source = self.frame_image.resize((self.cw, self.ch), Image.BILINEAR)
        if self._photo is None:
            self._photo = ImageTk.PhotoImage(source, master=self)
            self.canvas.itemconfigure(self.image_item, image=self._photo)
        else:
            self._photo.paste(source)
        self._draw_preview()

    def _rect(self, box):
        x, y, w, h = box
        return x * self.cw, y * self.ch, (x + w) * self.cw, (y + h) * self.ch

    def _out_rect(self, box):
        x, y, w, h = box
        return x * PREVIEW_W, y * PREVIEW_H, (x + w) * PREVIEW_W, (y + h) * PREVIEW_H

    def _draw_box(self, canvas, rect, color, name, handle, width, edges=False):
        x0, y0, x1, y1 = rect
        canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=width, tags="box")   # outline only: you need to see what's under it
        canvas.create_rectangle(x0 + 3, y0 + 3, x0 + 12 + 7 * len(name), y0 + 22, fill="#000000", outline="", tags="box")
        canvas.create_text(x0 + 8, y0 + 5, text=name, anchor="nw", fill=color, font=("Segoe UI", 9, "bold"), tags="box")
        spots = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
        if edges:
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            spots += [(mx, y0), (mx, y1), (x0, my), (x1, my)]
        for hx, hy in spots:
            canvas.create_rectangle(hx - handle, hy - handle, hx + handle, hy + handle, fill=color, outline="#000000", tags="box")

    def _draw_boxes(self):
        # Gameplay first, webcam second, so the webcam is on top here as well as in the short.
        self.canvas.delete("box")
        self._draw_box(self.canvas, self._rect(self.layout["game"]), self.game_color, "GAMEPLAY", HANDLE, 3, edges=True)
        self._draw_box(self.canvas, self._rect(self.layout["cam"]), self.cam_color, "WEBCAM", HANDLE, 3, edges=True)
        # On the preview the boxes only show while the mouse is over it, so it can be watched clean.
        self.preview.delete("box")
        if self.over_preview or self.out_drag:
            self._draw_box(self.preview, self._out_rect(self.layout["game_out"]), self.game_color, "GAMEPLAY", OUT_HANDLE, 2)
            self._draw_box(self.preview, self._out_rect(self.layout["cam_out"]), self.cam_color, "WEBCAM", OUT_HANDLE, 2)

    def _set_over_preview(self, over):
        self.over_preview = over
        self._draw_boxes()

    def _queue_preview(self):
        if self._preview_job is None: self._preview_job = self.after(30, self._draw_preview)

    def _draw_preview(self):
        if self._preview_job is not None:
            self.after_cancel(self._preview_job); self._preview_job = None
        if self.frame_image is None: return
        image = compose_preview(self.frame_image, self.layout)
        if self._preview_photo is None:
            self._preview_photo = ImageTk.PhotoImage(image, master=self)
            self.preview.itemconfigure(self.preview_item, image=self._preview_photo)
        else:
            self._preview_photo.paste(image)

    # --- MOUSE ON THE VIDEO: what to show ---
    def _hit(self, ex, ey, keys, rect, handle, edges=False):
        """(box name, what to do). The webcam is checked first because it's on top."""
        for name in keys:
            x0, y0, x1, y1 = rect(self.layout[name])
            for corner, hx, hy in (("tl", x0, y0), ("tr", x1, y0), ("bl", x0, y1), ("br", x1, y1)):
                if abs(ex - hx) <= handle + 2 and abs(ey - hy) <= handle + 2: return name, corner
            if edges:   # anywhere along a side, for cropping just that side
                near = EDGE_GRAB
                if x0 <= ex <= x1:
                    if abs(ey - y0) <= near: return name, "t"
                    if abs(ey - y1) <= near: return name, "b"
                if y0 <= ey <= y1:
                    if abs(ex - x0) <= near: return name, "l"
                    if abs(ex - x1) <= near: return name, "r"
            if x0 <= ex <= x1 and y0 <= ey <= y1: return name, "move"
        return None

    def _cursor(self, hit):
        cursors = {"move": "fleur", "tl": "size_nw_se", "br": "size_nw_se", "tr": "size_ne_sw", "bl": "size_ne_sw",
                   "t": "size_ns", "b": "size_ns", "l": "size_we", "r": "size_we"}
        return cursors[hit[1]] if hit else ""

    def _on_hover(self, event):
        self.canvas.configure(cursor=self._cursor(self._hit(event.x, event.y, ("cam", "game"), self._rect, HANDLE, edges=True)))

    def _on_press(self, event):
        hit = self._hit(event.x, event.y, ("cam", "game"), self._rect, HANDLE, edges=True)
        if not hit: self.drag = None; return
        self.drag = {"name": hit[0], "mode": hit[1], "start": (event.x, event.y), "box": list(self.layout[hit[0]])}

    def _on_release(self, event):
        self.drag = None

    def _on_drag(self, event):
        if not self.drag: return
        name, mode, box = self.drag["name"], self.drag["mode"], self.drag["box"]
        if mode == "move":
            dx, dy = (event.x - self.drag["start"][0]) / self.cw, (event.y - self.drag["start"][1]) / self.ch
            self.layout[name] = _clamp_box([box[0] + dx, box[1] + dy, box[2], box[3]])
        else:
            mx, my = min(max(event.x / self.cw, 0.0), 1.0), min(max(event.y / self.ch, 0.0), 1.0)
            self.layout[name] = self._resize_source(box, mode, mx, my)
            # A new selection shape: its box in the short takes the same shape, keeping its size and place.
            self.layout[name + "_out"] = fit_out(self.layout[name + "_out"], self.layout[name], self.aspect)
        self._edited()

    def _resize_source(self, box, mode, mx, my):
        x0, y0, x1, y1 = box[0], box[1], box[0] + box[2], box[1] + box[3]
        # mode names the sides being dragged: one side ("t") or a corner's two ("tl").
        if "l" in mode: x0 = min(mx, x1 - MIN_BOX)
        if "r" in mode: x1 = max(mx, x0 + MIN_BOX)
        if "t" in mode: y0 = min(my, y1 - MIN_BOX)
        if "b" in mode: y1 = max(my, y0 + MIN_BOX)
        return _clamp_box([x0, y0, x1 - x0, y1 - y0])

    # --- MOUSE ON THE PREVIEW: where it goes ---
    def _on_out_hover(self, event):
        hit = self._hit(event.x, event.y, ("cam_out", "game_out"), self._out_rect, OUT_HANDLE)
        self.preview.configure(cursor=self._cursor(hit))

    def _on_out_press(self, event):
        hit = self._hit(event.x, event.y, ("cam_out", "game_out"), self._out_rect, OUT_HANDLE)
        self.out_drag = {"name": hit[0], "mode": hit[1], "start": (event.x, event.y), "box": list(self.layout[hit[0]])} if hit else None

    def _on_out_release(self, event):
        self.out_drag = None
        self._draw_boxes()

    def _on_out_drag(self, event):
        if not self.out_drag: return
        name, mode, box = self.out_drag["name"], self.out_drag["mode"], self.out_drag["box"]
        if mode == "move":
            dx, dy = (event.x - self.out_drag["start"][0]) / PREVIEW_W, (event.y - self.out_drag["start"][1]) / PREVIEW_H
            self.layout[name] = _clamp_box([box[0] + dx, box[1] + dy, box[2], box[3]])
        else:
            mx = min(max(event.x / PREVIEW_W, 0.0), 1.0)
            self.layout[name] = self._resize_out(box, mode, mx, self.layout[name[:-4]])
        self._edited()

    def _resize_out(self, box, mode, mx, source):
        """Resizes from a corner, keeping the selection's shape; the opposite corner stays put. The selection isn't touched."""
        ratio = _out_ratio(source, self.aspect)
        ax = box[0] + box[2] if "l" in mode else box[0]
        ay = box[1] + box[3] if "t" in mode else box[1]
        room_x = ax if "l" in mode else 1.0 - ax
        room_y = ay if "t" in mode else 1.0 - ay
        w = min(max(abs(mx - ax), MIN_OUT), room_x, room_y / ratio)
        h = w * ratio
        return _clamp_box([ax - w if "l" in mode else ax, ay - h if "t" in mode else ay, w, h])

    # --- BUTTONS ---
    def _set_volume(self, volume):
        self.engine.set_volume(volume)
        self.on_volume(volume)

    def _place(self, where):
        place_webcam(self.layout, where, self.aspect)
        self._edited()

    def _fill_gap(self):
        fill_gap(self.layout, self.aspect)
        self._edited()

    def _on_blur_toggle(self):
        self.layout["blur"]["on"] = self.blur_var.get()
        self.blur_amount[2].configure(state=NORMAL if self.blur_var.get() else DISABLED)
        self._edited()

    def _on_shadow_toggle(self):
        self.layout["shadow"]["on"] = self.shadow_var.get()
        for _, _, scale in self.shadow_sliders.values(): scale.configure(state=NORMAL if self.shadow_var.get() else DISABLED)
        self._edited()

    def _reset(self):
        fresh = default_layout(self.aspect)
        self.layout.clear()
        self.layout.update(fresh)
        self._load_controls()
        self._edited()

    def _save(self):
        entries, shared = self.entries, self.shared or copy_layout(self.layout)
        self.close()
        self.on_save(entries, shared)

    def close(self):
        engine, self.engine = self.engine, None
        if engine: engine.close()
        self.destroy()
