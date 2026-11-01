# PyInstaller recipe for Bones Highlight Scout. Run build_exe.bat rather than calling this directly.
# Output: dist\BonesHighlightScout\BonesHighlightScout.exe (a folder build, like Bones Caption Studio).
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

app = "BonesHighlightScout"
icon = f"{app}/HighlightScout.ico"

datas = [(icon, "."), (f"{app}/HighlightScout.png", ".")]
datas += [(f"{app}/models/yamnet.onnx", "models"), (f"{app}/models/yamnet_class_map.csv", "models"),
          (f"{app}/models/yamnet_LICENSE.txt", "models")]   # reaction-sound model (Hype mode)
datas += collect_data_files("ttkbootstrap")
datas += collect_data_files("tkinterdnd2")   # the tkdnd library that drag-and-drop loads at runtime
hiddenimports = ["tkinterdnd2", "hype"]
# Hype mode: onnxruntime runs the sound model; faster-whisper + ctranslate2 do speech. Only their DLLs and
# data files are collected; collecting every submodule drags in optional converters that import PyTorch.
binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")
datas += collect_data_files("faster_whisper")   # includes the voice-activity model it uses to skip silence

a = Analysis(
    [f"{app}/{app}.py"],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    # The waveform is drawn with matplotlib's Tk backend; without this PyInstaller guesses Qt and bundles it.
    hooksconfig={"matplotlib": {"backends": ["TkAgg"]}},
    # Installed in this Python for other projects but not used here; keeps the build small.
    excludes=["scipy", "librosa", "moviepy", "torch", "torchaudio", "torchvision", "transformers", "tensorflow", "onnx",
              "pandas", "IPython", "numba", "sklearn", "PyQt5", "PyQt6", "PySide6", "shiboken6"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=app, icon=icon, console=False)
coll = COLLECT(exe, a.binaries, a.datas, name=app)
