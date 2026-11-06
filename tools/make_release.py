"""Builds the portable beta zip: python tools/make_release.py

Makes release/BonesHighlightScout-<version>-win64.zip with everything inside: the app, ffmpeg.exe, the speech
model, the README and the license notices. Testers just unzip and run it. Your own dist folder isn't touched,
so this works even while the app is open.
"""
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RELEASE = os.path.join(ROOT, "release")
SOURCE = os.path.join(ROOT, "BonesHighlightScout", "BonesHighlightScout.py")
FOLDER_NAME = "Bones Highlight Scout Beta"
WHISPER_CACHE = os.path.join(os.environ["APPDATA"], "BonesHighlightScout", "models", "models--Systran--faster-whisper-base", "snapshots", "*")


def fail(message):
    sys.exit(f"\nCan't build the release: {message}")


def main():
    with open(SOURCE, encoding="utf-8") as f:
        version = re.search(r'^APP_VERSION = "([^"]+)"', f.read(), re.M).group(1)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg: fail("ffmpeg isn't on PATH.")
    models = glob.glob(WHISPER_CACHE)
    if not models: fail("the speech model isn't downloaded yet. Run a Hype scan with speech once in the app first.")
    version_output = subprocess.run([ffmpeg, "-hide_banner", "-version"], capture_output=True, text=True).stdout
    configuration = next(line for line in version_output.splitlines() if line.startswith("configuration:"))
    if "--enable-nonfree" in configuration: fail("this ffmpeg build is 'nonfree', which can't legally be shared.")

    print(f"Building {version}...")
    shutil.rmtree(RELEASE, ignore_errors=True)
    build = subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                            "--distpath", os.path.join(RELEASE, "app"), "--workpath", os.path.join(RELEASE, "build"),
                            os.path.join(ROOT, "BonesHighlightScout.spec")], cwd=ROOT)
    if build.returncode != 0: fail("PyInstaller failed; see above.")
    stage = os.path.join(RELEASE, FOLDER_NAME)
    os.rename(os.path.join(RELEASE, "app", "BonesHighlightScout"), stage)

    print("Adding ffmpeg, the speech model and the documents...")
    os.makedirs(os.path.join(stage, "ffmpeg"))
    shutil.copy2(ffmpeg, os.path.join(stage, "ffmpeg", "ffmpeg.exe"))
    model_out = os.path.join(stage, "_internal", "models", "whisper-base")
    os.makedirs(model_out)
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        shutil.copy2(os.path.join(models[0], name), os.path.join(model_out, name))   # copy2 follows the cache's links
    with open(os.path.join(stage, "portable.txt"), "w") as f:
        f.write("This file tells Bones Highlight Scout to keep its settings in the data folder here.\n")
    shutil.copy2(os.path.join(ROOT, "release_files", "README.txt"), stage)
    with open(os.path.join(ROOT, "release_files", "THIRD-PARTY-NOTICES.txt"), encoding="utf-8") as f: notices = f.read()
    version_line = version_output.splitlines()[0]
    commit = re.search(r"git-([0-9a-f]+)", version_line)
    notices = notices.replace("FFMPEG_VERSION_LINE", version_line).replace("FFMPEG_COMMIT", commit.group(1) if commit else "see the version line")
    with open(os.path.join(stage, "THIRD-PARTY-NOTICES.txt"), "w", encoding="utf-8") as f: f.write(notices)

    print("Zipping...")
    archive = shutil.make_archive(os.path.join(RELEASE, f"BonesHighlightScout-{version}-win64"), "zip", RELEASE, FOLDER_NAME)
    shutil.rmtree(os.path.join(RELEASE, "app"), ignore_errors=True)
    shutil.rmtree(os.path.join(RELEASE, "build"), ignore_errors=True)
    digest = hashlib.sha256()
    with open(archive, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""): digest.update(block)
    folder_mb = sum(os.path.getsize(os.path.join(d, name)) for d, _, names in os.walk(stage) for name in names) / 1e6
    print(f"\nDone: {archive}\n  zip {os.path.getsize(archive) / 1e6:.0f} MB, unzipped {folder_mb:.0f} MB\n  SHA-256 {digest.hexdigest()}")


if __name__ == "__main__":
    main()
