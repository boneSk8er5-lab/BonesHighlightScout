# ==============================================================================
# HYPE DETECTION for Bones Highlight Scout
# ==============================================================================
# Finds moments where people react: laughter / screaming / cheering in the audio (YAMNet),
# hype phrases in what's said (faster-whisper), and spikes in a chat replay (Twitch / YouTube).
# None of this predicts views. It points at reactions; the user decides what's worth posting.
# Every signal is put on the same timeline as the loudness data so they can be combined.

import json
import os
import re
from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE = 16000          # what YAMNet and Whisper both expect

# --- REACTION SOUNDS (YAMNet) ---
YAMNET_HOP = 0.48            # seconds between YAMNet frames; each frame looks at 0.96 s
YAMNET_CHUNK_FRAMES = 1250   # 10 min of audio per model call (1250 * 0.48 s)
YAMNET_OVERLAP = int(0.96 * SAMPLE_RATE)

# tag -> (YAMNet class indexes, weight). Human reactions count most; game sounds (booms, gunfire)
# barely count, because in a shooter they happen all the time.
REACTION_GROUPS = {
    "LAUGHTER":  ([13, 14, 15, 16, 17, 18], 1.0),
    "SCREAMING": ([10, 11], 1.0),
    "SHOUTING":  ([6, 7, 8, 9], 0.8),
    "CHEERING":  ([58, 61, 62, 64], 0.8),
    "GASP":      ([39], 0.6),
    "BOOM":      ([420, 421, 430, 437, 463], 0.25),
}
REACTION_TAGS = list(REACTION_GROUPS)
REACTION_TAG_MIN = 0.25      # how sure YAMNet must be before a clip gets that tag


class ReactionDetector:
    """Feed it 16 kHz audio in any size pieces; it runs YAMNet every 10 minutes of audio."""

    def __init__(self, model_path):
        import onnxruntime as ort
        self.session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self.buffer = np.empty(0, np.float32)
        self.frames = []
        chunk = int(YAMNET_CHUNK_FRAMES * YAMNET_HOP * SAMPLE_RATE)
        self.chunk_samples = chunk

    def _run(self, audio, keep):
        scores = self.session.run(["output_0"], {"waveform": audio})[0]
        if keep is not None: scores = scores[:keep]
        self.frames.append(np.stack([scores[:, idx].max(axis=1) for idx, _ in REACTION_GROUPS.values()], axis=1))

    def feed(self, samples):
        self.buffer = np.concatenate([self.buffer, samples])
        # Each chunk gets 0.96 s of the next one so frames line up exactly across the cut.
        while len(self.buffer) >= self.chunk_samples + YAMNET_OVERLAP:
            self._run(self.buffer[:self.chunk_samples + YAMNET_OVERLAP], YAMNET_CHUNK_FRAMES)
            self.buffer = self.buffer[self.chunk_samples:]

    def finish(self):
        """Returns [frames, len(REACTION_TAGS)] probabilities; frame k is centred on k*0.48 + 0.48 s."""
        if len(self.buffer): self._run(self.buffer, None)
        self.buffer = np.empty(0, np.float32)
        out = np.concatenate(self.frames) if self.frames else np.zeros((0, len(REACTION_TAGS)), np.float32)
        self.frames = []
        return out.astype(np.float32)


def load_reaction_detector(model_dir):
    """None if onnxruntime or the model file is missing; the rest of the app keeps working."""
    path = os.path.join(model_dir, "yamnet.onnx")
    if not os.path.isfile(path): return None
    try: return ReactionDetector(path)
    except Exception: return None


_available = {}

def reactions_available(model_dir):
    if "reactions" not in _available:
        try:
            import onnxruntime  # noqa: F401
            _available["reactions"] = os.path.isfile(os.path.join(model_dir, "yamnet.onnx"))
        except Exception:
            _available["reactions"] = False
    return _available["reactions"]


# --- SPEECH (faster-whisper) ---
SPEECH_CHUNK_SECONDS = 300
WHISPER_MODEL = "base"

# (pattern, tag, weight) - matched against lower-case transcript lines. Weight 1.0 phrases can carry a moment
# on their own; weak ones (swearing, "crazy") are said all the time, so they only add to other reactions.
HYPE_PHRASES = [
    # "no way" only when it stands alone ("No way!"), not "there's no way I'm going"
    (r"(^|[.!?]\s+)\W*no+ (fucking )?way\b|\bno+ (fucking )?way(\W*$|!)", '"NO WAY"', 1.0),
    (r"\boh my (god|gosh|lord)\b|\bomg\b", '"OH MY GOD"', 1.0),
    # "let's go" is everyday talk ("let's go see...") unless it's stretched out, shouted, or said on its own
    (r"\blet'?s go{2,}\b|\blet'?s go+!|^\W*let'?s go+\W*$", '"LET\'S GO"', 1.0),
    (r"\bwhat the (hell|heck|fuck|f)\b|\bwtf\b", '"WHAT THE..."', 1.0),
    (r"\bdid you see that\b|\bdid that just happen\b|\bwhat was that\b", '"DID YOU SEE THAT"', 1.0),
    (r"\b(are|you) (you )?kidding( me)?\b", '"ARE YOU KIDDING"', 1.0),
    (r"\bi can'?t believe\b", '"I CAN\'T BELIEVE"', 1.0),
    (r"\bholy (shit|crap|cow|moly)\b", '"HOLY..."', 1.0),
    (r"\bclip (it|that)\b", '"CLIP IT"', 1.0),
    (r"\bno+o{2,}\b|\bnah+h\b", '"NOOO"', 1.0),
    (r"\b(yes){1}s{2,}\b|\byes yes\b", '"YESSS"', 1.0),
    (r"\b(ha){2,}\b|\blmao\b|\(laugh|\[laugh", "LAUGHING", 0.8),
    (r"\b(insane|crazy|clutch|unbelievable|cracked)\b", '"INSANE"', 0.2),
    (r"\bfuck|\bshit\b", "SWEARING", 0.2),
]
_PHRASES = [(re.compile(p), tag, weight) for p, tag, weight in HYPE_PHRASES]
PHRASE_WEIGHTS = {tag: weight for _, tag, weight in HYPE_PHRASES}


@dataclass
class SpeechLine:
    start: float
    end: float
    text: str


class SpeechTranscriber:
    """Wraps faster-whisper. The model (~145 MB) downloads into model_dir the first time."""

    def __init__(self, model_dir):
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from faster_whisper import WhisperModel
        self.model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", download_root=model_dir)
        self.language = None

    def transcribe(self, read_chunk, duration, progress, cancelled):
        """read_chunk(start, seconds) -> 16 kHz float32 audio. Returns a list of SpeechLine."""
        lines, start = [], 0.0
        while start < duration:
            if cancelled(): return None
            length = min(SPEECH_CHUNK_SECONDS, duration - start)
            audio = read_chunk(start, length)
            if len(audio) > SAMPLE_RATE // 2:
                segments, info = self.model.transcribe(audio, language=self.language, beam_size=1, vad_filter=True,
                                                       condition_on_previous_text=False)
                self.language = self.language or info.language   # detect once, then stay with it
                for seg in segments:
                    if cancelled(): return None
                    text = seg.text.strip()
                    if text: lines.append(SpeechLine(start + seg.start, start + seg.end, text))
                    progress(min((start + seg.end) / duration, 1.0))
            start += length
            progress(min(start / duration, 1.0))
        return lines


def speech_available():
    if "speech" not in _available:   # importing faster-whisper takes a second, so only do it once
        try:
            import faster_whisper  # noqa: F401
            _available["speech"] = True
        except Exception:
            _available["speech"] = False
    return _available["speech"]


def availability_checked():
    return "speech" in _available and "reactions" in _available


def save_speech(path, lines):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump([[l.start, l.end, l.text] for l in lines], f, ensure_ascii=False)
    except OSError: pass


def load_speech(path):
    try:
        with open(path, encoding="utf-8") as f: return [SpeechLine(float(a), float(b), str(t)) for a, b, t in json.load(f)]
    except (OSError, ValueError, TypeError): return None


def phrase_tags(text):
    lowered = text.lower()
    return [tag for pattern, tag, _ in _PHRASES if pattern.search(lowered)]


def _is_excited(line, words_per_sec_median):
    words = len(line.text.split())
    rate = words / max(line.end - line.start, 0.3)
    caps = sum(w.isupper() and len(w) > 1 for w in line.text.split())
    return "!" in line.text or caps >= 2 or (words >= 4 and rate > 1.6 * words_per_sec_median)


# --- CHAT REPLAY ---
CHAT_DELAY = 5.0             # chat reacts a few seconds after the moment; shift it back
CHAT_HYPE_TOKENS = re.compile(
    r"\b(kekw|lul|lulw|omegalul|lmao+|lol+|icant|pog+|poggers|pogchamp|pogu|w+|clip|clipped|no way|holy|wtf|sheesh|ez)\b|"
    r"\?{3,}|!{3,}|😂|🤣|💀|🔥|😱", re.IGNORECASE)
CHAT_CLIP = re.compile(r"\bclip (it|that)\b|\bclip+ed\b|^clip$", re.IGNORECASE)


def _yt_runs_text(runs):
    out = []
    for run in runs or []:
        if "text" in run: out.append(run["text"])
        elif "emoji" in run:
            shortcuts = run["emoji"].get("shortcuts") or []
            out.append(shortcuts[0] if shortcuts else run["emoji"].get("emojiId", ""))
    return "".join(out)


def load_chat(path):
    """Reads a chat replay and returns [(seconds_into_video, text)], sorted.
    Supports TwitchDownloader JSON, yt-dlp live_chat.json and chat-downloader JSON."""
    with open(path, encoding="utf-8") as f: raw = f.read()
    messages = []
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, dict) and "comments" in data:                          # TwitchDownloader
        for c in data["comments"]:
            t = c.get("content_offset_seconds")
            body = (c.get("message") or {}).get("body", "")
            if t is not None: messages.append((float(t), body))
    elif isinstance(data, list):                                               # chat-downloader
        for c in data:
            if isinstance(c, dict) and "time_in_seconds" in c:
                messages.append((float(c["time_in_seconds"]), str(c.get("message", ""))))
    else:                                                                      # yt-dlp live_chat.json (one JSON object per line)
        for line in raw.splitlines():
            try: item = json.loads(line)
            except ValueError: continue
            replay = item.get("replayChatItemAction") or {}
            offset = replay.get("videoOffsetTimeMsec")
            if offset is None: continue
            for action in replay.get("actions", []):
                renderer = ((action.get("addChatItemAction") or {}).get("item") or {}).get("liveChatTextMessageRenderer")
                if renderer: messages.append((int(offset) / 1000.0, _yt_runs_text((renderer.get("message") or {}).get("runs"))))
    messages.sort(key=lambda m: m[0])
    return messages


def find_chat_file(video_path):
    """A chat replay saved next to the video with the same name, if there is one."""
    folder, name = os.path.split(video_path)
    stem = os.path.splitext(name)[0]
    for candidate in (f"{stem}.live_chat.json", f"{stem}.json", f"{stem}_chat.json", f"{stem}.chat.json"):
        path = os.path.join(folder, candidate)
        if os.path.isfile(path): return path
    return None


# --- COMBINING ---
@dataclass
class HypeData:
    reactions: np.ndarray = None      # [frames, len(REACTION_TAGS)] at YAMNET_HOP
    speech: list = None               # [SpeechLine]
    chat: list = None                 # [(seconds, text)]
    chat_path: str = ""


@dataclass
class Signals:
    """Per-moment signals on the loudness timeline (one value per hop)."""
    strength: np.ndarray
    spike_db: np.ndarray
    reactions: np.ndarray = None      # [n, tags] probabilities
    chat_ratio: np.ndarray = None
    chat_clip: np.ndarray = None
    speech_tags: dict = field(default_factory=dict)   # line index -> tags


def _moving_average(x, k):
    if k <= 1 or len(x) <= k: return x
    padded = np.pad(x, (k // 2, k - 1 - k // 2), mode="edge")
    return np.convolve(padded, np.ones(k) / k, mode="valid")


def _rolling_median(x, window, block):
    """Rolling median using block medians so it stays fast on long videos."""
    n = len(x) // block
    if n < 3: return np.full_like(x, np.median(x) if len(x) else 0.0)
    blocks = np.median(x[:n * block].reshape(n, block), axis=1)
    w = max(1, min(window, n))
    padded = np.pad(blocks, (w // 2, w - 1 - w // 2), mode="edge")
    rolled = np.median(np.lib.stride_tricks.sliding_window_view(padded, w), axis=1)
    out = np.repeat(rolled, block)
    return np.pad(out, (0, len(x) - len(out)), mode="edge")


def build_signals(smooth_db, base_db, hop, hype):
    n = len(smooth_db)
    times = np.arange(n) * hop
    spike = smooth_db - base_db
    loud = np.clip((spike - 3) / 10, 0, 1)
    # "evidence" is actual reactions (sounds, phrases, chat). Loudness only boosts it: a loud moment
    # with nobody reacting shouldn't count, which is what separates this from the loudness modes.
    strength = np.zeros(n)
    signals = Signals(strength=strength, spike_db=spike)

    if hype.reactions is not None and len(hype.reactions):
        centres = np.arange(len(hype.reactions)) * YAMNET_HOP + YAMNET_HOP
        on_grid = np.stack([np.interp(times, centres, hype.reactions[:, i], left=0, right=0) for i in range(len(REACTION_TAGS))], axis=1)
        signals.reactions = on_grid
        # Human reactions are the evidence; anything under 10% sure counts for nothing.
        people = [i for i, tag in enumerate(REACTION_TAGS) if tag != "BOOM"]
        weights = np.array([REACTION_GROUPS[REACTION_TAGS[i]][1] for i in people])
        sure = np.clip(on_grid[:, people] - 0.1, 0, None)
        strength += 1.5 * np.clip((sure * weights).max(axis=1) / 0.5, 0, 1)
        # Explosions / gunfire only boost a moment that already has a reaction (they're constant in shooters).
        loud = np.maximum(loud, REACTION_GROUPS["BOOM"][1] * on_grid[:, REACTION_TAGS.index("BOOM")])

    if hype.speech:
        rates = [len(l.text.split()) / max(l.end - l.start, 0.3) for l in hype.speech]
        median_rate = float(np.median(rates)) if rates else 2.5
        speech = np.zeros(n)
        for i, line in enumerate(hype.speech):
            tags = phrase_tags(line.text)
            if tags: signals.speech_tags[i] = tags
            value = max((PHRASE_WEIGHTS[t] for t in tags), default=0.0) + (0.2 if _is_excited(line, median_rate) else 0.0)
            if value:
                a, b = int(line.start / hop), int(line.end / hop) + 1
                speech[a:b] = np.maximum(speech[a:b], value)
        strength += np.clip(speech, 0, 1.5)

    if hype.chat:
        per_sec = int(round(1 / hop))
        seconds = int(n * hop) + 2
        counts, hyped, clips = np.zeros(seconds), np.zeros(seconds), np.zeros(seconds)
        for t, text in hype.chat:
            s = int(t - CHAT_DELAY)
            if 0 <= s < seconds:
                counts[s] += 1
                if CHAT_HYPE_TOKENS.search(text): hyped[s] += 1
                if CHAT_CLIP.search(text): clips[s] += 1
        rate = _moving_average(counts, 5)
        baseline = np.maximum(_rolling_median(rate, 60, 10), 0.2)
        ratio = rate / baseline
        hype_share = _moving_average(hyped, 5) / np.maximum(rate, 1e-6)
        chat_strength = np.clip((ratio - 1.5) / 4, 0, 1) + 0.5 * np.clip(hype_share, 0, 1) * (ratio > 1.2)
        signals.chat_ratio = np.repeat(ratio, per_sec)[:n]
        signals.chat_clip = np.repeat(_moving_average(clips, 8), per_sec)[:n]
        strength += 1.5 * np.repeat(chat_strength, per_sec)[:n]

    strength = strength * (1 + 0.5 * loud) + 0.15 * loud   # loudness alone tops out at 0.15, well under the bar
    # Hold each value for a moment instead of averaging, so a half-second scream isn't smoothed away.
    signals.strength = _moving_average(_rolling_max(strength, max(1, int(1.5 / hop))), max(1, int(0.5 / hop)))
    return signals


def _rolling_max(x, k):
    if k <= 1 or len(x) <= k: return x
    padded = np.pad(x.astype(np.float32), (k // 2, k - 1 - k // 2), mode="edge")
    return np.lib.stride_tricks.sliding_window_view(padded, k).max(axis=1)


def describe_moment(signals, hype, hop, peak, start, end):
    """Tags saying why a moment was picked, plus a title from what was said (or None)."""
    tags = []
    a, b = max(0, int((peak - 4) / hop)), int((peak + 6) / hop) + 1
    if signals.reactions is not None:
        best = signals.reactions[a:b].max(axis=0) if b > a else np.zeros(len(REACTION_TAGS))
        for i in np.argsort(best)[::-1][:2]:
            if best[i] >= REACTION_TAG_MIN and REACTION_TAGS[i] != "BOOM": tags.append(REACTION_TAGS[i])

    title = None
    if hype.speech:
        lines = [(i, l) for i, l in enumerate(hype.speech) if l.end >= peak - 6 and l.start <= peak + 6]
        phrases = [t for i, _ in lines for t in signals.speech_tags.get(i, [])]
        for tag in dict.fromkeys(phrases):
            if len(tags) < 4: tags.append(tag)
        # Name the clip after the line being said at the moment: a hype line if there is one, else the closest.
        distance = lambda il: 0 if il[1].start <= peak <= il[1].end else min(abs(il[1].start - peak), abs(il[1].end - peak))
        tagged = [il for il in lines if il[0] in signals.speech_tags and PHRASE_WEIGHTS[signals.speech_tags[il[0]][0]] >= 0.8]
        pick = sorted(tagged or lines, key=distance)
        if pick: title = clean_title(pick[0][1].text)

    if signals.chat_ratio is not None and b > a:
        ratio = float(signals.chat_ratio[a:b].max())
        if ratio >= 2: tags.append(f"CHAT x{ratio:.0f}")
        if signals.chat_clip is not None and signals.chat_clip[a:b].max() > 0: tags.append('CHAT: "CLIP IT"')
    return tags, title


def clean_title(text, limit=48):
    text = re.sub(r"\s+", " ", re.sub(r"[\[\(].*?[\]\)]", "", text)).strip(" .,-")
    if len(text) > limit: text = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "..."
    return text[:1].upper() + text[1:] if text else None


def snap_to_speech(start, end, lines, duration, reach=4.0):
    """Moves clip edges out to sentence boundaries so nobody gets cut off mid-word."""
    if not lines: return start, end
    for line in lines:
        if line.start < start < line.end and start - line.start <= reach: start = line.start
        if line.start < end < line.end and line.end - end <= reach: end = line.end
    return max(0.0, start - 0.2), min(duration, end + 0.3)
