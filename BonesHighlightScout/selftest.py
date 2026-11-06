# ==============================================================================
# SELF TEST for Bones Highlight Scout
# ==============================================================================
# Runs a build through the real app from start to finish and writes what happened to a JSON file:
#   BonesHighlightScout.exe --self-test video.mp4 result.json
# Load (reaction sounds), scan with speech, the preview player, the layout editor and an export of each kind.
# It exists because a packaged app can fail in ways that only show up when each part is actually used.

import json
import os
import platform
import shutil
import sys
import tempfile
import time
import traceback


def run(app_module, video, result_path):
    result = {"version": app_module.APP_VERSION, "frozen": getattr(sys, "frozen", False), "platform": platform.platform(),
              "data_folder": app_module.APP_DIR, "ffmpeg": app_module.find_tool("ffmpeg"), "steps": {}, "problems": []}
    out_dir = tempfile.mkdtemp(prefix="bhs_selftest_")
    problems = result["problems"]

    # A test must never change anyone's real settings, saved clips or transcripts.
    app_module.load_settings = lambda: dict(app_module.DEFAULT_SETTINGS)
    app_module.save_settings = lambda settings: None
    app_module.SESSIONS_DIR = os.path.join(out_dir, "sessions")
    app_module.SPEECH_DIR = os.path.join(out_dir, "speech")

    for kind in ("show_info", "show_warning", "show_error"):
        setattr(app_module.Messagebox, kind, staticmethod(lambda message, *a, _kind=kind, **k: problems.append(f"{_kind}: {message}")))

    app = app_module.HighlightScout()
    root = app.root
    app._ask = lambda *a, **k: None
    app._on_tk_error = lambda exc, value, tb: problems.append("".join(traceback.format_exception(exc, value, tb)))
    root.report_callback_exception = app._on_tk_error
    app.settings.update(preview_volume=0.0)
    steps = result["steps"]
    marks = {"start": time.time()}

    def finish(failure=None):
        if failure: problems.append(failure)
        result["seconds"] = round(time.time() - marks["start"], 1)
        result["passed"] = not problems
        with open(result_path, "w", encoding="utf-8") as f: json.dump(result, f, indent=1)
        shutil.rmtree(out_dir, ignore_errors=True)
        app._on_close()

    def guarded(step):
        def inner(*args):
            try: step(*args)
            except Exception: finish(traceback.format_exc())
        return inner

    def when_idle(then, ticks=0):
        if app.busy and ticks < 18000: root.after(100, lambda: when_idle(then, ticks + 1))
        elif app.busy: finish("timed out waiting for a background job")
        else: guarded(then)()

    def start():
        app.detect_var.set(next(k for k, v in app_module.DETECT_MODES.items() if v == "hype"))
        app._on_detect_mode_changed()
        app.speech_var.set(True)
        app.pre_var.set(3); app.post_var.set(2); app.max_var.set(3)
        marks["load"] = time.time()
        app.load_video(video)
        when_idle(loaded)

    def loaded():
        if app.smooth is None: return finish("the video didn't load")
        steps["load"] = {"seconds": round(time.time() - marks["load"], 1), "duration": round(app.duration, 1), "size": app.video_size,
                         "reaction_frames": 0 if app.hype.reactions is None else len(app.hype.reactions)}
        if not steps["load"]["reaction_frames"]: problems.append("the reaction model produced nothing")
        marks["scan"] = time.time()
        app.scan()
        when_idle(scanned)

    def scanned():
        lines = app.hype.speech
        steps["scan"] = {"seconds": round(time.time() - marks["scan"], 1), "speech_lines": None if lines is None else len(lines),
                         "clips": [{"title": c.title, "tags": c.tags, "start": round(c.start, 1)} for c in app.clips]}
        if lines is None: problems.append("speech recognition didn't run")
        if not app.clips:   # a quiet video is fine; later steps still need a clip
            app._set_clips([app_module.Clip(start=0.0, end=min(5.0, app.duration), peak=1.0, score=1.0, title="Test clip")])
        for row in app.rows[1:]: row.sel_var.set(False)
        app._preview_row(app.rows[0])
        root.after(3000, guarded(played))

    def played():
        window = app.preview_window
        if window is None or window.engine is None: return finish("the preview player didn't open")
        now = window.engine.clock()
        steps["player"] = {"sound_device": window.engine.wave is not None, "picture_shown": window.photo is not None,
                           "position": round(now - window.start, 2), "button": window.play_btn.cget("text")}
        if not window.photo: problems.append("the preview player showed no picture")
        if now <= window.start: problems.append("the preview player didn't advance")
        app._stop_preview()
        app.open_short_editor()
        root.after(2500, guarded(edited))

    def edited():
        editors = [w for w in root.winfo_children() if isinstance(w, app_module.vertical.ShortLayoutEditor)]
        if not editors: return finish("the layout editor didn't open")
        editor = editors[0]
        steps["editor"] = {"picture_shown": editor.frame_image is not None, "clips_listed": editor.clip_list.size()}
        if editor.frame_image is None: problems.append("the layout editor showed no picture")
        editor.shadow_var.set(True); editor._on_shadow_toggle()
        editor._save()
        steps["exports"] = {}
        modes = list(app_module.EXPORT_MODES.items())
        app.out_var.set(out_dir)
        export_next(modes)

    def export_next(modes):
        if not modes:
            return finish()
        label, mode = modes[0]
        before = set(os.listdir(out_dir))
        app.export_var.set(label)
        marks["export"] = time.time()
        app.render()
        when_idle(lambda: exported(mode, before, modes[1:]))

    def exported(mode, before, rest):
        import av
        new = [f for f in os.listdir(out_dir) if f not in before]
        if not new:
            problems.append(f"export {mode} produced no file")
            steps["exports"][mode] = None
        else:
            path = os.path.join(out_dir, new[0])
            with av.open(path) as c:
                v = c.streams.video[0]
                steps["exports"][mode] = {"size": [v.width, v.height], "seconds": round((c.duration or 0) / av.time_base, 1),
                                          "has_audio": bool(c.streams.audio), "render_seconds": round(time.time() - marks["export"], 1)}
            if not steps["exports"][mode]["has_audio"]: problems.append(f"export {mode} has no sound")
        export_next(rest)

    root.after(1500, guarded(start))
    root.after(900000, lambda: finish("the self test timed out"))
    root.mainloop()
