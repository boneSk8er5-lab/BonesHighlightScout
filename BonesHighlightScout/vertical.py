# ==============================================================================
# VERTICAL SHORT LAYOUT for Bones Highlight Scout
# ==============================================================================
# Twitch-style portrait layout: one box over the webcam, one over the gameplay. The webcam is always
# drawn on top. Boxes are stored as [x, y, w, h] fractions of the video frame, so a saved layout works
# at any resolution with the same shape.

import threading
import tkinter as tk

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from PIL import Image, ImageOps, ImageTk

OUT_W, OUT_H = 1080, 1920
CAM_MIN_H, CAM_MAX_H = 320, 1280     # webcam height in the short, so neither part gets squeezed out
MIN_BOX = 0.04                       # smallest box, as a fraction of the frame
HANDLE = 7                           # corner handle size on screen (half width)

STYLES = {
    "Stacked (webcam above gameplay)": "stacked",
    "Overlay (webcam on top of full gameplay)": "overlay",
}


# --- GEOMETRY ---
def _even(n):
    return int(round(n / 2)) * 2


def cam_height(cam, aspect):
    """Webcam height in the short (it always spans the full width). aspect = video width / height."""
    return _even(min(CAM_MAX_H, max(CAM_MIN_H, OUT_W * cam[3] / (cam[2] * aspect))))


def game_height(cam, style, aspect):
    return OUT_H if style == "overlay" else OUT_H - cam_height(cam, aspect)


def _clamp_box(box):
    x, y, w, h = box
    w, h = min(w, 1.0), min(h, 1.0)
    return [min(max(x, 0.0), 1.0 - w), min(max(y, 0.0), 1.0 - h), w, h]


def fit_game(game, cam, style, aspect):
    """The gameplay box keeps the shape of its slot in the short, so the box is exactly what gets shown."""
    ratio = aspect * game_height(cam, style, aspect) / OUT_W    # box height per unit of box width
    cx, cy = game[0] + game[2] / 2, game[1] + game[3] / 2
    w = min(game[2], 1.0, 1.0 / ratio)
    h = w * ratio
    return _clamp_box([cx - w / 2, cy - h / 2, w, h])


def default_layout(aspect):
    w = 0.25
    cam = [0.02, 0.02, w, w * aspect * 9 / 16]     # a 16:9 webcam in the top-left corner
    return {"style": "stacked", "cam": cam, "game": fit_game([0.0, 0.0, 1.0, 1.0], cam, "stacked", aspect)}


def validate_layout(layout):
    """The layout if it's usable, else None (a settings file can hold anything)."""
    try:
        if layout["style"] not in STYLES.values(): return None
        for key in ("cam", "game"):
            x, y, w, h = (float(v) for v in layout[key])
            if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > 1.001 or y + h > 1.001: return None
        return layout
    except (KeyError, TypeError, ValueError):
        return None


# --- EXPORT ---
def short_filtergraph(layout, aspect):
    """ffmpeg filtergraph producing a 1080x1920 [v]. Scaling fills the slot without ever stretching the picture."""
    cam, game, style = layout["cam"], layout["game"], layout["style"]
    cam_h, game_h = cam_height(cam, aspect), game_height(cam, style, aspect)

    def part(box, height):
        x, y, w, h = box
        return (f"crop=iw*{w:.5f}:ih*{h:.5f}:iw*{x:.5f}:ih*{y:.5f},"
                f"scale={OUT_W}:{height}:force_original_aspect_ratio=increase,crop={OUT_W}:{height},setsar=1")

    join = "[game][cam]overlay=0:0[v]" if style == "overlay" else "[cam][game]vstack=inputs=2[v]"
    return f"[0:v]split=2[c0][g0];[c0]{part(cam, cam_h)}[cam];[g0]{part(game, game_h)}[game];{join}"


def compose_preview(frame, layout, width=270):
    """What the short will look like, using the same rules as the export."""
    W, H = frame.size
    aspect = W / H
    cam, game, style = layout["cam"], layout["game"], layout["style"]
    k = width / OUT_W
    cam_h, game_h = cam_height(cam, aspect), game_height(cam, style, aspect)

    def part(box, height):
        x, y, w, h = box
        crop = frame.crop((int(x * W), int(y * H), max(int(x * W) + 1, int((x + w) * W)), max(int(y * H) + 1, int((y + h) * H))))
        return ImageOps.fit(crop, (width, max(1, round(height * k))), Image.BILINEAR)

    out = Image.new("RGB", (width, round(OUT_H * k)))
    out.paste(part(game, game_h), (0, 0 if style == "overlay" else round(cam_h * k)))
    out.paste(part(cam, cam_h), (0, 0))     # pasted last: the webcam is always on top
    return out


# --- EDITOR WINDOW ---
class ShortLayoutEditor(tb.Toplevel):
    """Drag the boxes to move them, drag a corner to resize. The preview on the right shows the result."""

    def __init__(self, parent, layout, aspect, duration, start_time, grab_frame, post, palette, on_save):
        super().__init__(parent)
        self.title("Vertical short layout")
        self.transient(parent)
        self.aspect, self.duration = aspect, duration
        self.grab_frame, self.post, self.on_save = grab_frame, post, on_save
        self.cam_color, self.game_color = palette["accent_hover"], palette["warn"]
        self.layout = {"style": layout["style"], "cam": list(layout["cam"]), "game": list(layout["game"])}
        self.frame_image = None
        self.frame_seq = 0
        self.drag = None
        self._preview_job = None

        self.cw = 880
        self.ch = round(self.cw / aspect)
        if self.ch > 560: self.ch = 560; self.cw = round(self.ch * aspect)

        body = tb.Frame(self, padding=12); body.pack(fill=BOTH, expand=YES)
        left = tb.Frame(body); left.pack(side=LEFT, fill=BOTH, expand=YES)
        tb.Label(left, text="Drag a box to move it, drag a corner to resize. The webcam always renders on top.", style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        self.canvas = tk.Canvas(left, width=self.cw, height=self.ch, bg="#000000", highlightthickness=0)
        self.canvas.pack()
        self.image_item = self.canvas.create_image(0, 0, anchor="nw")

        scrub = tb.Frame(left); scrub.pack(fill=X, pady=(10, 0))
        tb.Label(scrub, text="FRAME").pack(side=LEFT, padx=(0, 8))
        self.time_var = tk.DoubleVar(value=start_time)
        scale = tb.Scale(scrub, from_=0, to=max(duration, 1), variable=self.time_var, bootstyle="primary")
        scale.pack(side=LEFT, fill=X, expand=YES)
        scale.bind("<ButtonRelease-1>", lambda e: self._load_frame())

        right = tb.Frame(body); right.pack(side=LEFT, fill=Y, padx=(14, 0))
        tb.Label(right, text="PREVIEW (9:16)").pack(anchor="w", pady=(0, 6))
        self.preview = tk.Canvas(right, width=270, height=480, bg="#000000", highlightthickness=0)
        self.preview.pack()
        self.preview_item = self.preview.create_image(0, 0, anchor="nw")

        bar = tb.Frame(self, padding=(12, 0, 12, 12)); bar.pack(fill=X)
        tb.Label(bar, text="LAYOUT").pack(side=LEFT, padx=(0, 6))
        names = {v: k for k, v in STYLES.items()}
        self.style_var = tk.StringVar(value=names[self.layout["style"]])
        combo = tb.Combobox(bar, textvariable=self.style_var, values=list(STYLES), state="readonly", width=38, bootstyle="primary")
        combo.pack(side=LEFT)
        combo.bind("<<ComboboxSelected>>", lambda e: self._on_style())
        tb.Button(bar, text="SAVE LAYOUT", style="BonesAccent.TButton", command=self._save).pack(side=RIGHT)
        tb.Button(bar, text="CANCEL", style="BonesDanger.TButton", command=self.destroy).pack(side=RIGHT, padx=6)
        tb.Button(bar, text="RESET", style="Bones.TButton", command=self._reset).pack(side=RIGHT)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)
        self.bind("<Escape>", lambda e: self.destroy())

        self._draw_boxes()
        self._load_frame()
        # Open centred over the main window instead of wherever Windows puts it.
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_reqwidth()) // 2
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_reqheight()) // 2)
        self.geometry(f"+{x}+{y}")
        self.grab_set()

    # --- FRAME ---
    def _load_frame(self):
        self.frame_seq += 1
        seq, t = self.frame_seq, float(self.time_var.get())

        def work():
            try: image = self.grab_frame(t)
            except Exception: image = None
            self.post(lambda: self._frame_ready(seq, image))

        threading.Thread(target=work, daemon=True).start()

    def _frame_ready(self, seq, image):
        if seq != self.frame_seq or image is None or not self.winfo_exists(): return
        self.frame_image = image.convert("RGB")
        self._shown = ImageTk.PhotoImage(self.frame_image.resize((self.cw, self.ch), Image.BILINEAR), master=self)
        self.canvas.itemconfigure(self.image_item, image=self._shown)
        self._queue_preview()

    # --- DRAWING ---
    def _rect(self, box):
        x, y, w, h = box
        return x * self.cw, y * self.ch, (x + w) * self.cw, (y + h) * self.ch

    def _draw_boxes(self):
        self.canvas.delete("box")
        # Gameplay first, webcam second, so the webcam is on top here as well as in the short.
        for box, color, name in ((self.layout["game"], self.game_color, "GAMEPLAY"), (self.layout["cam"], self.cam_color, "WEBCAM")):
            x0, y0, x1, y1 = self._rect(box)
            self.canvas.create_rectangle(x0, y0, x1, y1, outline=color, width=3, tags="box")   # outline only: you need to see what's under it
            self.canvas.create_rectangle(x0 + 3, y0 + 3, x0 + 12 + 7 * len(name), y0 + 22, fill="#000000", outline="", tags="box")
            self.canvas.create_text(x0 + 8, y0 + 5, text=name, anchor="nw", fill=color, font=("Segoe UI", 9, "bold"), tags="box")
            for hx, hy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                self.canvas.create_rectangle(hx - HANDLE, hy - HANDLE, hx + HANDLE, hy + HANDLE, fill=color, outline="#000000", tags="box")

    def _queue_preview(self):
        if self._preview_job is None: self._preview_job = self.after(30, self._draw_preview)

    def _draw_preview(self):
        self._preview_job = None
        if self.frame_image is None: return
        self._preview_photo = ImageTk.PhotoImage(compose_preview(self.frame_image, self.layout), master=self)
        self.preview.itemconfigure(self.preview_item, image=self._preview_photo)

    def _changed(self):
        self._draw_boxes()
        self._queue_preview()

    # --- MOUSE ---
    def _hit(self, ex, ey):
        """(box name, what to do). The webcam is checked first because it's on top."""
        for name in ("cam", "game"):
            x0, y0, x1, y1 = self._rect(self.layout[name])
            for corner, hx, hy in (("tl", x0, y0), ("tr", x1, y0), ("bl", x0, y1), ("br", x1, y1)):
                if abs(ex - hx) <= HANDLE + 2 and abs(ey - hy) <= HANDLE + 2: return name, corner
            if x0 <= ex <= x1 and y0 <= ey <= y1: return name, "move"
        return None

    def _on_hover(self, event):
        hit = self._hit(event.x, event.y)
        cursors = {"move": "fleur", "tl": "size_nw_se", "br": "size_nw_se", "tr": "size_ne_sw", "bl": "size_ne_sw"}
        self.canvas.configure(cursor=cursors[hit[1]] if hit else "")

    def _on_press(self, event):
        hit = self._hit(event.x, event.y)
        if not hit: self.drag = None; return
        self.drag = {"name": hit[0], "mode": hit[1], "start": (event.x, event.y), "box": list(self.layout[hit[0]])}

    def _on_release(self, event):
        self.drag = None

    def _on_drag(self, event):
        if not self.drag: return
        name, mode, box = self.drag["name"], self.drag["mode"], self.drag["box"]
        mx, my = min(max(event.x / self.cw, 0.0), 1.0), min(max(event.y / self.ch, 0.0), 1.0)
        if mode == "move":
            dx, dy = (event.x - self.drag["start"][0]) / self.cw, (event.y - self.drag["start"][1]) / self.ch
            self.layout[name] = _clamp_box([box[0] + dx, box[1] + dy, box[2], box[3]])
        elif name == "cam":
            self.layout["cam"] = self._resize_cam(box, mode, mx, my)
            self.layout["game"] = fit_game(self.layout["game"], self.layout["cam"], self.layout["style"], self.aspect)
        else:
            self.layout["game"] = self._resize_game(box, mode, mx, my)
        self._changed()

    def _resize_cam(self, box, mode, mx, my):
        x0, y0, x1, y1 = box[0], box[1], box[0] + box[2], box[1] + box[3]
        if "l" in mode: x0 = min(mx, x1 - MIN_BOX)
        else: x1 = max(mx, x0 + MIN_BOX)
        if "t" in mode: y0 = min(my, y1 - MIN_BOX)
        else: y1 = max(my, y0 + MIN_BOX)
        w, h = x1 - x0, y1 - y0
        # Keep the webcam's share of the short within limits by limiting its height.
        h_min, h_max = CAM_MIN_H * w * self.aspect / OUT_W, CAM_MAX_H * w * self.aspect / OUT_W
        h = min(max(h, h_min), h_max)
        if "t" in mode: y0 = y1 - h
        return _clamp_box([x0, y0, w, h])

    def _resize_game(self, box, mode, mx, my):
        ratio = self.aspect * game_height(self.layout["cam"], self.layout["style"], self.aspect) / OUT_W
        # The corner opposite the one being dragged stays put; height follows width to keep the slot's shape.
        ax = box[0] + box[2] if "l" in mode else box[0]
        ay = box[1] + box[3] if "t" in mode else box[1]
        room_x = ax if "l" in mode else 1.0 - ax
        room_y = ay if "t" in mode else 1.0 - ay
        w = min(max(abs(mx - ax), 0.1), room_x, room_y / ratio)
        h = w * ratio
        return _clamp_box([ax - w if "l" in mode else ax, ay - h if "t" in mode else ay, w, h])

    # --- BUTTONS ---
    def _on_style(self):
        self.layout["style"] = STYLES[self.style_var.get()]
        self.layout["game"] = fit_game(self.layout["game"], self.layout["cam"], self.layout["style"], self.aspect)
        self._changed()

    def _reset(self):
        style = self.layout["style"]
        self.layout = default_layout(self.aspect)
        self.layout["style"] = style
        self.layout["game"] = fit_game([0.0, 0.0, 1.0, 1.0], self.layout["cam"], style, self.aspect)
        self._changed()

    def _save(self):
        layout = self.layout
        self.destroy()
        self.on_save(layout)
