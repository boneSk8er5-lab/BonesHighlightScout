# PyInstaller recipe for Bones Highlight Scout. Run build_exe.bat rather than calling this directly.
# Output: dist\BonesHighlightScout\BonesHighlightScout.exe (a folder build, like Bones Caption Studio).
from PyInstaller.utils.hooks import collect_data_files

app = "BonesHighlightScout"
icon = f"{app}/HighlightScout.ico"

datas = [(icon, "."), (f"{app}/HighlightScout.png", ".")]
datas += collect_data_files("ttkbootstrap")
datas += collect_data_files("tkinterdnd2")   # the tkdnd library that drag-and-drop loads at runtime

a = Analysis(
    [f"{app}/{app}.py"],
    datas=datas,
    hiddenimports=["tkinterdnd2"],
    # The waveform is drawn with matplotlib's Tk backend; without this PyInstaller guesses Qt and bundles it.
    hooksconfig={"matplotlib": {"backends": ["TkAgg"]}},
    # Installed in this Python for other projects but not used here; keeps the build small.
    excludes=["scipy", "librosa", "moviepy", "torch", "pandas", "IPython", "numba", "sklearn",
              "PyQt5", "PyQt6", "PySide6", "shiboken6"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=app, icon=icon, console=False)
coll = COLLECT(exe, a.binaries, a.datas, name=app)
