# Bones Highlight Scout

Finds the best moments in a stream or gameplay recording, lets you review and trim them, and exports them as clips or vertical shorts. Everything runs on your own PC.

> **This is a beta.** Expect bugs, and please [report what you find](https://github.com/boneSk8er5-lab/BonesHighlightScout/issues).

## Download

Get the latest zip from the [Releases page](https://github.com/boneSk8er5-lab/BonesHighlightScout/releases).

1. Unzip the whole folder. It won't run from inside the zip.
2. Double-click `BonesHighlightScout.exe`.
3. Drag a video onto the window and press **SCAN**.

It runs on 64-bit Windows 10 or 11. There's nothing to install and it works without internet. Everything stays in its own folder, so it can run from a USB drive, and deleting the folder removes it.

The beta isn't code-signed, so Windows may show "Windows protected your PC". Click **More info**, then **Run anyway**.

## What it does

### Finds moments worth clipping

- **Reaction sounds:** an AI model listens for laughter, screaming, shouting, cheering and gasps.
- **Speech:** a speech model transcribes the video and looks for phrases like "no way", "oh my god" and "clip it". It also names clips after what was said and moves clip edges so nobody gets cut off mid-sentence.
- **Chat replays (optional):** finds message floods, emote spam and "clip it" in a Twitch or YouTube chat replay.
- **Loudness modes:** "Sudden spikes" and "Loud moments" are there if you just want loud parts.

Each clip is tagged with what was detected, such as `LAUGHTER` or `"OH MY GOD"`. Nothing here predicts views. It points you at reactions, and you decide what's good.

### Review

- Preview clips with previous/next, a scrubber and volume control
- Rename clips and edit their start and end times
- See every clip on a full-length waveform, and click it to preview
- Clips, names and trims are remembered for each video
- Keyboard shortcuts for the common actions

### Vertical shorts

A Twitch-style 9:16 editor with a live preview that plays your clip:

- Boxes on the video choose what to show, with corner and side handles for exact cropping
- Boxes on the preview choose where it goes: webcam top, middle, bottom, or anywhere you drag it
- Blurred background and a webcam drop shadow, with sliders
- Every clip can have its own layout, or you can use one layout on all of them

### Export

| Export type | Result |
|---|---|
| Accurate | Re-encoded, cuts exactly where you set |
| Fast | Copies the video without re-encoding, but can start a second or two early |
| Vertical 9:16 (blur fill) | 1080×1920, the whole frame over a blurred background |
| Vertical 9:16 (center crop) | 1080×1920, cropped to the middle of the frame |
| Vertical short (cam + game) | 1080×1920, using your webcam and gameplay layout |

Your original video is never changed. Clips are saved as new files, by default in a folder next to the video.

## Chat replays

Save the replay with one of these tools and load it with **LOAD CHAT REPLAY...**. If it's next to the video with a matching name (`video.json` or `video.live_chat.json`), it loads automatically.

- **Twitch:** JSON from [TwitchDownloader](https://github.com/lay295/TwitchDownloader)
- **YouTube:** `live_chat.json` from [yt-dlp](https://github.com/yt-dlp/yt-dlp)

## Reporting problems

[Open an issue](https://github.com/boneSk8er5-lab/BonesHighlightScout/issues) saying what you did, what you expected and what happened. Screenshots help.

If something went wrong, attach `data\error.log` from the app's folder. It may include file names and folder paths from your PC, and issues are public, so check it first.

## Running from source

You'll need Windows, Python (developed on 3.10) and [ffmpeg](https://ffmpeg.org/download.html) on your PATH.

```bash
python -m pip install -r BonesHighlightScout/requirements.txt
```

```bash
python BonesHighlightScout/BonesHighlightScout.py
```

From source, the speech model (about 145 MB) downloads the first time you scan with speech on, and settings are saved in `%APPDATA%\BonesHighlightScout`.

| To do this | Run |
|---|---|
| Build a local `.exe` | `build_exe.bat` |
| Build the portable zip | `python tools/make_release.py` |
| Test a build from start to finish | `BonesHighlightScout.exe --self-test video.mp4 result.json` |

`make_release.py` needs PyInstaller, and the speech model already downloaded by running a scan with speech once.

## License

The app's own code is under the [MIT License](LICENSE).

It uses other open-source software and AI models that keep their own licenses, including FFmpeg (GPLv3 or later in the release zip), YAMNet and Whisper. See [the third-party notices](release_files/THIRD-PARTY-NOTICES.txt).
